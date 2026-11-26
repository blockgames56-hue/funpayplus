"""Durable supplier delivery for explicitly mapped FunPay offers."""

from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from .db import Session
from .events import bus
from .funpay import FunPayError
from .models import AppRouteDelivery, AppRouteMapping, AppRouteSupplier, Chat, Order
from .security import vault
from .services import accounts, get_setting

delivery_lock = asyncio.Lock()


def validate_endpoint(value: str) -> str:
    parsed = urlparse(value)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Укажите HTTPS URL без учётных данных и фрагмента")
    if host == "localhost" or host.endswith(".localhost") or "." not in host:
        raise ValueError("Укажите публичный HTTPS адрес")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP-адреса поставщиков не поддерживаются")
    return value


def detail_lot_id(detail: dict) -> str | None:
    for source in (detail, detail.get("details"), detail.get("offer"), detail.get("lot")):
        if not isinstance(source, dict):
            continue
        for key in ("offer_id", "lot_id", "funpay_item_id"):
            value = source.get(key)
            if str(value).isdigit() and int(value) > 0:
                return str(value)
        if (source is detail.get("offer") or source is detail.get("lot")) and str(source.get("id", "")).isdigit():
            return str(source["id"])
    return None


async def deliver(order_id: int) -> bool:
    """Return True when a mapping owns the order, including uncertain states."""
    if not (await get_setting("plugin_controls", {})).get("app-route", True):
        return False
    async with delivery_lock:
        async with Session() as db:
            order = await db.get(Order, order_id)
            if not order or order.status != "paid":
                return False
            if await db.scalar(select(AppRouteDelivery.id).where(AppRouteDelivery.order_id == order.id)):
                return True
            if order.delivery_state == "supplier_pending":
                return True
            if order.delivery_state != "pending":
                return False
            if not order.buyer_id:
                return False
            if not await db.scalar(select(AppRouteMapping.id).where(
                AppRouteMapping.account_id == order.account_id,
            ).limit(1)):
                return False
            client = await accounts.client(order.account_id)
            detail = await client.order_details(order.funpay_order_id)
            lot_id = detail_lot_id(detail) or order.funpay_lot_id
            if not lot_id:
                return False
            if detail.get("status") != "paid" or int(detail.get("buyer", {}).get("user_id", 0)) != order.buyer_id:
                return False
            order.funpay_lot_id = lot_id
            mapping = await db.scalar(select(AppRouteMapping).where(
                AppRouteMapping.account_id == order.account_id,
                AppRouteMapping.funpay_item_id == lot_id,
            ))
            if not mapping:
                await db.commit()
                return False
            supplier = await db.get(AppRouteSupplier, mapping.supplier_id)
            if not supplier or not supplier.is_active:
                return False
            existing = await db.scalar(select(AppRouteDelivery).where(AppRouteDelivery.order_id == order.id))
            if existing:
                return True
            chat = await db.scalar(select(Chat).where(
                Chat.account_id == order.account_id,
                Chat.interlocutor_id == order.buyer_id,
            ))
            if not chat:
                return True
            delivery = AppRouteDelivery(order_id=order.id, mapping_id=mapping.id, status="sending", attempts=0)
            db.add(delivery)
            order.delivery_state = "supplier_pending"
            await db.commit()
            if supplier.api_type != "webhook":
                delivery.status = "manual"
                await db.commit()
                await bus.log("orders", f"Поставщик #{supplier.id}: Telegram Bot требует ручной выдачи заказа #{order.funpay_order_id}", order.account_id, "error", source_plugin='app-route')
                return True
            payload = {
                "order_id": order.funpay_order_id,
                "buyer": order.buyer_name,
                "item": mapping.supplier_item_id,
                "quantity": mapping.quantity,
            }
            headers = {"Idempotency-Key": f"funpay-plus-{order.funpay_order_id}"}
            if supplier.token:
                headers["Authorization"] = f"Bearer {vault.decrypt(supplier.token)}"
            try:
                validate_endpoint(supplier.endpoint_url)
                async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as http:
                    for attempt in range(2):
                        delivery.attempts = attempt + 1
                        await db.commit()
                        try:
                            response = await http.post(supplier.endpoint_url, json=payload, headers=headers)
                            response.raise_for_status()
                            result = response.json()
                            data = result.get("data") if isinstance(result, dict) else None
                            if not isinstance(result, dict) or result.get("status") != "success" or not isinstance(data, str) or not data.strip():
                                raise ValueError("Поставщик не вернул подтверждённый ключ")
                            key = data.strip()
                            delivery.result_data = vault.encrypt(key)
                            delivery.status = "received"
                            await db.commit()
                            break
                        except (httpx.HTTPError, ValueError):
                            if attempt:
                                raise
                from .message_templates import order_context, render
                text = await render("app-route", "route_success", order_context(order, key=key), db=db)
                order.delivery_text = vault.encrypt(text)
                delivery.status = "message_sending"
                await db.commit()
                await accounts.send(chat.id, text)
                order.auto_delivered = True
                order.delivery_state = "sent"
                delivery.status = "sent"
                await db.commit()
                await bus.log("orders", f"Поставщик выдал заказ #{order.funpay_order_id}", order.account_id, source_plugin='app-route')
            except (httpx.HTTPError, ValueError, FunPayError) as error:
                delivery.status = "uncertain"
                delivery.last_error = str(error)[:500] if not isinstance(error, httpx.HTTPError) else "Сетевая ошибка поставщика"
                await db.commit()
                await bus.log("orders", f"Ошибка поставщика! Заказ #{order.funpay_order_id}: проверьте выдачу вручную", order.account_id, "error", source_plugin='app-route')
                try:
                    from .message_templates import notify, order_context, send
                    # A send failure after receiving the key is ambiguous: do not
                    # tell the buyer delivery failed or repeat the product.
                    if not delivery.result_data:
                        await send("app-route", "route_fail", chat.id, order_context(order))
                    await notify("app-route", "route_admin_fail", order_context(order))
                except FunPayError:
                    await bus.log("notifications", "Алерт поставщика не доставлен: настройте Telegram", order.account_id, "error", source_plugin='app-route')
            return True
