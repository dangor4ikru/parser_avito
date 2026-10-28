from abc import ABC, abstractmethod

from integrations.notifications.utils import escape_markdown_v2, get_price
from models import Item


class Notifier(ABC):

    @abstractmethod
    def notify(self, ad: Item = None, message: str = None):
        """Отправляем одно объявление"""
        pass

    def notify_many(self, ads: list[Item]):
        """Отправляем список объявлений"""
        for ad in ads:
            self.notify(ad=ad)

    # default форматирование
    def format(self, ad: Item) -> str:
        price = escape_markdown_v2(get_price(ad))
        title = escape_markdown_v2(getattr(ad, "title", ""))
        seller = escape_markdown_v2(
            getattr(ad, "seller_name", None) or getattr(ad, "sellerId", None) or ""
        )
        short_url = f"https://avito.ru/{getattr(ad, 'id', '')}"

        parts = []

        if price:
            part = f"*{price}*"
            if getattr(ad, "isPromotion", False):
                part += " 🢁"
            parts.append(part)

        price_drop = getattr(ad, "price_drop", None)
        if price_drop:
            parts.append(escape_markdown_v2(
                f"📉 Цена снижена на {price_drop} (было {ad.old_price})"
            ))

        if title:
            parts.append(f"[{title}]({short_url})")

        if seller:
            parts.append(f"Продавец: {seller}")

        return "\n".join(parts)
