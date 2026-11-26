"""Validated round-trip of a seller's FunPay offer editor form."""

from decimal import Decimal

from .copylots import editor_fields, editor_form
from .funpay import FunPayClient, FunPayError, ResponseFormatError, app_data, soup_of

LABELS = {
    "summary": "Краткое описание",
    "desc": "Подробное описание",
    "payment_msg": "Сообщение покупателю после оплаты",
    "price": "Цена за 1 шт. (для продавца)",
    "amount": "Наличие",
    "active": "Активное",
    "auto_delivery": "Автоматическая выдача",
    "secrets": "Товары для автоматической выдачи",
}


def field_label(name: str) -> str:
    for key in ("summary", "desc", "payment_msg"):
        if name.startswith(f"fields[{key}]"):
            return LABELS[key]
    return LABELS.get(name, name)


def describe_form(html: str, *, offer_id: str | None = None) -> tuple[dict, dict[str, str]]:
    soup = soup_of(html)
    form = editor_form(soup)
    fields = editor_fields(form)
    if offer_id is not None and str(fields.get("offer_id")) != str(offer_id):
        raise FunPayError("FunPay вернул форму другого лота")
    if offer_id is None and str(fields.get("offer_id", "0")) not in {"0", ""}:
        raise FunPayError("FunPay вернул существующий лот вместо новой формы")
    controls = []
    seen = set()
    for element in form.select("input[name], textarea[name], select[name]"):
        name = element.get("name", "")
        kind = element.get("type", "text")
        category_hidden = (
            kind == "hidden"
            and name.startswith("fields[")
            and "lot-field-input" in element.get("class", [])
        )
        if name in seen:
            continue
        if (
            name in {"query", "csrf_token", "offer_id", "node_id"}
            or kind
            in {
                "hidden",
                "submit",
                "button",
                "file",
            }
            and not category_hidden
        ):
            continue
        if name == "fields[images]":
            continue
        seen.add(name)
        label = (
            form.find("label", attrs={"for": element.get("id", "")}) if element.get("id") else None
        )
        if not label:
            group = element.find_parent(class_="form-group")
            label = group.find("label") if group else None
        radio_options = (
            form.find_all("input", attrs={"type": "radio", "name": name}) if kind == "radio" else []
        )
        button_group = (
            element.find_parent(class_="lot-field-radio-box") if category_hidden else None
        )
        button_options = button_group.select("button[value]") if button_group else []
        controls.append(
            {
                "name": name,
                "label": label.get_text(" ", strip=True) if label else field_label(name),
                "value": fields.get(name, ""),
                "kind": "select"
                if element.name == "select" or kind == "radio" or button_options
                else "checkbox"
                if kind == "checkbox"
                else "textarea"
                if element.name == "textarea"
                else "number"
                if name in {"price", "amount"}
                else "text",
                "required": element.has_attr("required"),
                "readonly": element.has_attr("readonly") or element.has_attr("disabled"),
                "checked_value": str(element.get("value", "on")) if kind == "checkbox" else "",
                "max_length": int(element["maxlength"])
                if str(element.get("maxlength", "")).isdigit()
                else None,
                "options": [
                    {
                        "value": str(option.get("value", "")),
                        "label": option.get_text(" ", strip=True),
                    }
                    for option in element.select("option")
                ],
            }
        )
        if radio_options:
            controls[-1]["options"] = [
                {
                    "value": str(option.get("value", "")),
                    "label": (
                        form.find("label", attrs={"for": option.get("id", "")}) or option.parent
                    ).get_text(" ", strip=True),
                }
                for option in radio_options
            ]
        elif button_options:
            controls[-1]["options"] = [
                {"value": str(option["value"]), "label": option.get_text(" ", strip=True)}
                for option in button_options
            ]
    buyer = soup.select_one(
        "input[name='price_buyer'], input[name='buyer_price'], input.js-buyer-price"
    )
    return {
        "controls": controls,
        "node_id": int(fields.get("node_id") or 0),
        "buyer_price": buyer.get("value") if buyer else None,
    }, fields


async def load_form(
    client: FunPayClient, *, offer_id: str | None = None, node_id: int | None = None
):
    if offer_id is None and not node_id:
        raise FunPayError("Укажите категорию")
    if offer_id is not None and getattr(client, "native_lot_forms", False) is True:
        html = await client.lot_form_html(offer_id)
    else:
        response = await client.request(
            "GET", "lots/offerEdit", safe=True,
            params={"offer": offer_id} if offer_id is not None else {"node": node_id},
        )
        html = response.text
    metadata, fields = describe_form(html, offer_id=offer_id)
    csrf = app_data(soup_of(html)).get("csrf-token") or fields.get("csrf_token")
    if csrf:
        client.csrf = csrf
    if not client.csrf:
        raise FunPayError("Не удалось получить токен формы FunPay")
    return metadata, fields


def merge_fields(metadata: dict, fields: dict[str, str], changes: dict[str, str]) -> dict[str, str]:
    allowed = {item["name"]: item for item in metadata["controls"]}
    if any(name not in allowed for name in changes):
        raise FunPayError("Поле не принадлежит форме этой категории")
    result = {**fields}
    for name, value in changes.items():
        control = allowed[name]
        if len(value) > min(control.get("max_length") or 20000, 20000):
            raise FunPayError("Значение поля слишком длинное")
        if control["options"] and value not in {option["value"] for option in control["options"]}:
            raise FunPayError("Недопустимое значение поля категории")
        if control.get("readonly") and value != fields.get(name, ""):
            raise FunPayError("Это поле вычисляется FunPay")
        if control["kind"] == "checkbox" and value not in {"", control.get("checked_value", "on")}:
            raise FunPayError("Недопустимое значение переключателя")
        result[name] = value
    for name, control in allowed.items():
        if control["required"] and not result.get(name):
            raise FunPayError(f"Заполните поле: {control['label']}")
    if "price" in result:
        try:
            parsed_price = Decimal(result["price"].replace(",", "."))
            valid_price = parsed_price.is_finite() and parsed_price > 0
        except (ValueError, ArithmeticError):
            valid_price = False
        if not valid_price:
            raise FunPayError("Цена должна быть больше нуля")
    if "amount" in result and result["amount"]:
        if not result["amount"].isdigit() or int(result["amount"]) < 0:
            raise FunPayError("Количество не может быть отрицательным")
    return result


async def create_offers(
    client: FunPayClient,
    node_id: int,
    count: int,
    changes: dict[str, str],
    *,
    images: str | None = None,
):
    """Fetch a fresh new-offer form for every POST; never reuse the source ID."""
    created = 0
    for _ in range(count):
        try:
            metadata, fields = await load_form(client, node_id=node_id)
            if metadata["node_id"] and metadata["node_id"] != node_id:
                raise FunPayError("Форма принадлежит другой категории")
            merged = merge_fields(metadata, fields, changes)
            merged["offer_id"] = "0"
            merged["node_id"] = str(node_id)
            if images is not None and "fields[images]" in fields:
                merged["fields[images]"] = images
            await save_form(client, merged)
            created += 1
        except FunPayError as error:
            return {"created": created, "requested": count, "error": str(error)}
    return {"created": created, "requested": count}


async def save_form(client: FunPayClient, fields: dict[str, str], *, allow_no_error_ack: bool = False) -> None:
    if (getattr(client, "native_lot_forms", False) is True
            and str(fields.get("offer_id", "0")) not in {"", "0"}):
        await client.save_lot_fields({**fields, "csrf_token": client.csrf})
        return
    result = client.json_response(
        await client.request(
            "POST",
            "lots/offerSave",
            headers={"X-Requested-With": "XMLHttpRequest"},
            data={**fields, "csrf_token": client.csrf},
        )
    )
    if result.get("error") or result.get("errors"):
        raise FunPayError("FunPay отклонил сохранение лота; проверьте поля категории")
    acknowledged = result.get("done") is True or (
        allow_no_error_ack
        and "done" not in result
        and "error" in result
        and type(result["error"]) in {bool, int}
        and result["error"] == 0
    )
    if not acknowledged:
        raise ResponseFormatError(
            "FunPay не подтвердил сохранение. Проверьте список предложений перед повтором."
        )


async def quote_prices(client: FunPayClient, node_id: int, price: Decimal) -> list[dict]:
    if not price.is_finite() or price <= 0 or price > Decimal("10000000"):
        raise FunPayError("Укажите положительную цену до 10 000 000")
    result = client.json_response(
        await client.request(
            "POST",
            "lots/calc",
            safe=True,
            headers={"X-Requested-With": "XMLHttpRequest"},
            data={"nodeId": node_id, "price": str(price)},
        )
    )
    if result.get("error") or not isinstance(result.get("methods"), list):
        raise FunPayError("FunPay не вернул цены для способов оплаты")
    return [
        {
            "name": str(item.get("name", "")),
            "price": str(item.get("price", "")),
            "unit": str(item.get("unit", "")),
        }
        for item in sorted(result["methods"], key=lambda item: float(item.get("pos", 0)))
        if isinstance(item, dict)
    ]
