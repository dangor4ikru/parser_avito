"""
Извлечение характеристик и имени продавца из ответов Avito
(перенесено из eminsk/avito-sdk, PR #334 / #337).
"""
from typing import Any


def nested_get(data: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _collect_pairs(items: Any, result: dict[str, str]) -> None:
    """Добавить в result пары «название: значение» из списка виджетов Avito."""
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("name") or item.get("label")
        value = item.get("description") or item.get("value") or item.get("text")
        if isinstance(title, str) and title.strip() and value is not None and str(value).strip():
            result[title.strip()] = str(value).strip()


def extract_params(payload: Any) -> dict[str, str]:
    """
    Характеристики объявления («О помещении», параметры авто, электроники и т.п.)
    из JSON карточки m.avito.ru/api/1/card/items/{id}.

    Поддерживаются:
    1. Beduin-сценарий: success.view.scenario.beduin.main.params.*.items
    2. Mobile API: success.mobile.params / parameters / properties
       (список или словарь с flat/groups)
    """
    if not isinstance(payload, dict):
        return {}

    params: dict[str, str] = {}

    card_params = nested_get(payload, "success", "view", "scenario", "beduin", "main", "params")
    if isinstance(card_params, dict):
        for widget in card_params.values():
            if isinstance(widget, dict):
                _collect_pairs(widget.get("items"), params)

    mobile = nested_get(payload, "success", "mobile")
    if isinstance(mobile, dict):
        for key in ("params", "parameters", "properties"):
            container = mobile.get(key)
            if isinstance(container, list):
                _collect_pairs(container, params)
            elif isinstance(container, dict):
                _collect_pairs(container.get("flat"), params)
                for group in container.get("groups") or []:
                    if isinstance(group, dict):
                        _collect_pairs(group.get("items"), params)

    return params


def extract_seller_name(payload: Any) -> str | None:
    """Имя продавца из JSON карточки или из сырого объявления каталога."""
    if not isinstance(payload, dict):
        return None

    name = payload.get("sellerName")
    if isinstance(name, str) and name.strip():
        return name.strip()

    for seller in (
        nested_get(payload, "success", "mobile", "seller"),
        nested_get(payload, "success", "mobile", "sellerInfo"),
        payload.get("sellerInfo"),
        payload.get("seller"),
    ):
        if isinstance(seller, dict):
            name = seller.get("name") or seller.get("title")
            if isinstance(name, str) and name.strip():
                return name.strip()

    return None


def format_params(params: dict[str, str] | None, separator: str = "; ") -> str:
    if not params:
        return ""
    return separator.join(f"{key}: {value}" for key, value in params.items())
