import time
from abc import ABC, abstractmethod
import requests
from loguru import logger


def normalize_proxy(proxy: str) -> str:
    """user:pass@ip:port -> http://user:pass@ip:port; socks5://... оставляем как есть"""
    proxy = proxy.strip()
    return proxy if "://" in proxy else f"http://{proxy}"


class Proxy(ABC):
    @abstractmethod
    def get_httpx_proxy(self) -> dict | None:
        pass

    @abstractmethod
    def handle_block(self):
        pass


class NoProxy(Proxy):
    def get_httpx_proxy(self):
        return None

    def handle_block(self):
        pass


class ServerProxy(Proxy):
    def __init__(self, proxy):
        self.proxy = proxy

    def get_httpx_proxy(self):
        return normalize_proxy(self.proxy)

    def handle_block(self):
        pass


class MobileProxy(Proxy):
    # IP не меняем чаще, чем раз в ROTATION_COOLDOWN сек.: несколько блоков подряд
    # (например, от карточек и каталога) не должны дёргать модем по кругу
    ROTATION_COOLDOWN = 15
    # время на переподключение модема к сотовой сети после смены IP
    RECONNECT_DELAY = 5

    def __init__(self, url, change_ip_url):
        self.url = url
        self.change_ip_url = change_ip_url
        self._last_rotation_at = 0.0

    def get_httpx_proxy(self):
        return normalize_proxy(self.url)

    def handle_block(self):
        if self._last_rotation_at and time.monotonic() - self._last_rotation_at < self.ROTATION_COOLDOWN:
            logger.info("IP только что менялся, повторную смену пропускаю")
            return

        # делаем запрос на смену IP
        params = {
            "format": "json"
        }
        try:
            res = requests.get(self.change_ip_url, params=params, timeout=15)
        except requests.RequestException as err:
            logger.warning(f"Не удалось сменить IP: {err}")
            return

        self._last_rotation_at = time.monotonic()
        if res.status_code != 200:
            logger.warning(f"Смена IP вернула HTTP {res.status_code}")
            return

        try:
            new_ip = res.json().get("new_ip")
        except (ValueError, AttributeError):
            new_ip = None
        logger.success(f"новый IP {new_ip or '(провайдер не сообщил)'}")
        time.sleep(self.RECONNECT_DELAY)
