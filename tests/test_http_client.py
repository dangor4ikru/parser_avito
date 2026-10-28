from unittest.mock import MagicMock

import pytest
from curl_cffi import requests

from parser.http.client import BlockedError, HttpClient, NonRetryableHttpError
from parser.proxies.proxy import MobileProxy, NoProxy, ServerProxy, normalize_proxy


class FakeResponse:
    def __init__(self, status_code=200, text="{}", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {"content-type": "application/json"}
        self.cookies = {}

    def json(self):
        return {"ok": True}


def make_client(responses, **kwargs):
    proxy = MagicMock(spec=NoProxy)
    proxy.get_httpx_proxy.return_value = None
    params = dict(
        proxy=proxy,
        max_retries=3,
        retry_delay=0,
        block_threshold=2,
        request_delay_min=0,
        request_delay_max=0,
    )
    params.update(kwargs)
    client = HttpClient(**params)
    client._client = MagicMock()
    client._client.request.side_effect = responses
    client._reset_client = MagicMock()
    client._sleep = MagicMock()
    return client, proxy


def test_success_returns_response():
    client, _ = make_client([FakeResponse(200)])
    assert client.request("GET", "https://x").status_code == 200


def test_block_codes_trigger_recovery_after_threshold():
    client, proxy = make_client([FakeResponse(429), FakeResponse(403), FakeResponse(200)])
    assert client.request("GET", "https://x").status_code == 200
    proxy.handle_block.assert_called_once()
    assert client._block_attempts == 0


def test_waf_page_with_200_is_treated_as_block():
    waf = FakeResponse(200, "<html>Доступ ограничен: проблема с IP</html>", {"content-type": "text/html"})
    client, _ = make_client([waf, FakeResponse(200)])
    client.request("GET", "https://x")
    assert client._client.request.call_count == 2


def test_json_mentioning_marker_is_not_a_block():
    resp = FakeResponse(200, '{"description": "доступ ограничен для детей"}')
    assert not HttpClient.is_blocked(resp)


def test_not_found_is_not_retried():
    client, proxy = make_client([FakeResponse(404)])
    with pytest.raises(NonRetryableHttpError):
        client.request("GET", "https://x")
    assert client._client.request.call_count == 1
    proxy.handle_block.assert_not_called()


def test_server_error_retried_without_recovery():
    client, proxy = make_client([FakeResponse(502), FakeResponse(503), FakeResponse(200)])
    assert client.request("GET", "https://x").status_code == 200
    proxy.handle_block.assert_not_called()


def test_card_request_does_not_rotate_ip_on_block():
    client, proxy = make_client([FakeResponse(429)])
    with pytest.raises(BlockedError):
        client.fetch_item_data(1)
    proxy.handle_block.assert_not_called()
    assert client._block_attempts == 1


def test_network_errors_trigger_recovery():
    err = requests.RequestsError("boom")
    client, proxy = make_client([err, err, FakeResponse(200)])
    client.request("GET", "https://x")
    proxy.handle_block.assert_called_once()


def test_recovery_survives_proxy_failure():
    client, proxy = make_client([FakeResponse(429), FakeResponse(429), FakeResponse(200)])
    proxy.handle_block.side_effect = Exception("modem down")
    assert client.request("GET", "https://x").status_code == 200


def test_backoff_is_exponential_and_capped():
    client, _ = make_client([], retry_delay=5, max_retry_delay=30)
    assert 5 <= client._backoff_delay(1) < 6
    assert 20 <= client._backoff_delay(3) < 21
    assert 30 <= client._backoff_delay(10) < 31


def test_backoff_respects_retry_after():
    client, _ = make_client([], retry_delay=5)
    assert client._backoff_delay(1, FakeResponse(429, headers={"retry-after": "12"})) == 12


def test_stop_event_aborts_request():
    stop = MagicMock()
    stop.is_set.return_value = True
    client, _ = make_client([FakeResponse(200)], stop_event=stop)
    with pytest.raises(RuntimeError):
        client.request("GET", "https://x")
    client._client.request.assert_not_called()


def test_pacing_waits_between_requests():
    client, _ = make_client([], request_delay_min=10, request_delay_max=20)
    client._wait_pacing()
    client._wait_pacing()
    waited = client._sleep.call_args[0][0]
    assert 9 < waited <= 20


def test_request_delay_is_random_within_range():
    from utils.random_pause import random_delay, random_pause

    delays = {round(random_delay(1, 3), 3) for _ in range(50)}
    assert len(delays) > 1
    assert all(1 <= d <= 3 for d in delays)
    assert all(10 <= random_pause(10) <= 15 for _ in range(50))
    assert random_pause(0) == 0
    assert random_delay(5, 2) == 5  # max < min -> берём min


def test_normalize_proxy_keeps_scheme():
    assert normalize_proxy("u:p@1.2.3.4:8000") == "http://u:p@1.2.3.4:8000"
    assert normalize_proxy("socks5://u:p@1.2.3.4:8000") == "socks5://u:p@1.2.3.4:8000"
    assert ServerProxy("socks5://1.2.3.4:1").get_httpx_proxy() == "socks5://1.2.3.4:1"


def test_mobile_proxy_rotation_cooldown_and_errors(monkeypatch):
    calls = []

    def fake_get(*args, **kwargs):
        calls.append(1)
        resp = MagicMock(status_code=200)
        resp.json.side_effect = ValueError("not json")
        return resp

    monkeypatch.setattr("parser.proxies.proxy.requests.get", fake_get)
    monkeypatch.setattr("parser.proxies.proxy.time.sleep", lambda s: None)
    proxy = MobileProxy("1.2.3.4:8000", "https://change")
    proxy.handle_block()
    proxy.handle_block()
    assert len(calls) == 1


def test_mobile_proxy_network_error_does_not_raise(monkeypatch):
    import requests as std_requests

    def fail(*args, **kwargs):
        raise std_requests.ConnectionError("down")

    monkeypatch.setattr("parser.proxies.proxy.requests.get", fail)
    MobileProxy("1.2.3.4:8000", "https://change").handle_block()
