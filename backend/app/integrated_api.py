"""Authenticated UI API for the adapted plugin suite."""

import asyncio
import hashlib
import re
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from . import integrated_jobs, steam_rentals, support_tickets
from .db import Session
from .events import bus
from .integrated_config import (
    configured,
    packed,
    plain_config,
    public_config,
    save_config,
    unpacked,
)
from .integrated_schemas import (
    CONFIG_MODELS,
    BindingCreate,
    RentalAccountCreate,
    RentalStateChange,
    SupportSend,
    TargetInput,
)
from .models import (
    Account,
    AppRouteMapping,
    Lot,
    Order,
    PluginBinding,
    PluginJob,
    RentalAccount,
    RentalAccountBinding,
    RentalLease,
    SupportRequest,
    SupportRequestOrder,
    utcnow,
)
from .plugin_providers import points_request, smm_request, smm_services
from .security import require_auth

router = APIRouter(prefix="/api/plugins/integrated", dependencies=[Depends(require_auth)])


def checked(kind):
    if kind not in CONFIG_MODELS:
        raise HTTPException(404, "Плагин не найден")


def summary(row, fields):
    return {key: getattr(row, key) for key in fields.split()}


@router.get("/{kind}")
async def status(kind: str):
    checked(kind)
    async with Session() as db:
        bindings = (
            await db.execute(select(PluginBinding, Lot).join(Lot).where(PluginBinding.kind == kind))
        ).all()
        jobs = (
            await db.execute(
                select(PluginJob, Order)
                .join(Order, PluginJob.order_id == Order.id)
                .where(PluginJob.kind == kind)
                .order_by(PluginJob.id.desc())
                .limit(100)
            )
        ).all()
        data = {
            "settings": await public_config(kind),
            "configured": await configured(kind),
            "bindings": [
                {
                    **summary(
                        binding, "id lot_id quantity provider_id service_id is_active is_extension"
                    ),
                    "title": lot.title,
                    "funpay_item_id": lot.funpay_lot_id,
                    "account_id": lot.account_id,
                }
                for binding, lot in bindings
            ],
            "jobs": [
                {
                    **summary(
                        job, "id state quantity target remote_id last_error created_at updated_at"
                    ),
                    "order_number": order.funpay_order_id,
                    "buyer": order.buyer_name,
                    "review_image": unpacked(job.payload).get("review_image")
                    if job.state == "waiting_review"
                    else None,
                    "provider_status": unpacked(job.payload).get("provider_status"),
                    "remains": unpacked(job.payload).get("remains"),
                }
                for job, order in jobs
            ],
        }
        if kind == "steam-rent":
            rows = (
                await db.scalars(
                    select(RentalAccount)
                    .join(PluginBinding)
                    .where(PluginBinding.kind == kind)
                    .order_by(RentalAccount.id)
                )
            ).all()
            leases = (
                await db.execute(
                    select(RentalLease, RentalAccount, Order)
                    .join(RentalAccount)
                    .join(PluginJob, RentalLease.job_id == PluginJob.id)
                    .join(Order, PluginJob.order_id == Order.id)
                    .order_by(RentalLease.id.desc())
                    .limit(100)
                )
            ).all()
            data["inventory"] = [
                summary(row, "id binding_id label state last_error") for row in rows
            ]
            data["leases"] = [
                {
                    **summary(lease, "id state expires_at warned_at"),
                    "label": account.label,
                    "buyer": order.buyer_name,
                    "order_number": order.funpay_order_id,
                }
                for lease, account, order in leases
            ]
        if kind == "support-tickets":
            config = await plain_config(kind)
            rows = (
                await db.scalars(
                    select(SupportRequest).order_by(SupportRequest.id.desc()).limit(100)
                )
            ).all()
            links = (
                await db.execute(
                    select(SupportRequestOrder.request_id, Order.funpay_order_id)
                    .join(Order)
                    .where(SupportRequestOrder.request_id.in_([row.id for row in rows]))
                )
            ).all()
            data["requests"] = [
                {
                    **summary(row, "id state remote_id last_error created_at"),
                    "orders": [order_id for request_id, order_id in links if request_id == row.id],
                }
                for row in rows
            ]
            data["candidates"] = [
                summary(row, "id funpay_order_id buyer_name description created_at auto_delivered")
                for row in await support_tickets.candidates(db, config)
            ]
        return data


@router.put("/{kind}/settings")
async def settings(kind: str, payload: dict):
    checked(kind)
    try:
        return await save_config(kind, payload)
    except ValidationError as error:
        raise HTTPException(
            422, "; ".join(item["msg"] for item in error.errors(include_input=False))
        ) from None
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.post("/{kind}/lots/resolve")
async def resolve_lot(kind: str, payload: dict):
    """Разбирает вставленный ID/ссылку лота и подтягивает данные с FunPay."""
    checked(kind)
    query = str(payload.get("query") or "").strip()
    match = re.search(
        r"(?:offer(?:Edit)?/|offer(?:_id|\?id)?=|^)(\d+)(?:\D|$)", query
    )
    if not match:
        raise HTTPException(
            422,
            "Вставьте ID лота (например 81769276) или ссылку с offer=ID",
        )
    funpay_id = match.group(1)
    async with Session() as db:
        lot = await db.scalar(select(Lot).where(Lot.funpay_lot_id == funpay_id))
        problem = ""
        if not lot:
            # Лота нет в локальном списке — подтягиваем его с аккаунта FunPay.
            from .funpay import FunPayError
            from .lot_editor import load_form
            from .services import accounts

            active = (
                await db.scalars(select(Account.id).where(Account.is_active.is_(True)))
            ).all()
            for account_id in active:
                try:
                    await accounts.sync_lots(account_id)
                except FunPayError as error:
                    problem = str(error)
            lot = await db.scalar(select(Lot).where(Lot.funpay_lot_id == funpay_id))
        if not lot:
            # Последний шанс: скрытые/не попавшие в список лоты читаем
            # напрямую из формы редактирования FunPay.
            from .funpay import FunPayError
            from .lot_editor import load_form
            from .services import accounts

            active = (
                await db.scalars(select(Account.id).where(Account.is_active.is_(True)))
            ).all()
            for account_id in active:
                try:
                    client = await accounts.client(account_id)
                    metadata, fields = await load_form(client, offer_id=funpay_id)
                except FunPayError as error:
                    problem = str(error)
                    continue
                summary = next(
                    (
                        item["value"]
                        for item in metadata["controls"]
                        if item["name"] == "summary"
                    ),
                    "",
                )
                price = next(
                    (
                        item["value"]
                        for item in metadata["controls"]
                        if item["name"] == "price"
                    ),
                    "",
                )
                active_field = next(
                    (
                        item["value"]
                        for item in metadata["controls"]
                        if item["name"] == "active"
                    ),
                    "",
                )
                if not summary:
                    problem = "FunPay вернул форму без данных лота"
                    continue
                try:
                    lot_price = Decimal(str(price).replace(",", ".") or 0)
                except InvalidOperation:
                    lot_price = Decimal(0)
                lot = Lot(
                    account_id=account_id,
                    funpay_lot_id=funpay_id,
                    node_id=int(metadata.get("node_id") or 0),
                    game_id=0,
                    category_title=f"Категория {metadata.get('node_id') or '?'}",
                    title=summary,
                    price=lot_price,
                    amount=None,
                    is_active=str(active_field).strip().lower()
                    not in {"", "0", "false", "off"},
                )
                db.add(lot)
                await db.commit()
                await db.refresh(lot)
                break
        if not lot:
            detail = (
                f"Лот #{funpay_id} не найден на вашем аккаунте FunPay"
                if not problem
                else f"Лот #{funpay_id} не найден: {problem}"
            )
            raise HTTPException(404, detail)
        account = await db.get(Account, lot.account_id)
        return {
            "lot_id": lot.id,
            "funpay_lot_id": lot.funpay_lot_id,
            "title": lot.title,
            "price": str(lot.price),
            "category_title": lot.category_title,
            "amount": lot.amount,
            "is_active": lot.is_active,
            "account_id": lot.account_id,
            "account": account.username if account else "",
        }


@router.post("/{kind}/bindings", status_code=201)
async def add_binding(kind: str, payload: BindingCreate):
    checked(kind)
    if kind == "support-tickets":
        raise HTTPException(422, "Для поддержки привязка лотов не нужна")
    async with Session() as db:
        lot = await db.get(Lot, payload.lot_id)
        if not lot:
            raise HTTPException(404, "Лот не найден")
        if await db.scalar(
            select(AppRouteMapping.id).where(
                AppRouteMapping.account_id == lot.account_id,
                AppRouteMapping.funpay_item_id == lot.funpay_lot_id,
            )
        ):
            raise HTTPException(409, "Этот лот уже связан с AppRoute")
        config = await plain_config(kind)
        if kind == "auto-smm" and (
            not payload.service_id.isdigit()
            or not any(row["id"] == payload.provider_id for row in config["providers"])
        ):
            raise HTTPException(422, "Выберите поставщика и числовой ID услуги SMM")
        if (
            kind == "auto-stars"
            and not config["min_stars"] <= payload.quantity <= config["max_stars"]
        ):
            raise HTTPException(422, "Количество Stars выходит за установленные лимиты")
        if kind == "steam-rent" and payload.quantity > 8760:
            raise HTTPException(422, "Не более 8760 часов за единицу товара")
        if payload.is_extension and kind != "steam-rent":
            raise HTTPException(422, "Продление доступно только для аренды")
        if payload.is_extension and payload.service_id:
            base = (
                await db.get(PluginBinding, int(payload.service_id))
                if payload.service_id.isdigit()
                else None
            )
            if not base or base.kind != kind or base.is_extension:
                raise HTTPException(422, "Выберите основной лот аренды")
        binding = PluginBinding(kind=kind, **payload.model_dump())
        db.add(binding)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, "Этот лот уже привязан к плагину") from None
        if kind == "steam-rent" and not payload.is_extension:
            # Настройки применяются сразу после ввода: аккаунты пула без
            # привязки автоматически связываются с новым основным лотом.
            orphans = (
                await db.scalars(
                    select(RentalAccount.id).where(RentalAccount.binding_id.is_(None))
                )
            ).all()
            for orphan in orphans:
                await db.execute(
                    update(RentalAccount)
                    .where(RentalAccount.id == orphan)
                    .values(binding_id=binding.id)
                )
                db.add(RentalAccountBinding(account_id=orphan, binding_id=binding.id))
            if orphans:
                await db.commit()
        return {"id": binding.id}


@router.delete("/{kind}/bindings/{ident}")
async def delete_binding(kind: str, ident: int):
    checked(kind)
    async with Session() as db:
        binding = await db.get(PluginBinding, ident)
        if not binding or binding.kind != kind:
            raise HTTPException(404, "Связь не найдена")
        if await db.scalar(
            select(RentalAccount.id).where(RentalAccount.binding_id == ident).limit(1)
        ):
            raise HTTPException(409, "Сначала переместите или удалите аккаунты из этого лота")
        await db.delete(binding)
        await db.commit()
        return {"ok": True}


@router.put("/{kind}/bindings/{ident}")
async def edit_binding(kind: str, ident: int, payload: BindingCreate):
    checked(kind)
    async with Session() as db:
        row = await db.get(PluginBinding, ident)
        if not row or row.kind != kind or row.lot_id != payload.lot_id:
            raise HTTPException(422, "Неверная привязка лота")
        config = await plain_config(kind)
        if kind == "auto-smm" and (
            not payload.service_id.isdigit()
            or not any(provider["id"] == payload.provider_id for provider in config["providers"])
        ):
            raise HTTPException(422, "Выберите поставщика и ID услуги")
        if (
            kind == "auto-stars"
            and not config["min_stars"] <= payload.quantity <= config["max_stars"]
        ):
            raise HTTPException(422, "Количество Stars выходит за установленные лимиты")
        if kind == "steam-rent" and payload.quantity > 8760:
            raise HTTPException(422, "Не более 8760 часов за единицу товара")
        if payload.is_extension and kind != "steam-rent":
            raise HTTPException(422, "Продление доступно только для аренды")
        if payload.is_extension and await db.scalar(
            select(RentalAccount.id).where(RentalAccount.binding_id == ident).limit(1)
        ):
            raise HTTPException(
                409, "К этому лоту привязаны аккаунты. Создайте отдельный лот продления."
            )
        if payload.is_extension and payload.service_id:
            base = (
                await db.get(PluginBinding, int(payload.service_id))
                if payload.service_id.isdigit()
                else None
            )
            if not base or base.kind != kind or base.is_extension or base.id == ident:
                raise HTTPException(422, "Выберите основной лот аренды")
        for key, value in payload.model_dump().items():
            setattr(row, key, value)
        await db.commit()
        return {"ok": True}


@router.post("/{kind}/test")
async def test_connection(kind: str):
    checked(kind)
    config = await plain_config(kind)
    try:
        if kind == "auto-smm":
            values = []
            for provider in config["providers"]:
                balance = await smm_request(provider, "balance")
                values.append(
                    {
                        "name": provider["name"],
                        "balance": str(balance.get("balance", "—")),
                        "currency": balance.get("currency", ""),
                    }
                )
            return {"balances": values}
        if kind == "steam-points":
            balance = await points_request("balance", config)
            return {
                "balances": [
                    {
                        "name": "Steam Points",
                        "balance": str(balance.get("balance", "—")),
                        "currency": balance.get("currency", ""),
                    }
                ]
            }
        if kind == "auto-stars":
            from ton_core import Address, NetworkGlobalID
            from tonutils.clients import TonapiClient
            from tonutils.contracts import WalletV4R2

            from .integrated_config import pool

            client = TonapiClient(network=NetworkGlobalID.MAINNET, api_key=config["ton_api_key"])
            try:
                try:
                    wallet, _, _, _ = await asyncio.to_thread(
                        WalletV4R2.from_mnemonic, client, config["mnemonic"]
                    )
                except Exception:
                    raise ValueError(
                        "Не удалось открыть TON-кошелёк. Проверьте seed-фразу."
                    ) from None
                if wallet.address != Address(config["wallet_address"]):
                    raise ValueError("Seed-фраза не соответствует адресу кошелька")
            finally:
                await client.close()
            response = await pool.request(
                "GET",
                f"https://tonapi.io/v2/accounts/{wallet.address.to_str(is_user_friendly=False)}",
                headers={"Authorization": f"Bearer {config['ton_api_key']}"},
            )
            response.raise_for_status()
            return {
                "balances": [
                    {
                        "name": "TON",
                        "balance": str(int(response.json()["balance"]) / 10**9),
                        "currency": "TON",
                    }
                ]
            }
        if kind == "support-tickets":
            import httpx

            from .models import Account
            from .security import vault

            async with Session() as db:
                account = (
                    await db.get(Account, config["account_id"]) if config["account_id"] else None
                )
                if not account:
                    raise ValueError("Выберите аккаунт FunPay")
                cookies = httpx.Cookies()
                cookies.set("golden_key", vault.decrypt(account.golden_key), domain="funpay.com")
                if config.get("phpsessid"):
                    cookies.set("PHPSESSID", config["phpsessid"], domain="support.funpay.com")
                async with httpx.AsyncClient(
                    timeout=20, follow_redirects=False, cookies=cookies, trust_env=False
                ) as client:
                    await support_tickets.get_form(client, bool(config.get("phpsessid")))
            return {"message": "Поддержка FunPay доступна. Тикет не отправлялся."}
        return {"message": "Для проверки входа выберите аккаунт в инвентаре"}
    except Exception as error:
        raise HTTPException(
            422,
            str(error)
            if isinstance(error, ValueError)
            else "Не удалось проверить подключение. Проверьте ключи и доступность сервиса.",
        ) from None


@router.get("/auto-smm/services/{provider_id}")
async def list_services(provider_id: str):
    config = await plain_config("auto-smm")
    provider = next((row for row in config["providers"] if row["id"] == provider_id), None)
    if not provider:
        raise HTTPException(404, "Поставщик не найден")
    try:
        return await smm_services(provider)
    except Exception:
        raise HTTPException(502, "Не удалось получить список услуг поставщика") from None


async def get_job(ident, kind, db):
    job = await db.get(PluginJob, ident)
    if not job or job.kind != kind:
        raise HTTPException(404, "Заказ плагина не найден")
    return job


@router.put("/{kind}/jobs/{ident}/target")
async def set_target(kind: str, ident: int, payload: TargetInput):
    async with Session() as db:
        await get_job(ident, kind, db)
    try:
        return {"state": await integrated_jobs.update_target(ident, payload.target)}
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.post("/{kind}/jobs/{ident}/{action}")
async def job_action(kind: str, ident: int, action: str):
    if action == "confirm":
        return await perform_job_action(kind, ident, action)
    async with integrated_jobs.job_locks.setdefault(ident, asyncio.Lock()):
        return await perform_job_action(kind, ident, action)


async def perform_job_action(kind: str, ident: int, action: str):
    async with Session() as db:
        job = await get_job(ident, kind, db)
        if action == "confirm":
            try:
                await integrated_jobs.confirm_target(ident)
            except ValueError as error:
                raise HTTPException(422, str(error)) from None
        elif action == "approve" and job.state == "waiting_review":
            from .rental_verification import decide
            await decide(ident, "approve")
        elif action == "reject" and job.state == "waiting_review":
            from .rental_verification import decide
            await decide(ident, "reject")
        elif action == "resolve" and job.state in {"attention", "uncertain"}:
            order = await db.get(Order, job.order_id)
            if order.status not in {"paid", "closed"}:
                raise HTTPException(409, "Заказ отменён или возвращён")
            payload = unpacked(job.payload)
            if (
                kind == "steam-rent"
                and not payload.get("extended_lease")
                and not await db.scalar(select(RentalLease.id).where(RentalLease.job_id == job.id))
            ):
                raise HTTPException(409, "Для заказа ещё не выделен аккаунт аренды")
            payload["manually_resolved"] = True
            job.payload = packed(payload)
            job.state, job.last_error, job.next_check_at = "completed", None, None
            order.auto_delivered, order.delivery_state = True, "sent"
            await db.commit()
        elif action in {"check", "retry"}:
            if job.state == "completed":
                return {"ok": True}
            if (
                job.remote_id
                and kind in {"auto-smm", "auto-stars"}
                and not unpacked(job.payload).get("delivery_attempted")
            ):
                job.state = "submitted"
            elif (
                action == "retry"
                and job.state == "error"
                and not job.remote_id
                and not unpacked(job.payload).get("delivery_attempted")
                and not unpacked(job.payload).get("extended_lease")
                and not await db.scalar(select(RentalLease.id).where(RentalLease.job_id == job.id))
            ):
                job.state = "ready" if job.target or kind == "steam-rent" else "waiting_target"
            else:
                raise HTTPException(
                    409,
                    "Результат покупки неизвестен. Повторная покупка запрещена до проверки поставщика.",
                )
            await db.commit()
            integrated_jobs.start_job(ident)
        elif action == "refill" and kind == "auto-smm" and job.remote_id:
            payload = unpacked(job.payload)
            if payload.get("refill_requested"):
                raise HTTPException(409, "Рефилл уже запрашивался; проверьте поставщика")
            payload["refill_requested"] = True
            job.payload = packed(payload)
            await db.commit()
            try:
                result = await smm_request(payload["provider"], "refill", order=job.remote_id)
                if not result.get("refill"):
                    raise ValueError("Поставщик не подтвердил рефилл")
            except Exception:
                raise HTTPException(
                    502, "Результат рефилла требует проверки у поставщика"
                ) from None
        else:
            raise HTTPException(422, "Операция недоступна в текущем состоянии заказа")
    return {"ok": True}


@router.post("/steam-rent/inventory", status_code=201)
async def add_inventory(payload: RentalAccountCreate):
    async with Session() as db:
        binding = await db.get(PluginBinding, payload.binding_id)
        if not binding or binding.kind != "steam-rent" or binding.is_extension:
            raise HTTPException(422, "Выберите основной лот аренды")
        values = payload.model_dump(exclude={"binding_id", "label"})
        row = RentalAccount(
            binding_id=payload.binding_id,
            label=payload.label,
            credentials=packed(values),
            identity_hash=hashlib.sha256(values["steamid"].encode()).hexdigest(),
        )
        db.add(row)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, "Этот Steam-аккаунт уже есть в пуле") from None
        return {"id": row.id}


@router.post("/steam-rent/inventory/{ident}/test")
async def test_inventory(ident: int):
    from .steam_security import describe_login_error, verify_login

    async with Session() as db:
        row = await db.get(RentalAccount, ident)
        if not row:
            raise HTTPException(404, "Аккаунт не найден")
        try:
            await verify_login(unpacked(row.credentials))
        except Exception as error:
            detail = describe_login_error(error, credentials=unpacked(row.credentials))
            await bus.log(
                "steam-rent",
                f"Проверка входа не пройдена (#{ident}): {detail}",
                level="error",
                source_plugin="steam-rent",
            )
            raise HTTPException(
                422, f"Вход в Steam не подтверждён: {detail}. Проверьте данные и maFile."
            ) from None
    return {"message": "Вход в Steam подтверждён"}


@router.get("/steam-rent/inventory/{ident}/credentials")
async def inventory_credentials(ident: int):
    async with Session() as db:
        row = await db.get(RentalAccount, ident)
        if not row:
            raise HTTPException(404, "Аккаунт не найден")
        return {**unpacked(row.credentials), "binding_id": row.binding_id, "label": row.label}


@router.post("/steam-rent/inventory/{ident}/recover")
async def recover_inventory(ident: int):
    from .security import vault
    from .steam_security import describe_login_error, verify_login

    async with steam_rentals.inventory_lock:
        async with Session() as db:
            row = await db.get(RentalAccount, ident)
            if not row or row.state != "quarantine" or not row.pending_password:
                raise HTTPException(409, "Нет незавершённой смены пароля")
            values = unpacked(row.credentials)
            values["password"] = vault.decrypt(row.pending_password)
            row.state = "verifying"
            await db.commit()
    try:
        await verify_login(values)
    except Exception as error:
        detail = describe_login_error(error, credentials=values)
        async with Session() as db:
            row = await db.get(RentalAccount, ident)
            row.state = "quarantine"
            row.last_error = f"Steam не подтвердил новый пароль: {detail}"
            await db.commit()
        await bus.log(
            "steam-rent",
            f"Восстановление аккаунта #{ident} не подтверждено: {detail}",
            level="error",
            source_plugin="steam-rent",
        )
        raise HTTPException(
            422, f"Steam не подтвердил новый пароль: {detail}. Введите актуальные данные вручную."
        ) from None
    async with Session() as db:
        row = await db.get(RentalAccount, ident)
        row.credentials = packed(values)
        row.pending_password, row.last_error, row.state = None, None, "free"
        leases = (
            await db.scalars(
                select(RentalLease).where(
                    RentalLease.account_id == ident, RentalLease.state == "attention"
                )
            )
        ).all()
        for lease in leases:
            lease.state = "finished"
        await db.commit()
    return {"ok": True}


@router.post("/steam-rent/leases/{ident}/finish")
async def finish_lease(ident: int):
    async with Session() as db:
        lease = await db.get(RentalLease, ident)
        if not lease or lease.state != "active":
            raise HTTPException(409, "Аренда уже завершена")
        lease.expires_at = utcnow()
        await db.commit()
    await steam_rentals.tick()
    return {"ok": True}


@router.patch("/steam-rent/inventory/{ident}")
async def inventory_state(ident: int, payload: RentalStateChange):
    async with steam_rentals.inventory_lock:
        async with Session() as db:
            row = await db.get(RentalAccount, ident)
            if not row:
                raise HTTPException(404, "Аккаунт не найден")
            if row.state not in {"free", "frozen"}:
                raise HTTPException(409, "Аккаунт занят или ожидает проверки смены пароля")
            row.state = payload.state
            await db.commit()
            return {"ok": True}


@router.put("/steam-rent/inventory/{ident}")
async def replace_inventory(ident: int, payload: RentalAccountCreate):
    from .steam_security import describe_login_error, verify_login

    values = payload.model_dump(exclude={"binding_id", "label"})
    async with steam_rentals.inventory_lock:
        async with Session() as db:
            row = await db.get(RentalAccount, ident)
            if not row or row.state not in {"free", "frozen", "quarantine"}:
                raise HTTPException(409, "Аккаунт не найден или занят")
            binding = await db.get(PluginBinding, payload.binding_id)
            if not binding or binding.kind != "steam-rent" or binding.is_extension:
                raise HTTPException(422, "Неверный основной лот аренды")
            previous_state = row.state
            if (
                previous_state == "quarantine"
                and values["password"] == unpacked(row.credentials)["password"]
            ):
                raise HTTPException(
                    422,
                    "После сбоя смены пароля укажите новый пароль Steam или проверьте сохранённую смену",
                )
            identity_hash = hashlib.sha256(values["steamid"].encode()).hexdigest()
            if await db.scalar(
                select(RentalAccount.id).where(
                    RentalAccount.identity_hash == identity_hash, RentalAccount.id != ident
                )
            ):
                raise HTTPException(409, "Этот Steam-аккаунт уже есть в пуле")
            row.state = "verifying"
            await db.commit()
    try:
        await verify_login(values)
    except Exception as error:
        detail = describe_login_error(error, credentials=values)
        async with Session() as db:
            row = await db.get(RentalAccount, ident)
            row.state = previous_state
            row.last_error = f"Новые данные не прошли проверку входа Steam: {detail}"
            await db.commit()
        await bus.log(
            "steam-rent",
            f"Замена данных аккаунта #{ident} не прошла проверку: {detail}",
            level="error",
            source_plugin="steam-rent",
        )
        raise HTTPException(422, f"Новые данные не прошли проверку входа Steam: {detail}") from None
    async with Session() as db:
        row = await db.get(RentalAccount, ident)
        row.binding_id, row.label, row.credentials = (
            payload.binding_id,
            payload.label,
            packed(values),
        )
        row.identity_hash = identity_hash
        row.state, row.pending_password, row.last_error = "free", None, None
        leases = (
            await db.scalars(
                select(RentalLease).where(
                    RentalLease.account_id == ident, RentalLease.state == "attention"
                )
            )
        ).all()
        for lease in leases:
            lease.state = "finished"
        await db.commit()
    return {"ok": True}


@router.delete("/steam-rent/inventory/{ident}")
async def delete_inventory(ident: int):
    async with Session() as db:
        row = await db.get(RentalAccount, ident)
        if not row or row.state not in {"free", "frozen", "draft"}:
            raise HTTPException(409, "Аккаунт занят или требует проверки")
        if await db.scalar(select(RentalLease.id).where(RentalLease.account_id == ident)):
            raise HTTPException(409, "Для аккаунта есть история аренд. Используйте заморозку.")
        await db.delete(row)
        await db.commit()
        return {"ok": True}


@router.post("/support-tickets/send")
async def support_send(payload: SupportSend):
    try:
        return {"id": await support_tickets.send(payload.order_ids)}
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.post("/support-tickets/requests/{ident}/retry")
async def support_retry(ident: int):
    try:
        return {"id": await support_tickets.retry(ident)}
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
