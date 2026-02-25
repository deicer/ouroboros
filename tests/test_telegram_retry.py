import pytest

from supervisor import telegram


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = int(status_code)
        self._payload = payload
        self.content = b"{}"

    def json(self):
        return self._payload


def test_send_message_retries_429_with_backoff(monkeypatch):
    client = telegram.TelegramClient("test-token")
    sleeps = []
    calls = {"n": 0}

    def _fake_sleep(sec):
        sleeps.append(sec)

    def _fake_post(_url, data=None, timeout=0):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(429, {
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 1},
            })
        return _FakeResponse(200, {"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr(telegram.time, "sleep", _fake_sleep)
    monkeypatch.setattr(telegram.requests, "post", _fake_post)

    ok, err = client.send_message(chat_id=1, text="hello")
    assert ok is True
    assert err == "ok"
    assert calls["n"] == 2
    assert sleeps == [1.0]


def test_send_message_retries_500_exponential_and_fails(monkeypatch):
    client = telegram.TelegramClient("test-token")
    sleeps = []
    calls = {"n": 0}

    def _fake_sleep(sec):
        sleeps.append(sec)

    def _fake_post(_url, data=None, timeout=0):
        calls["n"] += 1
        return _FakeResponse(500, {"ok": False, "error_code": 500, "description": "Internal"})

    monkeypatch.setattr(telegram.time, "sleep", _fake_sleep)
    monkeypatch.setattr(telegram.requests, "post", _fake_post)

    ok, err = client.send_message(chat_id=1, text="hello")
    assert ok is False
    assert "status=500" in err
    assert calls["n"] == 3
    assert sleeps == [1.0, 2.0]


def test_get_updates_retries_429_with_backoff(monkeypatch):
    client = telegram.TelegramClient("test-token")
    sleeps = []
    calls = {"n": 0}

    def _fake_sleep(sec):
        sleeps.append(sec)

    def _fake_get(_url, params=None, timeout=0):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(429, {
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 2},
            })
        return _FakeResponse(200, {"ok": True, "result": [{"update_id": 10}]})

    monkeypatch.setattr(telegram.time, "sleep", _fake_sleep)
    monkeypatch.setattr(telegram.requests, "get", _fake_get)

    result = client.get_updates(offset=0, timeout=10)
    assert result == [{"update_id": 10}]
    assert calls["n"] == 2
    assert sleeps == [2.0]


def test_get_updates_non_retryable_no_backoff(monkeypatch):
    client = telegram.TelegramClient("test-token")
    sleeps = []
    calls = {"n": 0}

    def _fake_sleep(sec):
        sleeps.append(sec)

    def _fake_get(_url, params=None, timeout=0):
        calls["n"] += 1
        return _FakeResponse(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})

    monkeypatch.setattr(telegram.time, "sleep", _fake_sleep)
    monkeypatch.setattr(telegram.requests, "get", _fake_get)

    with pytest.raises(RuntimeError):
        client.get_updates(offset=0, timeout=10)
    assert calls["n"] == 1
    assert sleeps == []
