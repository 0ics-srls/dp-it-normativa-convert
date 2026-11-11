"""The ``<actId>.plan.json`` document that convert writes and ingest loads."""

import json
from datetime import date
from pathlib import Path

import pytest

from dp_it_normativa_convert.plan import PLAN_SCHEMA_VERSION, ActRow, PlanFormatError, plan_from_dict, plan_to_dict, read_plan, write_plan
from dp_it_normativa_convert.versions import ActEntry, ActPlan, VersionEntry, build_plan

BASE = "urn:nir:stato:legge:2000-01-01;1"


def sample_xml(text: str) -> str:
    return (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"><act><meta><identification>'
        f'<FRBRalias name="urn:nir" value="{BASE}"/><FRBRalias name="titoloBreve" value="Atto"/>'
        '<FRBRExpression><FRBRdate date="2020-01-01"/></FRBRExpression></identification></meta><body>'
        f"<article><num>Art. 1</num><heading>Rubrica</heading><paragraph><num>1.</num><content><p>{text}</p></content></paragraph></article>"
        "</body></act></akomaNtoso>"
    )


def sample_plan() -> ActPlan:
    versions = [
        VersionEntry("a_ORIGINALE_V0.xml", 0, date(2020, 1, 1), lambda: sample_xml("uno")),
        VersionEntry("a_VIGENZA_2021-02-03_V1.xml", 1, date(2021, 2, 3), lambda: sample_xml("due")),
    ]
    return build_plan(ActEntry("LEGGE_20000101_1", versions), "NORMATTIVA")


ACT = ActRow(
    act_urn=BASE,
    title="Atto",
    denominazione="LEGGE",
    year=2000,
    number=1,
    kind="normattiva",
    collection="NORMATTIVA",
    versions_count=2,
    first_valid_from=date(2020, 1, 1),
    last_valid_from=date(2021, 2, 3),
    units_count=1,
    source_url="https://www.normattiva.it/uri-res/N2Ls?" + BASE,
)


def test_document_has_the_documented_top_level_keys_and_snake_case_fields() -> None:
    document = plan_to_dict(sample_plan(), ACT)

    assert list(document) == ["schema_version", "act", "units", "version_rows", "seen_urns", "versions_count"]
    assert document["schema_version"] == PLAN_SCHEMA_VERSION
    assert document["versions_count"] == 2
    assert document["seen_urns"] == [f"{BASE}~art1-com1"]
    assert document["act"]["act_urn"] == BASE
    assert document["act"]["first_valid_from"] == "2020-01-01"
    assert set(document["units"][0]) == {
        "urn", "act", "act_urn", "article_num", "comma_num", "materialized_path", "rubrica", "text_raw", "text_contextual", "version_date", "collection",
    }
    assert document["units"][0]["version_date"] == "2021-02-03"
    assert document["version_rows"] == [
        {"urn": f"{BASE}~art1-com1", "valid_from": "2020-01-01", "valid_to": "2021-02-03", "text_raw": "uno"},
        {"urn": f"{BASE}~art1-com1", "valid_from": "2021-02-03", "valid_to": None, "text_raw": "due"},
    ]


def test_file_round_trip_keeps_every_field(tmp_path: Path) -> None:
    plan = sample_plan()
    path = tmp_path / "out" / "x.plan.json"

    write_plan(path, plan, ACT)
    loaded_plan, loaded_act = read_plan(path)

    assert loaded_plan == plan
    assert loaded_act == ACT
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == PLAN_SCHEMA_VERSION
    assert plan_from_dict(plan_to_dict(plan, ACT)) == (plan, ACT)


def test_output_is_deterministic_and_keeps_non_ascii_text(tmp_path: Path) -> None:
    versions = [VersionEntry("a_ORIGINALE_V0.xml", 0, date(2020, 1, 1), lambda: sample_xml("perché è così"))]
    plan = build_plan(ActEntry("LEGGE_20000101_1", versions), "NORMATTIVA")
    first, second = tmp_path / "1.json", tmp_path / "2.json"

    write_plan(first, plan, ACT)
    write_plan(second, plan, ACT)

    assert first.read_bytes() == second.read_bytes()
    assert "perché è così" in first.read_text(encoding="utf-8")


def test_a_document_of_another_schema_version_or_shape_is_refused(tmp_path: Path) -> None:
    document = plan_to_dict(sample_plan(), ACT)

    with pytest.raises(PlanFormatError):
        plan_from_dict({**document, "schema_version": PLAN_SCHEMA_VERSION + 1})
    with pytest.raises(PlanFormatError):
        plan_from_dict({k: v for k, v in document.items() if k != "units"})
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(PlanFormatError):
        read_plan(bad)
