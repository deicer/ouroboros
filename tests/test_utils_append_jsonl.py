import json
from concurrent.futures import ThreadPoolExecutor

from ouroboros.utils import append_jsonl


def test_append_jsonl_basic(tmp_path):
    path = tmp_path / "logs" / "events.jsonl"
    append_jsonl(path, {"n": 1, "msg": "a"})
    append_jsonl(path, {"n": 2, "msg": "b"})

    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2
    parsed = [json.loads(ln) for ln in lines]
    assert parsed[0]["n"] == 1
    assert parsed[1]["n"] == 2


def test_append_jsonl_threaded_no_loss(tmp_path):
    path = tmp_path / "logs" / "events.jsonl"
    total = 200

    def _write(i: int) -> None:
        append_jsonl(path, {"i": i})

    with ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(_write, range(total)))

    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == total
    parsed = [json.loads(ln) for ln in lines]
    ids = {int(item["i"]) for item in parsed}
    assert len(ids) == total
