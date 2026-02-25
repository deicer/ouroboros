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
