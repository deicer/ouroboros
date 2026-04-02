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


def test_send_message_reply_returns_message_id(monkeypatch):
    client = telegram.TelegramClient("test-token")

    def _fake_post(_url, data=None, timeout=0):
        assert data["reply_to_message_id"] == 42
        assert data["allow_sending_without_reply"] is True
        return _FakeResponse(200, {"ok": True, "result": {"message_id": 77}})

    monkeypatch.setattr(telegram.requests, "post", _fake_post)

    ok, err, message_id = client.send_message_reply(
        chat_id=1,
        text="hello",
        reply_to_message_id=42,
    )
    assert ok is True
    assert err == "ok"
    assert message_id == 77


def test_edit_and_delete_message_use_expected_methods(monkeypatch):
    client = telegram.TelegramClient("test-token")
    calls = []

    def _fake_post(url, data=None, timeout=0):
        calls.append((url, data))
        return _FakeResponse(200, {"ok": True, "result": True})

    monkeypatch.setattr(telegram.requests, "post", _fake_post)

    ok_edit, err_edit = client.edit_message_text(chat_id=1, message_id=77, text="updated")
    ok_delete, err_delete = client.delete_message(chat_id=1, message_id=77)

    assert ok_edit is True
    assert err_edit == "ok"
    assert ok_delete is True
    assert err_delete == "ok"
    assert calls[0][0].endswith("/editMessageText")
    assert calls[1][0].endswith("/deleteMessage")


def test_markdown_reply_threads_all_chunks(monkeypatch):
    calls = []

    class _FakeTG:
        def send_message_reply(self, chat_id, text, reply_to_message_id, parse_mode=""):
            calls.append((chat_id, text, reply_to_message_id, parse_mode))
            return True, "ok", len(calls)

        def send_message(self, chat_id, text, parse_mode=""):
            raise AssertionError("plain send should not be used for reply-threaded chunks")

    monkeypatch.setattr(telegram, "get_tg", lambda: _FakeTG())
    monkeypatch.setattr(telegram, "_chunk_markdown_for_telegram", lambda *_args, **_kwargs: ["one", "two"])

    ok, err, message_id = telegram._send_markdown_telegram(
        1,
        "ignored",
        reply_to_message_id=42,
    )

    assert ok is True
    assert err == "ok"
    assert message_id == 1
    assert [call[2] for call in calls] == [42, 42]


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
