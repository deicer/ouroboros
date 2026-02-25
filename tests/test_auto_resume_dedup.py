from __future__ import annotations

from supervisor.workers import _should_skip_auto_resume


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
