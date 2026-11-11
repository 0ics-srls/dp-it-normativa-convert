"""AknReader port: units (urn, comma, rubrica, text) of standard, codici and mixed Akoma Ntoso acts."""

from datetime import date
from pathlib import Path

import pytest

from dp_it_normativa_convert.akn import AknError, Unit, read

FIXTURES = Path(__file__).parent.parent / "fixtures"
DL = "urn:nir:stato:decreto.legge:2026-09-10;157"
DL_TITLE = (
    "Disposizioni urgenti in materia di prezzi petroliferi connessi al protrarsi della nuova crisi dei mercati internazionali "
    "e di infrastrutture e attivita' energetiche strategiche. (26G00178)"
)

NS = 'xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"'


def load(name: str) -> list[Unit]:
    return read((FIXTURES / name).read_text(encoding="utf-8"))


def act(meta: str, body: str = "", attachments: str = "") -> str:
    return f'<akomaNtoso {NS}><act name="x"><meta><identification>{meta}</identification></meta><body>{body}</body>{attachments}</act></akomaNtoso>'


META = (
    '<FRBRExpression><FRBRthis value="urn:nir:stato:test:2026;1"/><FRBRdate date="2026-01-01" name=""/></FRBRExpression>'
    '<FRBRalias name="titoloBreve" value="Atto di Test"/>'
)


# --- standard scheme, real Normattiva export (DL 2026/157) -------------------------------------------------------


def test_standard_act_yields_one_unit_per_paragraph_with_comma_urns() -> None:
    units = load("dl-2026-157_V0.xml")

    assert len(units) == 14
    assert [u.urn for u in units[:6]] == [f"{DL}~art1-com{n}" for n in range(1, 6)] + [f"{DL}~art2-com1"]
    assert {u.article_num for u in units} == {"1", "2", "3", "4"}


def test_base_urn_comes_from_the_urn_nir_alias_not_from_frbrthis() -> None:
    units = load("dl-2026-157_V0.xml")

    assert all(u.urn.startswith("urn:nir:stato:decreto.legge:2026-09-10;157~art") for u in units)


def test_title_falls_back_to_collapsed_doc_title_when_there_is_no_titolo_breve() -> None:
    units = load("dl-2026-157_V0.xml")

    assert {u.act for u in units} == {DL_TITLE}


def test_version_date_is_the_first_date_of_the_frbr_expression() -> None:
    assert {u.version_date for u in load("dl-2026-157_V0.xml")} == {date(2026, 9, 11)}
    assert {u.version_date for u in load("dl-2026-157_V1.xml")} == {date(2026, 11, 10)}


def test_rubrica_is_the_trimmed_heading_and_empty_heading_is_an_empty_string() -> None:
    units = load("dl-2026-157_V0.xml")

    art1 = units[0]
    art2 = units[5]
    art3 = next(u for u in units if u.article_num == "3")
    assert art1.rubrica == "Disposizioni in materia di accisa sul gasolio usato come carburante e di autotrasporto"
    assert art2.rubrica == ""
    assert art3.rubrica == "Disposizioni finanziarie"


def test_paragraph_without_num_gets_its_one_based_index_and_may_collide_with_a_numbered_comma() -> None:
    units = load("dl-2026-157_V0.xml")

    art2 = [u for u in units if u.article_num == "2"]
    assert [u.comma_num for u in art2] == ["1", "1", "2", "3", "4", "5"]
    assert art2[0].urn == art2[1].urn == f"{DL}~art2-com1"
    assert art2[0].text_raw.startswith("Misure di accelerazione dei procedimenti")
    assert art2[1].text_raw.startswith("Nei casi di procedimenti amministrativi")


def test_article_label_variants_are_normalised_to_the_bare_number() -> None:
    units = load("dl-2026-157_V1.xml")

    assert {u.article_num for u in units} == {"1", "1-bis", "2", "2-bis", "2-ter", "3", "4"}
    bis = next(u for u in units if u.article_num == "2-bis")
    assert bis.urn == f"{DL}~art2-bis-com1"
    assert bis.materialized_path == "2026-09-10-157~art2-bis/art2-bis/c1"


def test_heading_with_inline_ins_keeps_the_text_and_the_consolidation_markers() -> None:
    units = load("dl-2026-157_V1.xml")

    bis = next(u for u in units if u.article_num == "1-bis")
    assert bis.rubrica is not None
    assert bis.rubrica.startswith("(( (Modifiche all'articolo 1-decies")
    assert bis.rubrica.endswith("))")


def test_whitespace_in_text_is_collapsed_to_single_spaces() -> None:
    units = load("dl-2026-157_V0.xml")

    assert all("  " not in u.text_raw and "\n" not in u.text_raw and u.text_raw == u.text_raw.strip() for u in units)


def test_text_contextual_is_title_rubrica_text() -> None:
    units = load("dl-2026-157_V0.xml")

    art1 = units[0]
    assert art1.text_contextual == f"{DL_TITLE} > {art1.rubrica} > {art1.text_raw}"
    art2 = units[5]
    assert art2.text_contextual == f"{DL_TITLE} >  > {art2.text_raw}"


# --- standard scheme, hand-checked fixture (Statuto, L. 300/1970) -------------------------------------------------


def test_article_without_paragraphs_is_one_unit_with_no_comma() -> None:
    units = load("legge-300-1970.akn.xml")

    art13 = [u for u in units if u.article_num == "13"]
    assert len(art13) == 1
    assert art13[0].comma_num is None
    assert art13[0].urn == "urn:nir:stato:legge:1970-05-20;300~art13"
    assert art13[0].materialized_path == "1970-05-20-300~art13/art13"


def test_titolo_breve_wins_and_urn_falls_back_to_frbrthis_without_alias() -> None:
    units = load("legge-300-1970.akn.xml")

    assert {u.act for u in units} == {"Statuto dei lavoratori"}
    assert [u.urn for u in units if u.article_num == "18"] == [
        "urn:nir:stato:legge:1970-05-20;300~art18-com1",
        "urn:nir:stato:legge:1970-05-20;300~art18-com2",
    ]


def test_consolidation_markers_are_kept_verbatim_and_version_date_is_read() -> None:
    units = load("legge-300-1970.akn.xml")

    assert any("((licenziamento intimato senza giusta causa))" in u.text_raw for u in units)
    assert {u.version_date for u in units} == {date(2012, 7, 18)}
    art18 = units[-1]
    assert art18.text_contextual.startswith("Statuto dei lavoratori > Reintegrazione nel posto di lavoro > ")


def test_meta_without_title_falls_back_to_the_urn_nir_alias_then_to_empty() -> None:
    meta = '<FRBRalias name="urn:nir" value="urn:nir:stato:test:2026;2"/><FRBRExpression><FRBRdate date="2026-02-03"/></FRBRExpression>'
    body = "<article><num>Art. 1</num><paragraph><content><p>testo</p></content></paragraph></article>"

    assert read(act(meta, body))[0].act == "urn:nir:stato:test:2026;2"
    assert read(act('<FRBRExpression><FRBRdate date="2026-02-03"/></FRBRExpression>', body))[0].act == ""


def test_missing_expression_date_gives_the_minimum_date() -> None:
    body = "<article><num>Art. 1</num><content><p>testo</p></content></article>"

    assert read(act('<FRBRalias name="titoloBreve" value="T"/>', body))[0].version_date == date.min


def test_units_with_empty_text_are_kept_by_the_reader() -> None:
    body = "<article><num>Art. 1</num><paragraph><num>1.</num><content><p/></content></paragraph></article>"

    units = read(act(META, body))

    assert len(units) == 1
    assert units[0].text_raw == ""
    assert units[0].text_contextual == "Atto di Test > "


def test_article_without_a_body_element_is_still_found() -> None:
    xml = (
        f"<akomaNtoso {NS}><act><meta><identification>{META}</identification></meta>"
        "<article><num>Art. 5</num><content><p>solo</p></content></article></act></akomaNtoso>"
    )

    assert [u.urn for u in read(xml)] == ["urn:nir:stato:test:2026;1~art5"]


def test_blank_input_and_documents_without_an_act_are_rejected() -> None:
    with pytest.raises(AknError):
        read("  ")
    with pytest.raises(AknError):
        read(f"<akomaNtoso {NS}><bill/></akomaNtoso>")
    with pytest.raises(AknError):
        read("<not xml")


# --- codici scheme ------------------------------------------------------------------------------------------------


def test_codice_single_attachment_gives_article_scoped_unit_from_the_eid() -> None:
    units = load("codice-civile-art2103.akn.xml")

    assert len(units) == 1
    unit = units[0]
    assert unit.article_num == "2103"
    assert unit.urn == "urn:nir:stato:codice.civile:1942-03-16;262~art2103-com1"
    assert "categoria legale" in unit.text_raw
    assert unit.act == "Codice civile"
    assert unit.version_date == date(2015, 6, 25)


def _attachment(doc_name: str, eid: str | None, frbr: str, text: str) -> str:
    article_attr = f' eId="{eid}"' if eid else ""
    return (
        f'<attachment><doc name="{doc_name}"><meta><identification><FRBRExpression><FRBRthis value="{frbr}"/></FRBRExpression></identification></meta>'
        f"<mainBody><article{article_attr}><num>Art.</num><heading>Rubrica {text}</heading>"
        f"<paragraph><num>1.</num><content><p>{text}</p></content></paragraph></article></mainBody></doc></attachment>"
    )


def test_codice_dominant_partition_is_canonical_and_the_other_partition_keeps_its_attachment_uri() -> None:
    atts = (
        _attachment("CODICE CIVILE-art. 2103", None, "urn:nir:stato:cc:1942;262~art2103", "uno")
        + _attachment("CODICE CIVILE-art. 2104", None, "urn:nir:stato:cc:1942;262~art2104", "due")
        + _attachment("PRELEGGI-art. 1", None, "urn:nir:stato:cc:1942;262:preleggi~art1", "tre")
    )

    units = read(act(META, attachments=atts))

    by_text = {u.text_raw: u for u in units}
    assert by_text["uno"].urn == "urn:nir:stato:test:2026;1~art2103-com1"
    assert by_text["due"].urn == "urn:nir:stato:test:2026;1~art2104-com1"
    assert by_text["tre"].urn == "urn:nir:stato:cc:1942;262:preleggi~art1-com1"
    assert by_text["tre"].article_num == "1"


def test_codice_article_number_comes_from_the_doc_name_when_there_is_no_eid() -> None:
    atts = _attachment("CODICE CIVILE-art. 2103 bis", None, "x", "uno") + _attachment("CODICE CIVILE-art. 7.", None, "x", "due")

    units = read(act(META, attachments=atts))

    assert [u.article_num for u in units] == ["2103-bis", "7"]
    assert [u.urn for u in units] == ["urn:nir:stato:test:2026;1~art2103-bis-com1", "urn:nir:stato:test:2026;1~art7-com1"]


def test_codice_attachment_without_paragraphs_is_one_unit_with_no_comma() -> None:
    att = (
        '<attachment><doc name="CODICE-art. 9"><mainBody><article eId="art9"><num>Art. 9</num><content><p>solo   testo</p></content></article>'
        "</mainBody></doc></attachment>"
    )

    units = read(act(META, attachments=att))

    assert len(units) == 1
    assert units[0].comma_num is None
    assert units[0].urn == "urn:nir:stato:test:2026;1~art9"
    assert units[0].text_raw == "Art. 9solo testo", "an attachment article without content takes every text node, num included"


# --- mixed (body articles and attachments in one document) ---------------------------------------------------------


def test_mixed_act_reads_body_articles_and_attachments() -> None:
    body = (
        '<article eId="art_1"><num>Art. 1</num><paragraph><content><p>testo articolo uno</p></content></paragraph></article>'
        '<article eId="art_2"><num>Art. 2</num><paragraph><content><p>testo articolo due</p></content></paragraph></article>'
    )
    att = (
        '<attachment><meta><identification><FRBRExpression><FRBRthis value="urn:nir:stato:test:2026;1~art7"/>'
        '<FRBRdate date="2026-01-01" name=""/></FRBRExpression></identification></meta>'
        "<paragraph><content><p>testo disposizione allegata</p></content></paragraph></attachment>"
    )

    units = read(act(META, body, att))

    assert [u.urn for u in units] == [
        "urn:nir:stato:test:2026;1~art1-com1",
        "urn:nir:stato:test:2026;1~art2-com1",
        "urn:nir:stato:test:2026;1~art7-com1",
    ]


def test_articles_inside_attachments_are_not_read_by_the_standard_branch() -> None:
    units = load("codice-civile-art2103.akn.xml")

    assert len(units) == 1
