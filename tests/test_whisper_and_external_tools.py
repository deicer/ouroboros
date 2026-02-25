from __future__ import annotations

import pathlib

from ouroboros.tools.registry import ToolContext, ToolRegistry
from ouroboros.tools.whisper_transcriber import _whisper_transcriber


def _mk_ctx(tmp_path: pathlib.Path) -> ToolContext:
    repo = tmp_path / "repo"
    drive = tmp_path / "drive"
    repo.mkdir(parents=True, exist_ok=True)
    drive.mkdir(parents=True, exist_ok=True)
    return ToolContext(repo_dir=repo, drive_root=drive)


def test_whisper_transcriber_reports_missing_script(tmp_path, monkeypatch):
    ctx = _mk_ctx(tmp_path)
    audio = ctx.repo_dir / "sample.wav"
    audio.write_text("dummy", encoding="utf-8")
    monkeypatch.setenv("OUROBOROS_WHISPER_SCRIPT", str(tmp_path / "missing.py"))

    out = _whisper_transcriber(ctx, str(audio))
    assert "WHISPER_SCRIPT_NOT_FOUND" in out


def test_whisper_transcriber_runs_external_script(tmp_path, monkeypatch):
    ctx = _mk_ctx(tmp_path)
    audio = ctx.repo_dir / "sample.wav"
    audio.write_text("dummy", encoding="utf-8")

    script = tmp_path / "whisper_transcriber.py"
    script.write_text(
        "import sys\n"
        "print('TRANSCRIBED:' + sys.argv[1])\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OUROBOROS_WHISPER_SCRIPT", str(script))

    out = _whisper_transcriber(ctx, str(audio))
    assert "exit_code=0" in out
    assert "TRANSCRIBED:" in out


def test_registry_loads_external_tool_modules(tmp_path, monkeypatch):
    ext_dir = tmp_path / "ext_tools"
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "ext_echo.py").write_text(
        "from ouroboros.tools.registry import ToolEntry\n"
        "def _h(ctx, text=''):\n"
        "    return f'ext:{text}'\n"
        "def get_tools():\n"
        "    return [ToolEntry('ext_echo', {\n"
        "        'name': 'ext_echo',\n"
        "        'description': 'external echo',\n"
        "        'parameters': {\n"
        "            'type': 'object',\n"
        "            'properties': {'text': {'type': 'string'}},\n"
        "            'required': []\n"
        "        }\n"
        "    }, _h)]\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("OUROBOROS_EXTERNAL_TOOLS_DIR", str(ext_dir))
    repo2 = tmp_path / "repo2"
    drive2 = tmp_path / "drive2"
    repo2.mkdir(parents=True, exist_ok=True)
    drive2.mkdir(parents=True, exist_ok=True)
    reg = ToolRegistry(repo_dir=repo2, drive_root=drive2)
    available = set(reg.available_tools())
    assert "ext_echo" in available
    assert reg.execute("ext_echo", {"text": "ok"}) == "ext:ok"
