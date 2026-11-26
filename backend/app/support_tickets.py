"""Support tickets for overdue orders, separate from arbitration auto replies."""

import asyncio
import html
import re
from datetime import timedelta
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import delete, select

from .db import Session
from .events import bus
from .funpay import FunPayError, soup_of
from .integrated_config import plain_config
from .models import Account, Activity, Order, Proxy, SupportRequest, SupportRequestOrder, utcnow
from .security import vault
from .services import accounts, proxy_url, telegram

support_lock = asyncio.Lock()


async def candidates(db, config):
    query = select(Order).where(
        Order.status == "paid",
        Order.account_id == config.get("account_id"),
        Order.created_at <= utcnow() - timedelta(hours=config["min_age_hours"]),
        Order.date_source == "observed",
        ~Order.id.in_(select(SupportRequestOrder.order_id)),
    )
    if config.get("only_delivered", True):
        query = query.where(Order.auto_delivered.is_(True))
    return (await db.scalars(query.order_by(Order.created_at).limit(100))).all()


def safe_url(url):
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"funpay.com", "support.funpay.com"}
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Поддержка FunPay вернула неожиданный адрес")
    return url


async def get_form(http, cookie_present):
    url = (
        "https://support.funpay.com/tickets/new/1"
        if cookie_present
        else "https://funpay.com/support/sso?return_to=%2Ftickets%2Fnew%2F1"
    )
    for _ in range(6):
        response = await http.get(safe_url(url))
        if response.is_redirect:
            url = urljoin(url, response.headers.get("location", ""))
            continue
        response.raise_for_status()
        soup = await asyncio.to_thread(soup_of, response.text)
        csrf = soup.select_one('input[name="ticket[_token]"]')
        form = csrf.find_parent("form") if csrf else None
        if not form:
            raise ValueError("Войдите в поддержку FunPay и обновите PHPSESSID в настройках плагина")
        action = safe_url(urljoin(url, form.get("action") or "/tickets/create/1"))
        if urlparse(action).hostname != "support.funpay.com":
            raise ValueError("Неожиданный адрес отправки обращения")
        fields = {}
        for element in form.select("input[name], textarea[name], select[name]"):
            if element.get("type") in {"file", "submit"}:
                continue
            if element.name == "select":
                option = element.select_one("option[selected]") or element.select_one("option")
                value = option.get("value", "") if option else ""
            else:
                value = element.get("value", "") if element.name == "input" else element.get_text()
            fields[element["name"]] = str(value)
        if not fields.get("ticket[_token]"):
            raise ValueError("Поддержка не вернула CSRF-токен")
        return action, fields
    raise ValueError("Слишком много перенаправлений поддержки")


async def send(order_ids, *, automatic=False):
    async with support_lock:
        config = await plain_config("support-tickets")
        async with Session() as db:
            orders = (await db.scalars(select(Order).where(Order.id.in_(set(order_ids))))).all()
            if len(orders) != len(set(order_ids)) or not orders:
                raise ValueError("Часть заказов не найдена")
            account_ids = {order.account_id for order in orders}
            if len(account_ids) != 1 or next(iter(account_ids)) != config.get("account_id"):
                raise ValueError("Выберите заказы настроенного аккаунта")
            if any(order.status != "paid" for order in orders):
                raise ValueError("В обращение можно добавить только оплаченные заказы")
            if await db.scalar(
                select(SupportRequestOrder.id)
                .where(SupportRequestOrder.order_id.in_(order_ids))
                .limit(1)
            ):
                raise ValueError("По одному из заказов уже создано обращение; проверьте историю")
            account = await db.get(Account, orders[0].account_id)
            if not account or not account.is_active:
                raise ValueError("Аккаунт FunPay отключён. Включите его перед отправкой обращения.")
            client = await accounts.client(account.id)
            for order in orders:
                detail = await client.order_details(order.funpay_order_id)
                if detail.get("status") != "paid":
                    raise ValueError("Один из заказов уже закрыт на FunPay")
            request = SupportRequest(account_id=account.id, state="preparing")
            db.add(request)
            await db.flush()
            for order in orders:
                db.add(SupportRequestOrder(request_id=request.id, order_id=order.id))
            await db.commit()
            cookies = httpx.Cookies()
            cookies.set("golden_key", vault.decrypt(account.golden_key), domain="funpay.com")
            if config.get("phpsessid"):
                cookies.set("PHPSESSID", config["phpsessid"], domain="support.funpay.com")
            proxy = await db.get(Proxy, account.proxy_id) if account.proxy_id else None
            try:
                async with httpx.AsyncClient(
                    timeout=20,
                    follow_redirects=False,
                    trust_env=False,
                    cookies=cookies,
                    proxy=proxy_url(proxy) if proxy else None,
                    headers={"User-Agent": account.user_agent},
                ) as http:
                    action, fields = await get_form(http, bool(config.get("phpsessid")))
                    from .integrated_schemas import SupportConfig
                    from .message_templates import render

                    legacy = (
                        config["message"] if config["message"] != SupportConfig().message else None
                    )
                    message = await render(
                        "support-tickets",
                        "support_auto" if automatic else "support_manual",
                        {
                            "order_id": ", ".join("#" + order.funpay_order_id for order in orders),
                            "order_ids": ", ".join("#" + order.funpay_order_id for order in orders),
                        },
                        db=db,
                        legacy=legacy,
                    )
                    fields.update(
                        {
                            "ticket[fields][1]": account.username or "",
                            "ticket[fields][2]": orders[0].funpay_order_id,
                            "ticket[fields][3]": "2",
                            "ticket[fields][5]": "201",
                            "ticket[comment][body_html]": f'<p dir="auto">{html.escape(message)}</p>',
                            "ticket[comment][attachments]": "",
                            "ticket[submit]": "Отправить",
                        }
                    )
                    request.state = "submitting"
                    await db.commit()
                    response = await http.post(action, data=fields)
                    location = urljoin(action, response.headers.get("location", ""))
                    match = re.fullmatch(r"/tickets/(\d+)/?", urlparse(location).path)
                    if (
                        not response.is_redirect
                        or not match
                        or urlparse(location).hostname != "support.funpay.com"
                    ):
                        raise ValueError(
                            "Поддержка не подтвердила создание обращения. Проверьте список тикетов перед повтором."
                        )
                    request.remote_id, request.state = match[1], "sent"
                    db.add(
                        Activity(
                            channel="tickets",
                            text=f"Создано обращение #{match[1]}",
                            account_id=account.id,
                            source_plugin="support-tickets",
                        )
                    )
                    await db.commit()
                return request.id
            except Exception as error:
                request.last_error = (
                    str(error)[:500]
                    if isinstance(error, ValueError)
                    else "Сетевая ошибка поддержки FunPay"
                )
                request.state = "uncertain" if request.state == "submitting" else "error"
                await db.commit()
                await bus.log(
                    "tickets",
                    f"Обращение #{request.id}: {request.last_error}",
                    account.id,
                    "error",
                    source_plugin="support-tickets",
                )
                raise ValueError(request.last_error) from None


async def retry(ident):
    async with Session() as db:
        request = await db.get(SupportRequest, ident)
        if not request or request.state != "error":
            raise ValueError("Повтор разрешён только для запроса, который ещё не отправлялся")
        ids = (
            await db.scalars(
                select(SupportRequestOrder.order_id).where(SupportRequestOrder.request_id == ident)
            )
        ).all()
        await db.execute(delete(SupportRequestOrder).where(SupportRequestOrder.request_id == ident))
        request.state = "superseded"
        await db.commit()
    return await send(ids)


async def tick():
    config = await plain_config("support-tickets")
    if not config.get("enabled") or not config.get("account_id"):
        return
    async with Session() as db:
        latest = await db.scalar(
            select(SupportRequest)
            .where(SupportRequest.account_id == config["account_id"])
            .order_by(SupportRequest.id.desc())
            .limit(1)
        )
        cutoff = utcnow() - timedelta(minutes=config["interval_minutes"])
        if latest and latest.created_at.replace(tzinfo=utcnow().tzinfo) > cutoff:
            return
        orders = (await candidates(db, config))[: config["batch_limit"]]
    if orders:
        try:
            await send([order.id for order in orders], automatic=True)
        except (ValueError, FunPayError):
            await telegram.notify(
                "errors",
                "AutoTicket: не удалось создать обращение в поддержку FunPay. Проверьте настройки и журнал.",
            )


async def recover():
    async with Session() as db:
        rows = (
            await db.scalars(
                select(SupportRequest).where(SupportRequest.state.in_(["preparing", "submitting"]))
            )
        ).all()
        for row in rows:
            row.state = "error" if row.state == "preparing" else "uncertain"
            row.last_error = (
                "Перезапуск во время создания обращения; проверьте поддержку перед повтором"
            )
        await db.commit()
