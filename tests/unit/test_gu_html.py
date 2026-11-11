"""GuHtmlReader port (branch 53): Gazzetta Ufficiale print HTML -> article-level units, identity from the grab sidecar."""

from datetime import date
from pathlib import Path

import pytest

from dp_it_normativa_convert.akn import AknError
from dp_it_normativa_convert.gu_html import GuError, GuReport, read, read_document, read_with_report

FIXTURES = Path(__file__).parent.parent / "fixtures" / "gu"
VERSION = date(2026, 11, 11)
META = {"denominazione": "LEGGE", "anno": 1992, "numero": 104, "codiceRedazionale": "092G0108", "dataGU": "1992-02-17",
        "dataProvvedimento": "1992-02-05", "titolo": "Legge di test"}
BASE = "urn:nir:stato:legge:1992-02-05;104"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def pre(body: str) -> str:
    return f"<html><body><PRE>\n{body}\n</PRE></body></html>"


ONE_ARTICLE = pre(f"{' ' * 30}Art. 33.\n\n  1. Primo comma dell'articolo trentatre, con il suo testo disteso.\n\n"
                  "  2. Secondo comma dell'articolo trentatre, con altro testo disteso.")


# --- identity: sidecar first, header as fallback ---------------------------------------------------------------------


def test_identity_comes_from_the_sidecar_not_the_header() -> None:
    html = pre(f"{' ' * 30}Art. 1.\n\n  1. Il testo.")  # no header at all

    units = read(html, META, VERSION)

    assert units[0].urn == f"{BASE}~art1"
    assert units[0].act == "Legge di test"


def test_a_missing_sidecar_falls_back_to_the_header() -> None:
    html = pre("LEGGE 5 febbraio 1992, n. 104\n\n" + f"{' ' * 30}Art. 1.\n\n  1. Il testo.")

    units = read(html, None, VERSION)

    assert units[0].urn == f"{BASE}~art1"


def test_a_sidecar_without_a_usable_identity_falls_back_to_the_header() -> None:
    html = pre("DECRETO-LEGGE 10 settembre 2026, n. 157\n\n" + f"{' ' * 30}Art. 1.\n\n  1. Il testo.")
    grab_only = {"codiceRedazionale": "26G00178", "dataGU": "2026-09-10", "vigenza": "2026-11-11"}

    units = read(html, grab_only, VERSION)

    assert units[0].urn == "urn:nir:stato:decreto.legge:2026-09-10;157~art1"


def test_neither_sidecar_nor_header_is_an_error() -> None:
    with pytest.raises(GuError, match="not identifiable"):
        read(pre(f"{' ' * 30}Art. 1.\n\n  1. Il testo."), None, VERSION)


def test_the_jboss_error_page_is_an_error_not_an_act() -> None:
    page = "<html><head><title>JBoss Web/7.5.7 - Error report</title></head><body>500</body></html>"

    with pytest.raises(GuError, match="JBoss"):
        read(page, META, VERSION)


def test_gu_errors_are_akn_errors_so_convert_reports_them_per_item() -> None:
    assert issubclass(GuError, AknError)


def test_the_document_carries_title_base_urn_and_version_date() -> None:
    document = read_document(ONE_ARTICLE, META, VERSION)

    assert (document.title, document.base_urn, document.version_date) == ("Legge di test", BASE, VERSION)
    assert all(u.act_urn == BASE and u.version_date == VERSION for u in document.units)


# --- article structure -----------------------------------------------------------------------------------------------


def test_an_article_is_one_unit_with_all_its_commas_and_no_comma_number() -> None:
    units = read(ONE_ARTICLE, META, VERSION)

    assert len(units) == 1
    assert units[0].comma_num is None
    assert units[0].urn == f"{BASE}~art33"
    assert "Primo comma" in units[0].text_raw
    assert "Secondo comma" in units[0].text_raw


def test_the_dot_opens_a_comma_the_parenthesis_does_not() -> None:
    html = pre(
        f"{' ' * 30}Art. 1.\n{' ' * 40}La rubrica\n\n  1. Il primo comma dice questo:\n    a) prima voce dell'elenco;\n"
        "    b) seconda voce dell'elenco.\n  2. Il secondo comma."
    )

    units = read(html, META, VERSION)

    assert len(units) == 1
    assert "a) prima voce" in units[0].text_raw
    assert "Il primo comma" in units[0].text_raw
    assert "Il secondo comma" in units[0].text_raw


def test_a_centred_short_line_after_the_header_is_the_rubrica_and_not_part_of_the_text() -> None:
    html = pre(f"{' ' * 30}Art. 3.\n{' ' * 20}Persona con disabilita' avente diritto ai sostegni\n\n  1. Il testo del comma.")

    units = read(html, META, VERSION)

    assert units[0].rubrica == "Persona con disabilita' avente diritto ai sostegni"
    assert not units[0].text_raw.startswith("Persona con")
    assert units[0].text_contextual == "Legge di test > Persona con disabilita' avente diritto ai sostegni > " + units[0].text_raw


def test_old_acts_without_a_rubrica_keep_their_text() -> None:
    units = read(fixture("gu-paragrafi-non-numerati.l1790-1952.html"), None, VERSION)

    assert len(units) == 1
    assert units[0].rubrica is None
    assert units[0].text_raw.startswith("Il decreto legislativo 2 marzo 1948, n. 135")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Art. 5-quinquiesdecies.", "5-quinquiesdecies"),
        ("Art. 7-duodevicies.", "7-duodevicies"),
        ("Art. 2-bis.", "2-bis"),
        ("Art. 10 quinquies", "10-quinquies"),  # a space-separated suffix still attaches with a hyphen
        ("Articolo 12", "12"),
    ],
)
def test_latin_suffixes_are_recognised_and_normalised(header: str, expected: str) -> None:
    units = read(pre(f"{' ' * 30}{header}\n\n  1. Il testo del comma."), META, VERSION)

    assert [u.article_num for u in units] == [expected]


def test_articolo_unico_is_article_1() -> None:
    units = read(fixture("gu-articolo-unico.l1005-1950.html"), None, VERSION)

    assert len(units) == 1
    assert units[0].article_num == "1"
    assert units[0].urn.endswith("~art1")


def test_an_act_without_article_headers_is_article_1_from_the_enacting_formula() -> None:
    units = read(fixture("gu-senza-header-articolo.l1213-1960.html"), None, VERSION)

    assert units
    assert {u.article_num for u in units} == {"1"}
    assert not any("munita del sigillo" in u.text_raw.lower() for u in units)


def test_an_abrogated_comma_stays_a_unit_with_its_text() -> None:
    html = pre(f"{' ' * 30}Art. 9.\n\n  ((COMMA ABROGATO DAL D.LGS. 13 DICEMBRE 2010, N. 212))")

    units = read(html, META, VERSION)

    assert len(units) == 1
    assert "COMMA ABROGATO DAL" in units[0].text_raw


# --- text normalisation ----------------------------------------------------------------------------------------------


def test_justified_text_is_dewrapped_and_inline_tags_are_stripped() -> None:
    html = pre(
        f"{' ' * 30}Art. 3.\n\n  1. Si intende persona con <em>durature</em>  compromissioni\n"
        "  della <strong>capacita'</strong> &amp; della funzione (31) ((lavorativa))."
    )

    units = read(html, META, VERSION)

    assert units[0].text_raw == "1. Si intende persona con durature compromissioni della capacita' & della funzione lavorativa."


def test_note_only_lines_are_not_text() -> None:
    html = pre(f"{' ' * 30}Art. 3.\n\n  1. Il testo vero.\n  (31) ((32))\n")

    assert read(html, META, VERSION)[0].text_raw == "1. Il testo vero."


def test_the_editorial_aggiornamento_block_goes_to_a_separate_agg_unit() -> None:
    html = pre(f"{' ' * 30}Art. 47.\n\n  1. La norma resta.\n  ---------------\n  AGGIORNAMENTO (4)\n  Il D.L. 9 settembre 1997 ha disposto.")

    units = read(html, META, VERSION)

    norm = next(u for u in units if u.comma_num is None)
    agg = next(u for u in units if u.comma_num == "agg")
    assert "AGGIORNAMENTO" not in norm.text_raw
    assert agg.urn == f"{BASE}~art47-agg"
    assert agg.text_raw.startswith("AGGIORNAMENTO")


def test_the_same_aggiornamento_note_is_kept_once_per_act() -> None:
    block = "  1. Norma {n}.\n  ---------------\n  AGGIORNAMENTO (4)\n  Il D.L. 9 settembre 1997 ha disposto."
    html = pre(f"{' ' * 30}Art. 1.\n\n{block.format(n=1)}\n{' ' * 30}Art. 2.\n\n{block.format(n=2)}")

    units = read(html, META, VERSION)

    assert [u.urn for u in units if u.comma_num == "agg"] == [f"{BASE}~art1-agg"]


# --- containers (annex / agreement) ----------------------------------------------------------------------------------


ANNEX = pre(
    f"{' ' * 30}Art. 1.\n\n  1. Articolo uno del corpo della legge.\n\n{' ' * 30}ANNESSO\n\n{' ' * 30}Art. I.\n\n"
    "  1. Primo articolo dell'accordo allegato."
)


def test_roman_article_numbers_inside_a_container_are_articles_with_the_container_in_the_urn() -> None:
    units = read(ANNEX, META, VERSION)

    annex = next(u for u in units if "~allegato1-art" in u.urn)
    assert annex.article_num == "I"
    assert annex.urn == f"{BASE}~allegato1-artI"
    assert annex.comma_num is None
    assert {u.article_num for u in units if "allegato" not in u.urn} == {"1"}


def test_an_arabic_article_after_a_table_still_belongs_to_the_act() -> None:
    html = pre(
        f"{' ' * 30}Art. 1.\n\n  1. Il primo articolo.\n\n{' ' * 40}TABELLA A\n  ==========================================\n"
        f"  voce      |   importo\n  ==========================================\n\n{' ' * 30}Art. 2.\n\n  1. Il secondo articolo, dopo la tabella."
    )

    units = read(html, META, VERSION)

    assert any(u.article_num == "2" and "allegato" not in u.urn for u in units)


def test_everything_after_the_closing_seal_formula_is_a_container_not_the_act() -> None:
    html = pre(
        f"{' ' * 30}Art. 1.\n\n  1. Il corpo della legge.\n\n  La presente legge, munita del sigillo dello Stato, sara' inserita.\n"
        "  Data a Roma, addi' 5 febbraio 1992\n\n"
        f"{' ' * 30}Art. 1.\n\n  1. Primo articolo del trattato.\n"
    )

    units = read(html, META, VERSION)

    assert [u.urn for u in units] == [f"{BASE}~art1", f"{BASE}~allegato1-art1"]
    assert not any("sigillo" in u.text_raw for u in units)


def test_the_report_counts_what_the_reader_saw_including_the_unrecognised() -> None:
    units, report = read_with_report(ANNEX, META, VERSION)

    assert isinstance(report, GuReport)
    assert report.articles == 2
    assert report.units == len(units)
    assert report.containers == 1

    stray = pre("  Testo che non appartiene ad alcun articolo, prima di qualunque header.\n  Neanche questa riga.")
    units, report = read_with_report(stray, META, VERSION)
    assert units == []
    assert report.unrecognized == 2


def test_a_table_fixture_keeps_its_rows_and_counts_the_tables() -> None:
    units, report = read_with_report(fixture("gu-con-tabella.l467-1949.html"), None, VERSION)

    assert len(units) == 4
    assert any("|" in u.text_raw for u in units)
    assert report.tables > 0
