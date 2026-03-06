import datetime

from supervisor import state


def _iso_utc(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).isoformat()


def test_refresh_openrouter_budget_if_stale_updates_remaining(tmp_path, monkeypatch):
    state.init(tmp_path)
    old_ts = _iso_utc(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2))

    st = state.default_state_dict()
    st["openrouter_limit_remaining"] = 12.0
    st["openrouter_limit_remaining_updated_at"] = old_ts
    st["openrouter_last_check_at"] = old_ts
    state.save_state(st)

    monkeypatch.setattr(state, "check_openrouter_ground_truth", lambda: {
        "total_usd": 10.0,
        "daily_usd": 1.0,
        "limit": 100.0,
        "limit_remaining": 88.0,
    })

    refreshed = state.refresh_openrouter_budget_if_stale(max_age_sec=1800)
    assert float(refreshed["openrouter_limit_remaining"]) == 88.0
    assert refreshed.get("openrouter_limit_remaining_updated_at")
    assert refreshed["openrouter_limit_remaining_updated_at"] != old_ts


def test_refresh_openrouter_budget_if_stale_skips_when_fresh(tmp_path, monkeypatch):
    state.init(tmp_path)
    now_ts = _iso_utc(datetime.datetime.now(datetime.timezone.utc))

    st = state.default_state_dict()
    st["openrouter_limit_remaining"] = 50.0
    st["openrouter_limit_remaining_updated_at"] = now_ts
    st["openrouter_last_check_at"] = now_ts
    state.save_state(st)

    calls = {"n": 0}

    def _fake_gt():
        calls["n"] += 1
        return {
            "total_usd": 0.0,
            "daily_usd": 0.0,
            "limit": 100.0,
            "limit_remaining": 49.0,
        }

    monkeypatch.setattr(state, "check_openrouter_ground_truth", _fake_gt)
    refreshed = state.refresh_openrouter_budget_if_stale(max_age_sec=1800)

    assert calls["n"] == 0
    assert float(refreshed["openrouter_limit_remaining"]) == 50.0


def test_openrouter_budget_remaining_refreshes_stale_snapshot(tmp_path, monkeypatch):
    state.init(tmp_path)
    old_ts = _iso_utc(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3))

    st = state.default_state_dict()
    st["openrouter_limit_remaining"] = 5.0
    st["openrouter_limit_remaining_updated_at"] = old_ts
    st["openrouter_last_check_at"] = old_ts
    state.save_state(st)

    monkeypatch.setattr(state, "check_openrouter_ground_truth", lambda: {
        "total_usd": 1.0,
        "daily_usd": 0.1,
        "limit": 100.0,
        "limit_remaining": 77.0,
    })

    remaining = state.openrouter_budget_remaining(state.load_state())
    assert float(remaining) == 77.0


def test_local_llm_mode_ignores_openrouter_budget_snapshot(tmp_path, monkeypatch):
    state.init(tmp_path)
    old_ts = _iso_utc(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3))

    st = state.default_state_dict()
    st["openrouter_limit_remaining"] = 5.0
    st["openrouter_limit_remaining_updated_at"] = old_ts
    st["openrouter_last_check_at"] = old_ts
    state.save_state(st)

    monkeypatch.setenv("OUROBOROS_LLM_BASE_URL", "http://127.0.0.1:2455/v1")

    calls = {"n": 0}

    def _fake_gt():
        calls["n"] += 1
        return {
            "total_usd": 1.0,
            "daily_usd": 0.1,
            "limit": 100.0,
            "limit_remaining": 77.0,
        }

    monkeypatch.setattr(state, "check_openrouter_ground_truth", _fake_gt)

    remaining = state.openrouter_budget_remaining(state.load_state())

    assert remaining == float("inf")
    assert calls["n"] == 0
