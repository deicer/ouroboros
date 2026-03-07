from __future__ import annotations

from supervisor.workers import (
    _restart_bootstrap_text,
    _choose_restart_bootstrap_mode,
    _should_skip_auto_resume,
)


def test_auto_resume_skip_when_recent_same_signature():
    st = {
        "auto_resume_last": {
            "signature": "sig-a",
            "ts": "2026-02-25T08:00:00+00:00",
        }
    }
    skip = _should_skip_auto_resume(
        st,
        signature="sig-a",
        now_iso="2026-02-25T08:05:00+00:00",
        dedup_sec=900,
    )
    assert skip is True


def test_auto_resume_do_not_skip_if_signature_differs():
    st = {
        "auto_resume_last": {
            "signature": "sig-a",
            "ts": "2026-02-25T08:00:00+00:00",
        }
    }
    skip = _should_skip_auto_resume(
        st,
        signature="sig-b",
        now_iso="2026-02-25T08:05:00+00:00",
        dedup_sec=900,
    )
    assert skip is False


def test_auto_resume_do_not_skip_if_old():
    st = {
        "auto_resume_last": {
            "signature": "sig-a",
            "ts": "2026-02-25T08:00:00+00:00",
        }
    }
    skip = _should_skip_auto_resume(
        st,
        signature="sig-a",
        now_iso="2026-02-25T08:20:01+00:00",
        dedup_sec=900,
    )
    assert skip is False


def test_auto_resume_do_not_skip_after_new_restart_cycle():
    st = {
        "auto_resume_last": {
            "signature": "sig-a",
            "ts": "2026-02-25T08:39:14+00:00",
        }
    }
    skip = _should_skip_auto_resume(
        st,
        signature="sig-a",
        now_iso="2026-02-25T08:45:56+00:00",
        dedup_sec=900,
        restart_ts_iso="2026-02-25T08:45:56+00:00",
    )
    assert skip is False


def test_auto_resume_skip_if_already_sent_in_same_restart_cycle():
    st = {
        "auto_resume_last": {
            "signature": "sig-a",
            "ts": "2026-02-25T08:46:00+00:00",
        }
    }
    skip = _should_skip_auto_resume(
        st,
        signature="sig-a",
        now_iso="2026-02-25T08:46:20+00:00",
        dedup_sec=900,
        restart_ts_iso="2026-02-25T08:45:56+00:00",
    )
    assert skip is True


def test_restart_bootstrap_mode_resumes_recent_owner_work():
    mode = _choose_restart_bootstrap_mode(
        {"last_owner_message_at": "2026-03-08T10:00:00+00:00"},
        now_iso="2026-03-08T10:10:00+00:00",
        has_pending_restart_verify=False,
    )
    assert mode == "resume_owner_work"


def test_restart_bootstrap_mode_prefers_idle_self_improvement_for_stale_owner_context():
    mode = _choose_restart_bootstrap_mode(
        {"last_owner_message_at": "2026-03-07T05:00:00+00:00"},
        now_iso="2026-03-08T10:10:00+00:00",
        has_pending_restart_verify=False,
    )
    assert mode == "idle_self_improvement"


def test_restart_bootstrap_mode_resumes_when_restart_verify_is_pending():
    mode = _choose_restart_bootstrap_mode(
        {"last_owner_message_at": "2026-03-01T05:00:00+00:00"},
        now_iso="2026-03-08T10:10:00+00:00",
        has_pending_restart_verify=True,
    )
    assert mode == "resume_owner_work"


def test_idle_restart_bootstrap_text_points_agent_to_self_improvement():
    text = _restart_bootstrap_text("idle_self_improvement")
    assert "self-improvement" in text.lower()
    assert "owner task" in text.lower()
