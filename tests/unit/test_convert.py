"""convert action: zip of an act -> plan.json + latest AKN + meta, per item, with failure aggregation."""

import hashlib
import io
import json
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from dp_it_normativa_convert.akn import read
from dp_it_normativa_convert.convert import ConvertError, run_convert, safe_name
from dp_it_normativa_convert.plan import read_plan

FIXTURES = Path(__file__).parent.parent / "fixtures"
DIR = "DECRETO-LEGGE_20260910_157"
V0 = "2026-09-10_26G00178_ORIGINALE_V0.xml"
V1 = "2026-09-10_26G00178_VIGENZA_2026-11-10_V1.xml"
ACT_ID = "normattiva:DECRETO-LEGGE:2026:157"
URN = "urn:nir:stato:decreto.legge:2026-09-10;157"
ITEM: dict[str, Any] = {
    "key": f"{ACT_ID}|v1",
    "url": "https://www.normattiva.it/x",
    "meta": {"kind": "normattiva", "actId": ACT_ID, "version": "1", "denominazione": "DECRETO-LEGGE", "anno": 2026, "numero": 157},
}


def write_dl_zip(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{safe_name(ACT_ID)}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(FIXTURES / "dl-2026-157_V0.xml", f"{DIR}/{V0}")
        archive.write(FIXTURES / "dl-2026-157_V1.xml", f"{DIR}/{V1}")
    return path


def progress() -> tuple[list[tuple[int, str]], Any]:
    seen: list[tuple[int, str]] = []
    return seen, lambda percent, message: seen.append((percent, message))


def test_safe_name_matches_the_grab_naming() -> None:
    assert safe_name("normattiva:DECRETO LEGISLATIVO:2003:196") == "normattiva_DECRETO-LEGISLATIVO_2003_196"
    assert safe_name("gu:26G00178") == "gu_26G00178"


def test_dl_2026_157_gives_the_vigente_units_of_v1_and_the_history_of_both_versions(tmp_path: Path) -> None:
    write_dl_zip(tmp_path / "raw")
    _, sink = progress()

    result = run_convert({"items": [ITEM], "dirIn": str(tmp_path / "raw"), "dirOut": str(tmp_path / "out"), "lottoId": "L1"}, sink)

    stem = safe_name(ACT_ID)
    plan, act = read_plan(tmp_path / "out" / f"{stem}.plan.json")
    v0 = {u.urn: u.text_raw for u in read((FIXTURES / "dl-2026-157_V0.xml").read_text(encoding="utf-8"))}
    v1 = {u.urn: u.text_raw for u in read((FIXTURES / "dl-2026-157_V1.xml").read_text(encoding="utf-8"))}

    assert {u.urn: u.text_raw for u in plan.units} == v1
    assert plan.versions_count == 2
    assert {u.collection for u in plan.units} == {"NORMATTIVA"}
    assert {u.act_urn for u in plan.units} == {URN}
    closed = [r for r in plan.version_rows if r.valid_to is not None]
    opened = [r for r in plan.version_rows if r.valid_to is None]
    expected_closed = {u for u in v0 if u not in v1 or v0[u] != v1[u]}
    assert {r.urn for r in closed} == expected_closed
    assert all(r.valid_from == date(2026, 9, 10) and r.valid_to == date(2026, 11, 10) for r in closed)
    assert {r.urn for r in opened} == set(v1)
    opened_from = {r.urn: r.valid_from for r in opened}
    unchanged = {u for u in v1 if v0.get(u) == v1[u]}
    assert unchanged, "the fixture versions share unchanged units"
    assert {opened_from[u] for u in unchanged} == {date(2026, 9, 10)}
    assert {opened_from[u] for u in set(v1) - unchanged} == {date(2026, 11, 10)}
    assert act.act_urn == URN
    assert act.kind == "normattiva"
    assert (act.denominazione, act.year, act.number) == ("DECRETO-LEGGE", 2026, 157)
    assert (act.versions_count, act.units_count) == (2, len(v1))
    assert (act.first_valid_from, act.last_valid_from) == (date(2026, 9, 10), date(2026, 11, 10))
    assert act.collection == "NORMATTIVA"
    assert act.source_url == f"https://www.normattiva.it/uri-res/N2Ls?{URN}"
    assert result["metrics"] == {"acts": 1, "units": len(v1), "versions": 2, "versionRows": len(plan.version_rows)}


def test_the_latest_akn_is_written_byte_for_byte_with_a_meta_file(tmp_path: Path) -> None:
    write_dl_zip(tmp_path / "raw")
    _, sink = progress()

    result = run_convert({"items": [ITEM], "dirIn": str(tmp_path / "raw"), "dirOut": str(tmp_path / "out")}, sink)

    stem = safe_name(ACT_ID)
    akn = (tmp_path / "out" / f"{stem}.akn.xml").read_bytes()
    assert akn == (FIXTURES / "dl-2026-157_V1.xml").read_bytes().replace(b"\r\n", b"\n") or akn == (FIXTURES / "dl-2026-157_V1.xml").read_bytes()
    meta = json.loads((tmp_path / "out" / f"{stem}.convert.json").read_text(encoding="utf-8"))
    assert meta["sha256"] == hashlib.sha256(akn).hexdigest()
    assert meta["version_date"] == "2026-11-10"
    assert meta["act_id"] == ACT_ID
    assert result["converted"] == [
        {"key": ITEM["key"], "plan": str(tmp_path / "out" / f"{stem}.plan.json"), "akn": str(tmp_path / "out" / f"{stem}.akn.xml"),
         "meta": str(tmp_path / "out" / f"{stem}.convert.json")}
    ]
    assert result["dirOut"] == str(tmp_path / "out")


def test_collection_can_be_set_by_the_step(tmp_path: Path) -> None:
    write_dl_zip(tmp_path / "raw")

    run_convert({"items": [ITEM], "dirIn": str(tmp_path / "raw"), "dirOut": str(tmp_path / "out"), "collection": "CODICI"}, lambda *_: None)

    plan, act = read_plan(tmp_path / "out" / f"{safe_name(ACT_ID)}.plan.json")
    assert {u.collection for u in plan.units} == {"CODICI"}
    assert act.collection == "CODICI"


def test_a_corrupt_zip_fails_the_item_and_the_run_lists_it_while_good_acts_are_kept(tmp_path: Path) -> None:
    write_dl_zip(tmp_path / "raw")
    bad = {**ITEM, "key": "normattiva:LEGGE:2000:212|v1", "meta": {**ITEM["meta"], "actId": "normattiva:LEGGE:2000:212", "denominazione": "LEGGE",
                                                                  "anno": 2000, "numero": 212}}
    (tmp_path / "raw" / "normattiva_LEGGE_2000_212.zip").write_bytes(b"PK not really a zip")

    with pytest.raises(ConvertError) as error:
        run_convert({"items": [bad, ITEM], "dirIn": str(tmp_path / "raw"), "dirOut": str(tmp_path / "out")}, lambda *_: None)

    assert error.value.code == "system_error"
    assert bad["key"] in str(error.value)
    assert ITEM["key"] not in str(error.value)
    assert (tmp_path / "out" / f"{safe_name(ACT_ID)}.plan.json").exists()
    assert not (tmp_path / "out" / "normattiva_LEGGE_2000_212.plan.json").exists()


def test_a_missing_zip_and_an_unreadable_version_are_failed_items(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    with zipfile.ZipFile(tmp_path / "raw" / f"{safe_name(ACT_ID)}.zip", "w") as archive:
        archive.writestr(f"{DIR}/{V0}", "<not xml")
    other = {**ITEM, "key": "normattiva:LEGGE:2000:1|v1", "meta": {**ITEM["meta"], "actId": "normattiva:LEGGE:2000:1"}}

    with pytest.raises(ConvertError) as error:
        run_convert({"items": [ITEM, other], "dirIn": str(tmp_path / "raw"), "dirOut": str(tmp_path / "out")}, lambda *_: None)

    assert ITEM["key"] in str(error.value)
    assert other["key"] in str(error.value)
    assert error.value.code == "system_error"


def test_progress_is_reported_per_item(tmp_path: Path) -> None:
    write_dl_zip(tmp_path / "raw")
    seen, sink = progress()

    run_convert({"items": [ITEM], "dirIn": str(tmp_path / "raw"), "dirOut": str(tmp_path / "out")}, sink)

    assert [p for p, _ in seen] == [100]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"items": [], "dirIn": "a", "dirOut": "b"},
        {"items": [ITEM], "dirIn": "a"},
        {"items": [ITEM], "dirOut": "b"},
        {"items": [{"key": "k"}], "dirIn": "a", "dirOut": "b"},
        {"items": [{**ITEM, "meta": {**ITEM["meta"], "kind": "other"}}], "dirIn": "a", "dirOut": "b"},
        {"items": [ITEM], "dirIn": "a", "dirOut": "b", "collection": ""},
    ],
)
def test_bad_parameters_are_config_errors(params: dict[str, Any]) -> None:
    with pytest.raises(ConvertError) as error:
        run_convert(params, lambda *_: None)

    assert error.value.code == "config_error"


def test_the_corrupt_input_message_names_the_reason(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / f"{safe_name(ACT_ID)}.zip").write_bytes(io.BytesIO(b"junk").getvalue())

    with pytest.raises(ConvertError, match=r"not a zip|BadZipFile|File is not"):
        run_convert({"items": [ITEM], "dirIn": str(tmp_path / "raw"), "dirOut": str(tmp_path / "out")}, lambda *_: None)
