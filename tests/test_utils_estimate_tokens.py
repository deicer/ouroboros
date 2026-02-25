from ouroboros import utils


def test_estimate_tokens_uses_utf8_fallback_for_russian(monkeypatch):
    monkeypatch.setattr(utils, "_TIKTOKEN_ENCODER", None, raising=False)
    monkeypatch.setattr(utils, "_TIKTOKEN_UNAVAILABLE", True, raising=False)

    text = "Привет, как дела?"
    expected = max(1, (len(text.encode("utf-8")) + 2) // 3)

    assert utils.estimate_tokens(text) == expected


def test_estimate_tokens_prefers_tiktoken_encoder_when_available(monkeypatch):
    class _DummyEncoder:
        def encode(self, text: str):
            return [1, 2, 3, 4, 5, 6, 7]

    monkeypatch.setattr(utils, "_TIKTOKEN_UNAVAILABLE", False, raising=False)
    monkeypatch.setattr(utils, "_TIKTOKEN_ENCODER", _DummyEncoder(), raising=False)

    assert utils.estimate_tokens("любой текст") == 7


def test_estimate_tokens_never_returns_zero(monkeypatch):
    monkeypatch.setattr(utils, "_TIKTOKEN_ENCODER", None, raising=False)
    monkeypatch.setattr(utils, "_TIKTOKEN_UNAVAILABLE", True, raising=False)
    assert utils.estimate_tokens("") == 1
