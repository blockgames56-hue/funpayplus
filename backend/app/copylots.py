"""Public offer import into the current seller's category form."""

from decimal import Decimal

from bs4 import BeautifulSoup

from .funpay import FunPayClient, FunPayError, app_data, soup_of


def editor_form(soup: BeautifulSoup):
    for form in soup.find_all("form"):
        if form.find(attrs={"name": "fields[summary][ru]"}) and form.find(attrs={"name": "price"}):
            return form
    raise FunPayError("Форма категории не распознана. Предложение не опубликовано.")


def editor_fields(form) -> dict[str, str]:
    fields = {}
    for element in form.select("input[name], textarea[name], select[name]"):
        if element.get("type") in {"submit", "button", "file"}:
            continue
        if element.get("type") in {"checkbox", "radio"} and not element.has_attr("checked"):
            continue
        if element.name == "textarea":
            value = element.get_text()
        elif element.name == "select":
            option = element.select_one("option[selected]") or element.select_one("option")
            value = str(option.get("value", "")) if option else ""
        else:
            value = str(element.get("value", "on" if element.get("type") == "checkbox" else ""))
        fields[str(element["name"])] = value
    return fields


async def copy_form(client: FunPayClient, source_id: int) -> tuple[dict, dict[str, str]]:
    import re

    source = soup_of(
        (await client.request("GET", "lots/offer", params={"id": source_id}, safe=True)).text
    )
    backlink = source.select_one("a.js-back-link[href*='/lots/']")
    match = re.search(r"/lots/(\d+)/", backlink.get("href", "")) if backlink else None
    if not match:
        raise FunPayError("Категория исходного предложения не найдена")
    node_id = int(match[1])
    title = description = ""
    for item in source.select(".param-item"):
        heading = item.find("h5")
        value = item.find("div")
        if not heading or not value:
            continue
        name = heading.get_text(strip=True).lower()
        if name in {"краткое описание", "short description", "короткий опис"}:
            title = value.get_text("\n", strip=True)
        elif name in {"подробное описание", "detailed description", "докладний опис"}:
            description = value.get_text("\n", strip=True)
    if not title:
        raise FunPayError("Публичное описание исходного лота не найдено")
    own = soup_of(
        (await client.request("GET", "lots/offerEdit", params={"node": node_id}, safe=True)).text
    )
    csrf = app_data(own).get("csrf-token")
    if csrf:
        client.csrf = csrf
    form = editor_form(own)
    fields = editor_fields(form)
    if fields.get("offer_id", "0") not in {"0", ""}:
        raise FunPayError("Получена форма существующего лота вместо нового")
    controls = []
    for element in form.select("select[name], input[name], textarea[name]"):
        name = str(element["name"])
        if not name.startswith("fields[") or any(
            f"[{part}]" in name for part in ("summary", "desc", "payment_msg", "images")
        ):
            continue
        if element.get("type") in {"hidden", "file", "checkbox", "radio"}:
            continue
        label = form.find("label", attrs={"for": element.get("id", "")})
        controls.append(
            {
                "name": name,
                "label": label.get_text(" ", strip=True) if label else name,
                "value": fields.get(name, ""),
                "required": element.has_attr("required"),
                "options": [
                    {"value": str(option.get("value", "")), "label": option.get_text(strip=True)}
                    for option in element.select("option")
                ],
            }
        )
    return {
        "source_id": source_id,
        "node_id": node_id,
        "title": title,
        "description": description,
        "controls": controls,
    }, fields


async def publish_copy(
    client: FunPayClient,
    source_id: int,
    title: str,
    description: str,
    price: Decimal,
    amount: int,
    extra: dict[str, str],
):
    preview, fields = await copy_form(client, source_id)
    allowed = {control["name"]: control for control in preview["controls"]}
    for name, value in extra.items():
        if name not in allowed or len(value) > 10000:
            raise FunPayError("Недопустимое поле формы категории")
        options = allowed[name]["options"]
        if options and value not in {option["value"] for option in options}:
            raise FunPayError("Недопустимое значение поля категории")
        fields[name] = value
    for name, control in allowed.items():
        if control["required"] and not fields.get(name):
            raise FunPayError(f"Заполните поле: {control['label']}")
    fields.update(
        {
            "offer_id": "0",
            "node_id": str(preview["node_id"]),
            "active": "on",
            "price": str(price),
            "amount": str(amount),
            "fields[summary][ru]": title,
            "fields[desc][ru]": description,
            "auto_delivery": "",
            "secrets": "",
            "fields[images]": "",
            "csrf_token": client.csrf,
        }
    )
    result = client.json_response(
        await client.request(
            "POST", "lots/offerSave", headers={"X-Requested-With": "XMLHttpRequest"}, data=fields
        )
    )
    if result.get("error") or result.get("errors"):
        raise FunPayError(
            "FunPay отклонил публикацию. Проверьте поля категории в редакторе FunPay."
        )
    return {"ok": True}
