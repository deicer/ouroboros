import json
import pathlib

from ouroboros.context import build_llm_messages
from ouroboros.memory import Memory

REPO = pathlib.Path(__file__).resolve().parent.parent


class _TestEnv:
    def __init__(self, repo_root: pathlib.Path, drive_root: pathlib.Path):
        self.repo_dir = repo_root
        self.drive_root = drive_root
        self.branch_dev = "main"

    def repo_path(self, rel: str) -> pathlib.Path:
        return self.repo_dir / rel

    def drive_path(self, rel: str) -> pathlib.Path:
        return self.drive_root / rel


def _semi_stable_text(messages: list[dict]) -> str:
    system = messages[0]
    assert system["role"] == "system"
    content = system["content"]
    assert isinstance(content, list)
    return str(content[1]["text"])


def _dynamic_text(messages: list[dict]) -> str:
    system = messages[0]
    assert system["role"] == "system"
    content = system["content"]
    assert isinstance(content, list)
    return str(content[2]["text"])


def test_build_llm_messages_includes_goals_section_and_clips(tmp_path):
    goals_path = tmp_path / "memory" / "goals.json"
    goals_path.parent.mkdir(parents=True, exist_ok=True)

    middle_marker = "MIDDLE_SENTINEL_FOR_GOALS_CLIP"
    long_goal = ("A" * 7000) + middle_marker + ("B" * 7000)
    goals_payload = {"active_goal": long_goal, "priority": "high"}
    goals_path.write_text(json.dumps(goals_payload, ensure_ascii=False), encoding="utf-8")

    env = _TestEnv(repo_root=REPO, drive_root=tmp_path)
    memory = Memory(drive_root=tmp_path, repo_dir=REPO)
    messages, _ = build_llm_messages(
        env=env,
        memory=memory,
        task={"id": "task-goals", "type": "user", "text": "check goals"},
    )

    semi_stable = _semi_stable_text(messages)
    assert "## Goals" in semi_stable
    assert '"priority": "high"' in semi_stable
    assert "...(truncated)..." in semi_stable
    assert middle_marker not in semi_stable


def test_build_llm_messages_skips_empty_goals_json(tmp_path):
    goals_path = tmp_path / "memory" / "goals.json"
    goals_path.parent.mkdir(parents=True, exist_ok=True)
    goals_path.write_text("{}", encoding="utf-8")

    env = _TestEnv(repo_root=REPO, drive_root=tmp_path)
    memory = Memory(drive_root=tmp_path, repo_dir=REPO)
    messages, _ = build_llm_messages(
        env=env,
        memory=memory,
        task={"id": "task-empty-goals", "type": "user", "text": "check goals"},
    )

    semi_stable = _semi_stable_text(messages)
    assert "## Goals" not in semi_stable


def test_build_llm_messages_reads_legacy_chat_history_summary(tmp_path):
    legacy_summary = tmp_path / "memory" / "chat_history_summary.md"
    legacy_summary.parent.mkdir(parents=True, exist_ok=True)
    legacy_summary.write_text("Legacy summary block", encoding="utf-8")

    env = _TestEnv(repo_root=REPO, drive_root=tmp_path)
    memory = Memory(drive_root=tmp_path, repo_dir=REPO)
    messages, _ = build_llm_messages(
        env=env,
        memory=memory,
        task={"id": "task-legacy-summary", "type": "user", "text": "check summary"},
    )

    semi_stable = _semi_stable_text(messages)
    assert "## Dialogue Summary" in semi_stable
    assert "legacy compacted history" in semi_stable
    assert "Legacy summary block" in semi_stable


def test_build_llm_messages_runtime_context_omits_utc_now_and_volatile_state_fields(tmp_path):
    state_path = tmp_path / "state" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "owner_id": 123,
                "owner_chat_id": 456,
                "no_approve_mode": True,
                "session_id": "volatile-session",
                "tg_offset": 999,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    env = _TestEnv(repo_root=REPO, drive_root=tmp_path)
    memory = Memory(drive_root=tmp_path, repo_dir=REPO)
    messages, _ = build_llm_messages(
        env=env,
        memory=memory,
        task={"id": "task-runtime", "type": "user", "text": "check runtime"},
    )

    dynamic = _dynamic_text(messages)
    assert '"utc_now"' not in dynamic
    assert '"owner_id": 123' in dynamic
    assert '"owner_chat_id": 456' in dynamic
    assert '"no_approve_mode": true' in dynamic
    assert "volatile-session" not in dynamic
    assert '"tg_offset"' not in dynamic
