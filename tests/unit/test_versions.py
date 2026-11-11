"""Zip layout parser and the SCD-2 version plan (ports of ZipCorpusSource and VersionOrchestrator)."""

import base64
import zipfile
from datetime import date
from pathlib import Path

import pytest

from dp_it_normativa_convert.akn import AknDocument, Unit
from dp_it_normativa_convert.versions import (
    ActEntry,
    ActPlan,
    PlanError,
    VersionEntry,
    ZipLayoutError,
    build_plan,
    parse_act_date,
    parse_version_name,
    read_zip,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
BASE = "urn:nir:stato:legge:2000-01-01;1"
D1, D2, D3 = date(2020, 1, 1), date(2021, 6, 1), date(2022, 9, 9)


def act_xml(articles: dict[str, str]) -> str:
    """A minimal standard act: one article (no paragraphs) per entry."""
    body = "".join(f"<article><num>Art. {num}</num><content><p>{text}</p></content></article>" for num, text in articles.items())
    return (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"><act><meta><identification>'
        f'<FRBRalias name="urn:nir" value="{BASE}"/><FRBRalias name="titoloBreve" value="Atto"/>'
        f'<FRBRExpression><FRBRdate date="2020-01-01"/></FRBRExpression></identification></meta><body>{body}</body></act></akomaNtoso>'
    )


def entry(number: int, valid_from: date, articles: dict[str, str], name: str | None = None) -> VersionEntry:
    xml = act_xml(articles)
    return VersionEntry(name or f"x_VIGENZA_{valid_from}_V{number}.xml", number, valid_from, lambda: xml)


def plan_of(*versions: VersionEntry, collection: str = "NORMATTIVA") -> ActPlan:
    return build_plan(ActEntry("LEGGE_20000101_1", list(versions)), collection)


def rows(plan: ActPlan) -> list[tuple[str, date, date | None, str]]:
    return [(r.urn.removeprefix(BASE + "~"), r.valid_from, r.valid_to, r.text_raw) for r in plan.version_rows]


# --- names and zip layout ---------------------------------------------------------------------------------------


def test_original_version_takes_the_act_date_and_vigenza_its_own_date() -> None:
    act_date = date(2000, 7, 27)

    assert parse_version_name("2000-07-31_000G0265_ORIGINALE_V0.xml", act_date) == (0, act_date)
    assert parse_version_name("2000-07-31_000G0265_VIGENZA_2003-01-01_V12.xml", act_date) == (12, date(2003, 1, 1))
    assert parse_version_name("x_originale_v3.XML", act_date) == (3, act_date)


@pytest.mark.parametrize("name", ["readme.txt", "x_VIGENZA_2003-01-01.xml", "x_VIGENZA_2003-13-45_V1.xml", "x_ORIGINALE_VX.xml", ""])
def test_other_names_are_not_versions(name: str) -> None:
    assert parse_version_name(name, date(2000, 1, 1)) is None


def test_act_date_is_the_second_to_last_underscore_token_of_the_directory() -> None:
    assert parse_act_date("LEGGE_20000727_212") == date(2000, 7, 27)
    assert parse_act_date("DECRETO-LEGGE_20260910_157") == date(2026, 9, 10)
    with pytest.raises(ZipLayoutError):
        parse_act_date("LEGGE_notadate_212")
    with pytest.raises(ZipLayoutError):
        parse_act_date("nounderscore")


def test_zip_versions_are_ordered_by_date_then_number_and_other_files_are_counted(tmp_path: Path) -> None:
    path = tmp_path / "a.zip"
    d = "LEGGE_20000101_1"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"{d}/x_VIGENZA_2021-06-01_V3.xml", act_xml({"1": "c"}))
        z.writestr(f"{d}/x_ORIGINALE_V0.xml", act_xml({"1": "a"}))
        z.writestr(f"{d}/x_VIGENZA_2021-06-01_V2.xml", act_xml({"1": "b"}))
        z.writestr(f"{d}/notes.txt", "ignored")
        z.writestr("rootfile.xml", "not in an act dir")
        z.writestr("EMPTY_20000101_2/readme.md", "an act dir without versions")

    contents = read_zip(path)

    assert [a.act_dir for a in contents.acts] == [d]
    assert [(v.number, v.valid_from) for v in contents.acts[0].versions] == [(0, date(2000, 1, 1)), (2, D2), (3, D2)]
    assert contents.ignored_files == 2


def test_zip_entry_content_is_read_lazily_and_base64_content_is_decoded(tmp_path: Path) -> None:
    path = tmp_path / "b.zip"
    xml = act_xml({"1": "testo"})
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("LEGGE_20000101_1/x_ORIGINALE_V0.xml", base64.b64encode(xml.encode("utf-8")).decode("ascii"))
        z.writestr("LEGGE_20000101_1/x_VIGENZA_2021-06-01_V1.xml", "  " + xml)

    versions = read_zip(path).acts[0].versions

    assert versions[0].read_xml() == xml
    assert versions[1].read_xml().strip() == xml


# --- the SCD-2 plan ---------------------------------------------------------------------------------------------


def test_unchanged_unit_stays_one_open_row_and_a_changed_text_closes_at_the_new_date() -> None:
    plan = plan_of(entry(0, D1, {"1": "uno", "2": "due"}), entry(1, D2, {"1": "uno", "2": "DUE"}))

    assert sorted(rows(plan)) == sorted([
        ("art1", D1, None, "uno"),
        ("art2", D1, D2, "due"),
        ("art2", D2, None, "DUE"),
    ])


def test_vanished_unit_is_closed_a_new_unit_opened_and_a_rebirth_gets_its_own_row() -> None:
    plan = plan_of(
        entry(0, D1, {"1": "uno", "3": "tre"}),
        entry(1, D2, {"1": "uno", "4": "quattro"}),
        entry(2, D3, {"1": "uno", "4": "quattro", "3": "tre di nuovo"}),
    )

    assert sorted(rows(plan)) == sorted([
        ("art1", D1, None, "uno"),
        ("art3", D1, D2, "tre"),
        ("art3", D3, None, "tre di nuovo"),
        ("art4", D2, None, "quattro"),
    ])
    assert plan.versions_count == 3
    assert set(plan.seen_urns) == {f"{BASE}~art{n}" for n in (1, 3, 4)}


def test_vigente_units_are_the_last_version_stamped_with_collection_and_the_open_row_date() -> None:
    plan = plan_of(
        entry(0, D1, {"1": "uno", "2": "due", "3": "tre"}),
        entry(1, D2, {"1": "uno", "2": "DUE", "3": "tre"}),
        entry(2, D3, {"1": "uno", "2": "DUE", "3": "tre", "5": "cinque"}),
        collection="CODICI",
    )

    by_urn = {u.urn.removeprefix(BASE + "~"): u for u in plan.units}
    assert {k: v.version_date for k, v in by_urn.items()} == {"art1": D1, "art2": D2, "art3": D1, "art5": D3}
    assert {u.collection for u in plan.units} == {"CODICI"}
    assert {u.act_urn for u in plan.units} == {BASE}
    assert by_urn["art2"].text_raw == "DUE"


def test_a_change_on_the_same_date_overwrites_instead_of_producing_a_duplicate_key() -> None:
    plan = plan_of(entry(0, D1, {"1": "a"}), entry(1, D2, {"1": "b"}), entry(2, D2, {"1": "c"}))

    assert sorted(rows(plan)) == [("art1", D1, D2, "a"), ("art1", D2, None, "c")]
    keys = [(r.urn, r.valid_from) for r in plan.version_rows]
    assert len(keys) == len(set(keys))


def test_a_row_that_would_close_on_its_own_opening_date_is_dropped_and_a_same_date_rebirth_does_not_collide() -> None:
    plan = plan_of(
        entry(0, D1, {"1": "uno"}),
        entry(1, D2, {"1": "uno", "2": "x"}),
        entry(2, D2, {"1": "uno"}),
        entry(3, D2, {"1": "uno", "2": "y"}),
    )

    assert sorted(rows(plan)) == [("art1", D1, None, "uno"), ("art2", D2, None, "y")]
    keys = [(r.urn, r.valid_from) for r in plan.version_rows]
    assert len(keys) == len(set(keys))


def test_duplicate_urns_collapse_with_the_last_one_winning_and_are_counted() -> None:
    xml = act_xml({"1": "uno", "2": "due"}).replace("</body>", "<article><num>Art. 1</num><content><p>uno bis</p></content></article></body>")
    version = VersionEntry("x_ORIGINALE_V0.xml", 0, D1, lambda: xml)

    plan = plan_of(version)

    assert [(u.urn.removeprefix(BASE + "~"), u.text_raw) for u in plan.units] == [("art1", "uno bis"), ("art2", "due")]
    assert plan.dropped_units == 1


def test_units_whose_contextual_text_is_blank_are_dropped_as_in_the_legacy_orchestrator() -> None:
    # the AKN reader always yields "<title> > <text>", so only a reader that returns blank contextual text triggers the rule
    blank = Unit("u1", "", "1", None, "p", None, "", "  ", D1, act_urn=BASE)
    kept = Unit("u2", "t", "2", None, "p", None, "x", "t > x", D1, act_urn=BASE)
    version = VersionEntry("x_ORIGINALE_V0.xml", 0, D1, lambda: "ignored")

    plan = build_plan(ActEntry("LEGGE_20000101_1", [version]), "NORMATTIVA", lambda _: AknDocument("t", BASE, D1, [blank, kept]))

    assert [u.urn for u in plan.units] == ["u2"]
    assert plan.seen_urns == ["u2"]
    assert plan.dropped_units == 1


def test_an_unreadable_version_fails_the_plan_naming_the_file() -> None:
    bad = VersionEntry("x_VIGENZA_2021-06-01_V1.xml", 1, D2, lambda: "<not xml")

    with pytest.raises(PlanError, match=r"x_VIGENZA_2021-06-01_V1\.xml"):
        plan_of(entry(0, D1, {"1": "a"}), bad)


def test_an_act_without_versions_or_a_blank_collection_is_rejected() -> None:
    with pytest.raises(PlanError):
        plan_of()
    with pytest.raises(PlanError):
        plan_of(entry(0, D1, {"1": "a"}), collection=" ")


# --- parity: the real zip of L. 2000/212 (first four versions) -----------------------------------------------------


def test_real_zip_of_legge_212_2000_gives_the_hand_verified_history() -> None:
    contents = read_zip(FIXTURES / "l-2000-212_4versions.zip")

    assert contents.ignored_files == 0
    [act] = contents.acts
    assert act.act_dir == "LEGGE_20000727_212"
    assert [v.valid_from for v in act.versions] == [date(2000, 7, 27), date(2003, 1, 1), date(2005, 8, 10), date(2005, 12, 3)]

    plan = build_plan(act, "NORMATTIVA")

    # reference: per URN, one row per run of identical text over the versions (dates are distinct, so no same-date rules apply)
    assert plan.versions_count == 4
    assert len(plan.units) == 80
    assert len(plan.seen_urns) == 80
    assert len(plan.version_rows) == 83
    assert sum(1 for r in plan.version_rows if r.valid_to is None) == 80
    assert all(r.valid_to is None or r.valid_to > r.valid_from for r in plan.version_rows)
    assert {u.act_urn for u in plan.units} == {"urn:nir:stato:legge:2000-07-27;212"}
