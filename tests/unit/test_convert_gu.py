"""convert action for kind=gu: grab HTML + sidecar -> single-version plan.json, the source HTML as the stored document, meta."""

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from dp_it_normativa_convert.convert import ConvertError, run_convert
from dp_it_normativa_convert.plan import read_plan

CODE = "26G00178"
ACT_ID = f"gu:{CODE}"
STEM = "gu_26G00178"  # safe(actId): the plan / akn / convert files keep the naming ingest already loads
ITEM: dict[str, Any] = {
    "key": f"{ACT_ID}|v1",
    "url": "https://www.gazzettaufficiale.it/",
    "meta": {"kind": "gu", "actId": ACT_ID, "version": "1", "dataGU": "2026-09-10", "codiceRedazionale": CODE},
}
HTML = (
    "<html><body><PRE>\n"
    "DECRETO-LEGGE 10 settembre 2026, n. 157\n"
    f"{' ' * 30}Art. 1.\n{' ' * 36}Prezzi\n\n  1. Il primo comma.\n  2. Il secondo comma.\n\n"
    f"{' ' * 30}Art. 2.\n\n  1. Entra in vigore il giorno successivo.\n"
    "</PRE></body></html>"
)
SIDECAR = {"codiceRedazionale": CODE, "dataGU": "2026-09-10", "vigenza": "2026-11-11"}
URN = "urn:nir:stato:decreto.legge:2026-09-10;157"


def stage(tmp_path: Path, html: str = HTML, sidecar: dict[str, Any] | None = SIDECAR) -> tuple[Path, Path]:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / f"{CODE}.html").write_bytes(html.encode("utf-8"))
    if sidecar is not None:
        (raw / f"{CODE}.meta.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return raw, tmp_path / "out"


def run(raw: Path, out: Path, item: dict[str, Any] = ITEM, **extra: Any) -> dict[str, Any]:
    return run_convert({"items": [item], "dirIn": str(raw), "dirOut": str(out), **extra}, lambda *_: None)


def test_a_gu_act_becomes_a_single_version_plan_valid_from_the_sidecar_vigenza(tmp_path: Path) -> None:
    raw, out = stage(tmp_path)

    result = run(raw, out)

    plan, act = read_plan(out / f"{STEM}.plan.json")
    assert [u.urn for u in plan.units] == [f"{URN}~art1", f"{URN}~art2"]
    assert plan.versions_count == 1
    assert {(r.valid_from, r.valid_to) for r in plan.version_rows} == {(date(2026, 11, 11), None)}
    assert (act.act_urn, act.kind, act.collection, act.versions_count) == (URN, "gu", "NORMATTIVA", 1)
    assert (act.first_valid_from, act.last_valid_from) == (date(2026, 11, 11), date(2026, 11, 11))
    assert (act.denominazione, act.year, act.number) == ("DECRETO-LEGGE", 2026, 157)
    assert act.source_url is not None
    assert act.source_url.startswith("https://www.gazzettaufficiale.it/")
    assert result["metrics"]["acts"] == 1
    assert result["metrics"]["versions"] == 1


def test_the_source_html_is_stored_as_the_document_and_hashed(tmp_path: Path) -> None:
    raw, out = stage(tmp_path)

    run(raw, out)

    stored = (out / f"{STEM}.akn.xml").read_bytes()
    assert stored == HTML.encode("utf-8")
    info = json.loads((out / f"{STEM}.convert.json").read_text(encoding="utf-8"))
    assert info["sha256"] == hashlib.sha256(stored).hexdigest()
    assert info["version_date"] == "2026-11-11"
    assert info["act_urn"] == URN
    assert info["units"] == 2
    assert info["versions"] == 1


def test_the_item_vigenza_wins_over_the_sidecar(tmp_path: Path) -> None:
    raw, out = stage(tmp_path)
    item = {**ITEM, "meta": {**ITEM["meta"], "vigenza": "2026-12-01"}}

    run(raw, out, item)

    _, act = read_plan(out / f"{STEM}.plan.json")
    assert act.first_valid_from == date(2026, 12, 1)


def test_the_sidecar_identity_wins_over_the_header(tmp_path: Path) -> None:
    raw, out = stage(tmp_path, sidecar={**SIDECAR, "denominazione": "LEGGE", "anno": 1992, "numero": 104, "dataProvvedimento": "1992-02-05",
                                         "titolo": "Legge di test"})

    run(raw, out)

    plan, act = read_plan(out / f"{STEM}.plan.json")
    assert plan.units[0].urn == "urn:nir:stato:legge:1992-02-05;104~art1"
    assert (act.denominazione, act.year, act.number, act.title) == ("LEGGE", 1992, 104, "Legge di test")


def test_a_missing_sidecar_and_no_item_vigenza_fails_the_item(tmp_path: Path) -> None:
    raw, out = stage(tmp_path, sidecar=None)

    with pytest.raises(ConvertError, match="vigenza") as error:
        run(raw, out)

    assert error.value.code == "system_error"


def test_a_missing_html_and_a_jboss_page_are_failed_items(tmp_path: Path) -> None:
    raw, out = stage(tmp_path)
    (raw / f"{CODE}.html").unlink()
    with pytest.raises(ConvertError, match=r"26G00178\.html"):
        run(raw, out)

    (raw / f"{CODE}.html").write_text("<html><head><title>JBoss Web/7 - Error</title></head></html>", encoding="utf-8")
    with pytest.raises(ConvertError, match="JBoss"):
        run(raw, out)
    assert not (out / f"{STEM}.plan.json").exists()


def test_an_act_without_any_unit_is_a_failed_item(tmp_path: Path) -> None:
    raw, out = stage(tmp_path, html="<PRE>\nDECRETO-LEGGE 10 settembre 2026, n. 157\n</PRE>")

    with pytest.raises(ConvertError, match="no units"):
        run(raw, out)


def test_gu_items_need_the_gu_coordinates() -> None:
    bad = {**ITEM, "meta": {"kind": "gu", "actId": ACT_ID}}

    with pytest.raises(ConvertError) as error:
        run_convert({"items": [bad], "dirIn": "a", "dirOut": "b"}, lambda *_: None)

    assert error.value.code == "config_error"
    assert "codiceRedazionale" in str(error.value)
