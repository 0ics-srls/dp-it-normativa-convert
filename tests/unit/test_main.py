"""Proc-protocol loop and manifest of the convert class."""

import io
import json
from pathlib import Path
from typing import Any

from test_convert import ACT_ID, ITEM, write_dl_zip

import main

ROOT = Path(__file__).resolve().parent.parent.parent


def lines_of(text: str) -> tuple[int, list[dict[str, Any]]]:
    out = io.StringIO()
    code = main.main(io.StringIO(text), out)
    return code, [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def request(action: str, **params: Any) -> str:
    return json.dumps({"_action": action, "_meta": {"executionId": "e1"}, **params}) + "\n"


def test_execute_converts_and_emits_progress_then_one_result(tmp_path: Path) -> None:
    write_dl_zip(tmp_path / "raw")

    code, lines = lines_of(request("execute", items=[ITEM], dirIn=str(tmp_path / "raw"), dirOut=str(tmp_path / "out")))

    assert code == 0
    assert lines[-1]["type"] == "result"
    assert all(m["type"] == "progress" for m in lines[:-1])
    assert lines[-1]["data"]["metrics"]["acts"] == 1
    assert (tmp_path / "out" / "normattiva_DECRETO-LEGGE_2026_157.plan.json").exists()


def test_a_failing_act_prints_one_error_line_with_system_error(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()

    code, lines = lines_of(request("execute", items=[ITEM], dirIn=str(tmp_path / "raw"), dirOut=str(tmp_path / "out")))

    assert code != 0
    errors = [m for m in lines if m["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["code"] == "system_error"
    assert ACT_ID in errors[0]["message"]


def test_bad_line_unknown_action_and_terminate() -> None:
    _, lines = lines_of("not json\n" + request("bogus"))
    assert [m["code"] for m in lines] == ["config_error", "config_error"]

    _, after = lines_of(request("terminate") + request("bogus"))
    assert after == []


def test_manifest_declares_execute_with_items_dirs_and_collection() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["name"] == "dp-it-normativa-convert"
    assert set(manifest["actions"]) == {"execute", "terminate"}
    properties = manifest["actions"]["execute"]["input"]["properties"]
    assert {"items", "dirIn", "dirOut", "lottoId", "collection"} <= set(properties)
    assert manifest["actions"]["execute"]["input"]["required"] == ["items", "dirIn", "dirOut"]
