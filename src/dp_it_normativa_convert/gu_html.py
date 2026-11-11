"""Gazzetta Ufficiale print HTML -> article-level units (port of the branch-53 ``GuHtmlReader``).

The print is flat ``<PRE>`` text justified at ~70 columns, so the rules are positional: an article header line, a centred short rubrica,
a comma that opens with ``N.`` (never ``N)``), containers (annex, agreement, treaty) that restart the numbering, and the seal formula
that ends the body of the law. The identity of the act comes from the grab sidecar when it knows it, else from the page header. One unit per
article (the comma number inside the flat text is ambiguous), plus a ``~artN-agg`` unit for the editorial AGGIORNAMENTO chronicle.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Any

from dp_it_normativa_convert.akn import AknDocument, AknError, Unit, create_unit

JBOSS_MARKER = "<title>JBoss"
AGG = "agg"

_SUFFIXES = (
    "semel|bis|ter|quater|quinquies|sexies|septies|octies|novies|decies|undecies|duodecies|"
    "terdecies|quaterdecies|quinquiesdecies|sexiesdecies|septiesdecies|duodevicies|undevicies|vicies"
)
_FLAGS = re.IGNORECASE
_PRE = re.compile(r"<PRE[^>]*>(.*?)</PRE>", re.DOTALL | _FLAGS)
_TAG = re.compile(r"<[^>]+>")
_HEADER = re.compile(
    rf"^\s*(?:Art\.|Articolo)\s*(?P<num>\d+(?:\.\d+)?|[IVXLC]+)(?P<suf>(?:\s*[-\s]\s*(?:{_SUFFIXES}))*)\s*\.?\s*$", _FLAGS
)
_HEADER_UNICO = re.compile(r"^\s*Articolo\s+unico\s*\.?\s*$", _FLAGS)
_COMMA_START = re.compile(rf"^\s{{0,6}}\(*\s*\d{{1,3}}(?:\s*-\s*(?:{_SUFFIXES}))?\.\s", _FLAGS)
_NOVELLA_MARKERS = re.compile(r"\(\(|\)\)")
_NOTE_REFS = re.compile(r"\(\s*\d+\s*\)")
_ONLY_NOTE_REFS = re.compile(r"^[\s()]*(?:\(+\s*\d+\s*\)+[\s()]*)+$")
_HEADING = re.compile(r"(LEGGE|DECRETO-LEGGE)\s+(\d{1,2})\s+([a-zà-ù]+)\s+(\d{4}),?\s*n\.\s*(\d+)", _FLAGS)
_CONTAINER_OPEN = re.compile(r"^\s*(allegat[oi]|anness[oi]|tabell[ae]|preambolo|accordo|convenzione|protocollo|statuto|trattato)\b", _FLAGS)
_TABLE_ROW = re.compile(r"[=\-]{10,}")
_ROMAN = re.compile(r"^[IVXLC]+$", _FLAGS)
_LAW_CLOSING = re.compile(r"presente\s+(legge|decreto)[^.]{0,40}munita\s+del\s+sigillo", _FLAGS)
_PRINT_APPARATUS = re.compile(
    r"^\s*(nella\s+Raccolta|italiana\.|E'\s+fatto\s+obbligo|osservare\s+come\s+legge|Data\s+a\s+|Visto,\s+il\s+Guardasigilli)", _FLAGS
)
_WHITESPACE = re.compile(r"\s+")
_UPDATE_MARKER = re.compile(r"\s*-{3,}\s*AGGIORNAMENTO\s*\(+\s*\d|\s+AGGIORNAMENTO\s*\(+\s*\d+[a-z]*\s*\)+", _FLAGS)
_ONLY_NUMBERING = re.compile(r"^(?:\d+[.)]|art\.?\s*\d+[a-z-]*\.?|n\.)$", _FLAGS)
_ENACTING = "la seguente legge:"
_MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]

_RUBRICA_MIN_INDENT = 8
_RUBRICA_MAX_LEN = 90
_CONTAINER_MIN_INDENT = 5
_CONTAINER_MAX_LEN = 60


class GuError(AknError):
    """The page is not a readable Gazzetta Ufficiale act print."""


@dataclass(frozen=True)
class GuReport:
    """What the reader saw: what is not counted is a hole nobody sees (``unrecognized`` is text that fits no article)."""

    articles: int
    units: int
    containers: int
    tables: int
    unrecognized: int


@dataclass
class _Article:
    container: str | None
    num: str
    rubrica: str | None = None
    commas: list[str] = field(default_factory=list)
    updates: list[str] = field(default_factory=list)


def _today() -> date:
    return datetime.now(UTC).date()


# --- helpers ---------------------------------------------------------------------------------------------------------


def has_content(text: str | None) -> bool:
    """Not blank and not bare numbering once the ``(( ))`` layout markers are removed (legacy ``Contenuto.HaContenuto``)."""
    if not text or not text.strip():
        return False
    clean = text.replace("((", " ").replace("))", " ").strip()
    return bool(clean) and not _ONLY_NUMBERING.match(clean)


def split_update(text: str) -> tuple[str, str | None]:
    """Cut at the first editorial AGGIORNAMENTO marker: (norm, chronicle with the marker kept in front)."""
    found = _UPDATE_MARKER.search(text)
    if found is None:
        return text, None
    tail = text[found.start() :].lstrip()
    return text[: found.start()].rstrip(), tail.lstrip("-").lstrip()


def _normalize(raw: str) -> str:
    text = _NOTE_REFS.sub("", _NOVELLA_MARKERS.sub("", raw))
    return _WHITESPACE.sub(" ", text).strip()


def _pre_text(page: str) -> str:
    return "\n".join(html_lib.unescape(_TAG.sub("", block)) for block in _PRE.findall(page))


def _title(page: str) -> str:
    first = _PRE.search(page)
    return "" if first is None else _normalize(html_lib.unescape(first.group(1)))


def _base_urn(denominazione: str, day: date, number: int) -> str:
    kind = "legge" if denominazione.strip().upper() == "LEGGE" else "decreto.legge"
    return f"urn:nir:stato:{kind}:{day.isoformat()};{number}"


def _from_sidecar(meta: dict[str, Any] | None) -> tuple[str, str] | None:
    if not meta:
        return None
    number, given = meta.get("numero"), meta.get("dataProvvedimento")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0 or not isinstance(given, str):
        return None
    try:
        day = date.fromisoformat(given)
    except ValueError:
        return None
    denominazione = meta.get("denominazione")
    title = meta.get("titolo")
    kind = denominazione if isinstance(denominazione, str) and denominazione else "LEGGE"
    return _base_urn(kind, day, number), title if isinstance(title, str) else ""


def _from_header(page: str) -> tuple[str, str] | None:
    found = _HEADING.search(html_lib.unescape(_TAG.sub(" ", page)))
    if found is None:
        return None
    month = _MONTHS.index(found.group(3).lower()) + 1 if found.group(3).lower() in _MONTHS else 0
    if month == 0:
        return None
    try:
        day = date(int(found.group(4)), month, int(found.group(2)))
    except ValueError:
        return None
    return _base_urn(found.group(1), day, int(found.group(5))), _title(page)


def _identity(page: str, meta: dict[str, Any] | None) -> tuple[str, str]:
    identity = _from_sidecar(meta) or _from_header(page)
    if identity is None:
        msg = "act not identifiable: neither the sidecar nor the heading of the page says which act it is"
        raise GuError(msg)
    return identity


# --- article structure -----------------------------------------------------------------------------------------------


class _Scanner:
    """The line-by-line state machine of the flat print (one instance per page)."""

    def __init__(self) -> None:
        self.articles: list[_Article] = []
        self.current: _Article | None = None
        self.buffer: list[str] = []
        self.container: str | None = None
        self.closed = False
        self.containers = 0
        self.tables = 0
        self.unrecognized = 0

    def close_comma(self) -> None:
        if self.current is not None and self.buffer:
            # split on the RAW text before normalising: normalising strips the note references and would hide the "(6)" of the marker
            body, update = split_update(" ".join(line.strip() for line in self.buffer))
            text = _normalize(body)
            if text:
                self.current.commas.append(text)
            if update is not None:
                self.current.updates.append(_normalize(update))
        self.buffer.clear()

    def open_container(self) -> None:
        self.containers += 1
        self.container = f"allegato{self.containers}"

    def scan(self, lines: list[str]) -> None:
        for index, line in enumerate(lines):
            if _TABLE_ROW.search(line) or "|" in line:
                self.tables += 1
            if self._closing(lines, index, line) or self._container_title(line) or self._article_header(line):
                continue
            self._body_line(line)
        self.close_comma()

    def _closing(self, lines: list[str], index: int, line: str) -> bool:
        """The seal formula ends the body of the law; it may wrap onto the next line, and the head of the line is still norm."""
        if self.closed:
            return False
        window = f"{line} {lines[index + 1]}" if index + 1 < len(lines) else line
        found = _LAW_CLOSING.search(window)
        if found is None or found.start() >= len(line):
            return False
        if found.start() > 0:
            self.buffer.append(line[: found.start()].rstrip())
        self.close_comma()
        self.closed = True
        self.open_container()
        self.current = None
        return True

    def _container_title(self, line: str) -> bool:
        """A container title is centred and short; the bare keyword also starts ordinary justified lines ("Accordo, ivi incluse ...")."""
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if not (_CONTAINER_OPEN.match(line) and indent >= _CONTAINER_MIN_INDENT and len(line.strip()) <= _CONTAINER_MAX_LEN):
            return False
        self.close_comma()
        # an still-empty container does not open a second one: after the seal the title ("ANNESSO") is the same thing
        if self.container is None or self.current is not None:
            self.open_container()
        self.current = None
        return True

    def _article_header(self, line: str) -> bool:
        header = _HEADER.match(line)
        unico = _HEADER_UNICO.match(line) is not None
        if header is None and not unico:
            return False
        self.close_comma()
        number = "1"
        written = header.group("num") if header is not None else ""
        if header is not None and not unico:
            suffix = re.sub(r"[\s.\-]+", "-", header.group("suf").strip().lower()).strip("-")
            number = written if not suffix else f"{written}-{suffix}"
        # inside a container only Roman articles stay (the annexed agreement); an Arabic "Art. N" says the body of the law resumed,
        # unless the seal already closed it: then an Arabic "Art. 1" is article 1 of the annexed text
        if self.container is not None and not self.closed and not _ROMAN.match(written):
            self.container = None
        self.current = _Article(self.container, number)
        self.articles.append(self.current)
        return True

    def _body_line(self, line: str) -> None:
        current = self.current
        if current is None:
            if line.strip():
                self.unrecognized += 1  # text that belongs to no article is not dropped silently: it is counted
            return
        # the rubrica is CENTRED and short; "the first line after the header" would eat the text of old acts that have none
        if (
            current.rubrica is None
            and not current.commas
            and not self.buffer
            and line.strip()
            and not _COMMA_START.match(line)
            and len(line) - len(line.lstrip()) >= _RUBRICA_MIN_INDENT
            and len(line.strip()) <= _RUBRICA_MAX_LEN
            and not line.lstrip().startswith("((")
        ):
            current.rubrica = _WHITESPACE.sub(" ", line.strip())
            return
        if _ONLY_NOTE_REFS.match(line):
            return
        if _COMMA_START.match(line):
            self.close_comma()
        self.buffer.append(line)


def _without_enacting_formula(scanner: _Scanner, text: str) -> None:
    """Some acts declare no article: the text starts after "la seguente legge:" (rule 6), with the same seal cut as the other road."""
    start = text.lower().find(_ENACTING)
    if start < 0:
        return
    body = _Article(None, "1")
    scanner.current = body
    scanner.buffer.clear()
    after_seal: list[str] = []
    sealed = False
    for line in text[start + len(_ENACTING) :].split("\n"):
        if not sealed and _LAW_CLOSING.search(line):
            scanner.close_comma()
            sealed = True
            continue
        if sealed:
            after_seal.append(line)
            continue
        if _COMMA_START.match(line):
            scanner.close_comma()
        scanner.buffer.append(line)
    scanner.close_comma()
    if body.commas:
        scanner.articles.append(body)
    if sealed:
        # what follows the seal is not thrown away: in a ratification law it holds the treaty, kept as a container
        annex = _Article("allegato1", "1")
        scanner.current = annex
        scanner.buffer.clear()
        scanner.buffer.extend(line for line in after_seal if not _PRINT_APPARATUS.match(line))
        scanner.close_comma()
        if annex.commas:
            scanner.articles.append(annex)


def _articles(text: str) -> _Scanner:
    scanner = _Scanner()
    scanner.scan(text.split("\n"))
    if not scanner.articles:
        _without_enacting_formula(scanner, text)
    for article in scanner.articles:
        if not article.commas and article.rubrica is not None:  # an article without commas does not exist: its only line IS the text
            article.commas, article.rubrica = [article.rubrica], None
    scanner.articles = [a for a in scanner.articles if a.commas]
    return scanner


# --- units -----------------------------------------------------------------------------------------------------------


def _unit(context: tuple[str, date], article_urn: str, article: _Article, comma: str | None, text: str) -> Unit:
    title, version = context
    unit = create_unit(title, article_urn, version, article.num, comma, article.rubrica, text)
    if comma != AGG:
        return unit
    # the chronicle partition is addressed ~artN-agg (not ~artN-comagg)
    return replace(unit, urn=f"{article_urn}-{AGG}", materialized_path=f"{unit.materialized_path.rsplit('/c', 1)[0]}/{AGG}")


def _keep_one_note_per_act(units: list[Unit]) -> list[Unit]:
    """The same editorial note is repeated inside every article of the source: keep the first copy per act (case and spacing ignored)."""
    seen: set[tuple[str, str]] = set()
    kept: list[Unit] = []
    for unit in units:
        if not unit.urn.endswith(f"-{AGG}"):
            kept.append(unit)
            continue
        key = (unit.act, _WHITESPACE.sub(" ", unit.text_raw).strip().lower())
        if not unit.act or key not in seen:
            seen.add(key)
            kept.append(unit)
    return kept


def read_with_report(page: str, meta: dict[str, Any] | None = None, version_date: date | None = None) -> tuple[list[Unit], GuReport]:
    """The units of the page and the count of what the reader saw (no per-act de-duplication of the notes, see :func:`read`)."""
    units, report, _, _ = _read(page, meta, version_date)
    return units, report


def _read(page: str, meta: dict[str, Any] | None, version_date: date | None) -> tuple[list[Unit], GuReport, str, str]:
    if JBOSS_MARKER in page:
        msg = "JBoss error page served with HTTP 200: it is not an act and must not enter the corpus"
        raise GuError(msg)
    base_urn, title = _identity(page, meta)
    version = version_date or _today()
    scanner = _articles(_pre_text(page))

    units: list[Unit] = []
    for article in scanner.articles:
        fragment = f"art{article.num}" if article.container is None else f"{article.container}-art{article.num}"
        article_urn = f"{base_urn}~{fragment}"
        units.append(_unit((title, version), article_urn, article, None, "\n\n".join(article.commas)))
        if article.updates:
            units.append(_unit((title, version), article_urn, article, AGG, "\n\n".join(article.updates)))
    good = [u for u in units if has_content(u.text_raw)]
    report = GuReport(len(scanner.articles), len(good), scanner.containers, scanner.tables, scanner.unrecognized)
    return good, report, base_urn, title


def read_document(page: str, meta: dict[str, Any] | None = None, version_date: date | None = None) -> AknDocument:
    """The act as the same document the Akoma Ntoso reader returns, so ``build_plan`` can take it as a one-version act."""
    version = version_date or _today()
    units, _, base_urn, title = _read(page, meta, version)
    return AknDocument(title, base_urn, version, [replace(u, act_urn=base_urn) for u in _keep_one_note_per_act(units)])


def read(page: str, meta: dict[str, Any] | None = None, version_date: date | None = None) -> list[Unit]:
    """The units of the page, the editorial note kept once per act."""
    return read_document(page, meta, version_date).units
