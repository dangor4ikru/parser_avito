"""
Клиент для запросов парсера (curl_cffi)

Антиблок-логика (по мотивам eminsk/avito-sdk):
- случайная пауза между запросами (от/до), без «пачек» и ровного шага;
- блок распознаётся не только по коду ответа, но и по странице-заглушке WAF;
- экспоненциальная пауза между повторами, уважение заголовка Retry-After;
- 404/400 не повторяются, 5xx повторяются без смены IP и cookies;
- запросы карточек не запускают смену IP/покупку cookies: при блоке
  обогащение останавливается, а восстановление делает запрос каталога.
"""
import random
import time

from curl_cffi import requests
from loguru import logger

from parser.cookies.base import CookiesProvider
from parser.proxies.proxy import Proxy
from utils.random_pause import random_delay


BLOCK_STATUS_CODES = (403, 429, 439)
WAF_MARKERS = (
    "проблема с ip",
    "доступ ограничен",
    "проверка безопасности",
    "smartcaptcha",
)


class BlockedError(RuntimeError):
    """Avito ответил блокировкой, а восстановление сессии для запроса запрещено."""


class NonRetryableHttpError(RuntimeError):
    """Ответ, который бессмысленно повторять (404, 400 и т.п.)."""

    def __init__(self, status_code: int, url: str):
        super().__init__(f"HTTP {status_code} для {url}")
        self.status_code = status_code


class HttpClient:
    ITEM_API_URL = "https://m.avito.ru/api/1/card/items/{item_id}"
    ITEM_API_HEADERS = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ru-RU,ru;q=0.9",
        "referer": "https://m.avito.ru/",
        "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Mobile Safari/537.36"
        ),
    }

    def __init__(
        self,
        proxy: Proxy,
        cookies: CookiesProvider | None = None,
        timeout: int = 30,
        max_retries: int = 5,
        retry_delay: int = 5,
        block_threshold: int = 3,
        request_delay_min: float = 1.0,
        request_delay_max: float = 3.0,
        max_retry_delay: int = 60,
        stop_event=None,
    ):
        self.proxy = proxy
        self.cookies = cookies
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.retry_delay = retry_delay
        self.block_threshold = block_threshold
        self.request_delay_min = max(0.0, request_delay_min)
        self.request_delay_max = max(self.request_delay_min, request_delay_max)
        self.max_retry_delay = max_retry_delay
        self.stop_event = stop_event

        self._block_attempts = 0
        self._last_request_at = 0.0
        self._client = self._build_client()

    def _build_client(self) -> requests.Session:
        fingerprint = self.cookies.get_fingerprint() if self.cookies else None
        fingerprint = fingerprint if isinstance(fingerprint, dict) else {}
        fingerprint_headers = fingerprint.get("headers", {})
        fingerprint_headers = (
            fingerprint_headers if isinstance(fingerprint_headers, dict) else {}
        )
        user_agent = fingerprint_headers.get("user-agent")
        if not user_agent and self.cookies:
            user_agent = self.cookies.get_user_agent()
        user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        )
        is_mobile = " Mobile " in user_agent
        impersonate = fingerprint.get("impersonate") or "chrome"
        session = requests.Session(impersonate=impersonate)
        default_headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            "referer": "https://www.avito.ru/",
            'sec-ch-ua': '"Not=A?Brand";v="99", "Google Chrome";v="149", "Chromium";v="149"',
            'sec-ch-ua-mobile': '?1' if is_mobile else '?0',
            'sec-ch-ua-platform': '"Android"' if is_mobile else '"Windows"',
            'user-agent': user_agent,
        }
        headers = {**default_headers, **fingerprint_headers}
        headers["user-agent"] = user_agent
        session.headers.update(headers)
        if self.cookies:
            session.cookies.update(self.cookies.get())

        proxy = self.proxy.get_httpx_proxy()
        if proxy:
            session.proxies = {
                "http": proxy,
                "https": proxy,
            }

        return session

    def _reset_client(self) -> None:
        self._client.close()
        self._client = self._build_client()

    def _recover_session(self) -> None:
        """Сменить IP и обновить cookies в правильном порядке."""
        try:
            self.proxy.handle_block()
        except Exception as err:
            logger.warning(f"Не удалось сменить IP: {err}")
        if self.cookies:
            try:
                self.cookies.handle_block()
            except Exception as err:
                logger.warning(f"Не удалось обновить cookies: {err}")
        self._reset_client()
        self._block_attempts = 0

    def _stopped(self) -> bool:
        return bool(self.stop_event and self.stop_event.is_set())

    def _sleep(self, seconds: float) -> None:
        """Пауза, которую можно прервать кнопкой «Стоп»."""
        if seconds <= 0:
            return
        if self.stop_event is not None and hasattr(self.stop_event, "wait"):
            self.stop_event.wait(seconds)
        else:
            time.sleep(seconds)

    def _wait_pacing(self) -> None:
        """Случайная пауза между запросами: от request_delay_min до request_delay_max сек."""
        if self.request_delay_max <= 0:
            return
        gap = random_delay(self.request_delay_min, self.request_delay_max)
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < gap:
            self._sleep(gap - elapsed)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _retry_after(response) -> float | None:
        value = (getattr(response, "headers", None) or {}).get("retry-after")
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return None

    def _backoff_delay(self, attempt: int, response=None) -> float:
        retry_after = self._retry_after(response) if response is not None else None
        if retry_after is not None:
            return min(retry_after, self.max_retry_delay)
        delay = self.retry_delay * 2 ** (attempt - 1)
        return min(delay, self.max_retry_delay) + random.uniform(0, 1)

    @staticmethod
    def is_blocked(response) -> bool:
        """Блок — это коды 403/429/439 или страница-заглушка WAF с кодом 200."""
        if response.status_code in BLOCK_STATUS_CODES:
            return True
        if response.status_code != 200:
            return False
        content_type = (response.headers or {}).get("content-type", "")
        if "json" in content_type.lower():
            return False
        text = (response.text or "").lower()
        return any(marker in text for marker in WAF_MARKERS)

    def request(
        self,
        method: str,
        url: str,
        *,
        allow_recovery: bool = True,
        retries: int | None = None,
        **kwargs,
    ):
        last_exc = None

        for attempt in range(1, (retries or self.max_retries) + 1):
            if self._stopped():
                raise RuntimeError("Парсинг остановлен")

            self._wait_pacing()
            try:
                response = self._client.request(
                    method,
                    url,
                    timeout=self.timeout,
                    **kwargs,
                )
            except requests.RequestsError as e:
                last_exc = e
                self._block_attempts += 1
                logger.warning(f"Request error (attempt {attempt}): {e}")
                if allow_recovery and self._block_attempts >= self.block_threshold:
                    logger.warning(
                        "Достигнут лимит сетевых ошибок, "
                        "обновляем proxy и cookies"
                    )
                    self._recover_session()
                self._sleep(self._backoff_delay(attempt))
                continue

            if self.cookies:
                self.cookies.update(response)

            if self.is_blocked(response):
                self._block_attempts += 1
                logger.warning(
                    f"Запрос заблокирован ({response.status_code}) к {url}, "
                    f"попытка {self._block_attempts}"
                )
                if not allow_recovery:
                    raise BlockedError(
                        f"Блокировка ({response.status_code}) при запросе {url}"
                    )

                if self._block_attempts >= self.block_threshold:
                    logger.warning("Достигнут лимит блокировок, запускается обработка")
                    self._recover_session()

                delay = self._backoff_delay(attempt, response)
                logger.info(f"Повтор через {delay:.1f} сек.")
                self._sleep(delay)
                continue

            if response.status_code >= 500:
                last_exc = RuntimeError(f"HTTP {response.status_code} для {url}")
                logger.warning(
                    f"Сервер Avito ответил {response.status_code} (attempt {attempt})"
                )
                self._sleep(self._backoff_delay(attempt, response))
                continue

            self._block_attempts = 0
            if response.status_code >= 400:
                raise NonRetryableHttpError(response.status_code, url)
            return response

        raise RuntimeError("HTTP запросы были неуспешными") from last_exc

    def fetch_item_data(self, item_id: int) -> dict:
        """Загрузить JSON карточки объявления через mobile API.

        При блокировке бросает BlockedError, не меняя IP и не покупая cookies.
        """
        response = self.request(
            "GET",
            self.ITEM_API_URL.format(item_id=item_id),
            allow_recovery=False,
            retries=2,
            headers=self.ITEM_API_HEADERS.copy(),
        )
        logger.info(f"Карточка {item_id}: HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Ответ карточки не является JSON-объектом")
        return payload
