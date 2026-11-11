"""Akoma Ntoso 3.0 reader: port of the legacy ``LexCorpus.Akn.AknReader`` and ``Unit.Create``.

An act becomes a flat list of :class:`Unit` (comma granularity). Two layouts are read in one document: the standard
``eId`` layout (articles in ``body``) and the codici layout (one article per ``attachment``). Behaviour is kept
identical to the legacy reader on purpose (the phase 83 parity run compares both), including its quirks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_WHITESPACE = re.compile(r"\s+")
_ART_LABEL = re.compile(r"^(art\.?|articolo)\s*", re.IGNORECASE)
_ART_IN_URN = re.compile(r"~art(\d+\w*)")
_ART_IN_EID = re.compile(r"^art_?(.+)$")
_DOC_NAME_ART = re.compile(r"^(?P<pre>.*?)[\s-]*art\.?\s*(?P<num>[\w\.\s-]+)$", re.IGNORECASE)


class AknError(ValueError):
    """The document is not a readable Akoma Ntoso act."""


@dataclass(frozen=True)
class Unit:
    """One comma (or an article when it has no commas) with every derived field."""

    urn: str
    act: str
    article_num: str
    comma_num: str | None
    materialized_path: str
    rubrica: str | None
    text_raw: str
    text_contextual: str
    version_date: date
    act_urn: str = ""
    collection: str | None = None


def create_unit(  # noqa: PLR0913, PLR0917 - the legacy Unit.Create signature
    act_title: str,
    article_urn: str,
    version_date: date,
    article_num: str,
    comma_num: str | None,
    rubrica: str | None,
    text_raw: str,
) -> Unit:
    """Derive urn, path and contextual text so they cannot diverge (legacy ``Unit.Create``)."""
    urn = article_urn + ("" if comma_num is None else f"-com{comma_num}")
    contextual = f"{act_title} > {'' if rubrica is None else rubrica + ' > '}{text_raw}"
    return Unit(
        urn=urn,
        act=act_title,
        article_num=article_num,
        comma_num=comma_num,
        materialized_path=_build_path(article_urn, article_num, comma_num),
        rubrica=rubrica,
        text_raw=text_raw,
        text_contextual=contextual,
        version_date=version_date,
    )


def _build_path(article_urn: str, article_num: str, comma_num: str | None) -> str:
    tail = article_urn[article_urn.rfind(":") + 1 :] if ":" in article_urn else article_urn
    slug = tail.replace(";", "-")
    return f"{slug}/art{article_num}" + ("" if comma_num is None else f"/c{comma_num}")


def _q(name: str) -> str:
    return f"{{{AKN_NS}}}{name}"


def _descendants(element: Element, name: str) -> list[Element]:
    """Descendants (not the element itself) with the given local name, in document order."""
    return [e for e in element.iter(_q(name)) if e is not element]


def _first(element: Element, name: str) -> Element | None:
    found = _descendants(element, name)
    return found[0] if found else None


def _value(element: Element | None) -> str | None:
    """Concatenated text of an element (``XElement.Value``)."""
    return None if element is None else "".join(element.itertext())


@dataclass(frozen=True)
class AknDocument:
    """The meta of an act together with its units; every unit carries the base urn of the act as ``act_urn``."""

    title: str
    base_urn: str
    version_date: date
    units: list[Unit]


def read(xml: str) -> list[Unit]:
    """Read an act into units (see :func:`read_document`)."""
    return read_document(xml).units


def read_document(xml: str) -> AknDocument:
    """Read an act into units. Body articles and attachment articles are both read, never either/or."""
    if not xml or not xml.strip():
        raise AknError("empty document")
    try:
        root = ElementTree.fromstring(xml)
    except ParseError as error:
        raise AknError(f"not well-formed XML: {error}") from error
    acts = list(root.iter(_q("act")))
    if not acts:
        raise AknError("no act element")
    act = acts[0]
    parents = {child: parent for parent in root.iter() for child in parent}
    try:
        title, base_urn, version_date = _read_meta(act)
    except ValueError as error:
        raise AknError(f"unreadable meta: {error}") from error
    units = [*_read_standard(act, parents, title, base_urn, version_date), *_read_codice(act, title, base_urn, version_date)]
    return AknDocument(title, base_urn, version_date, [replace(u, act_urn=base_urn) for u in units])


def _read_meta(act: Element) -> tuple[str, str, date]:
    expression = _first(act, "FRBRExpression")
    nir_alias = _alias(act, "urn:nir")
    base_urn = nir_alias
    if base_urn is None:
        this = _first(expression, "FRBRthis") if expression is not None else None
        this = this if this is not None else _first(act, "FRBRthis")
        base_urn = this.get("value") if this is not None else None
    base_urn = base_urn or ""
    frbr_date = _first(expression, "FRBRdate") if expression is not None else None
    date_text = frbr_date.get("date") if frbr_date is not None else None
    version_date = date.fromisoformat(date_text) if date_text is not None else date.min
    title = _alias(act, "titoloBreve") or _doc_title(act) or nir_alias or ""
    return title, base_urn, version_date


def _alias(act: Element, name: str) -> str | None:
    for alias in _descendants(act, "FRBRalias"):
        if alias.get("name") == name:
            return alias.get("value")
    return None


def _doc_title(act: Element) -> str | None:
    text = _value(_first(act, "docTitle"))
    if text is None or not text.strip():
        return None
    return _WHITESPACE.sub(" ", text).strip()


def _text_of(element: Element) -> str:
    """Text of the content of a paragraph or article, collapsing whitespace; ``((...))`` markers stay verbatim."""
    content = element.find(_q("content"))
    target = content if content is not None else element
    return _WHITESPACE.sub(" ", "".join(target.itertext())).strip()


def _normalize_num(raw: str | None) -> str | None:
    """Strip a leading "Art."/"Articolo" label and surrounding punctuation: "18", "2103-bis", "1"."""
    if raw is None or not raw.strip():
        return None
    stripped = _ART_LABEL.sub("", raw.strip()).strip().strip(". ()")
    return stripped or None


def _read_standard(act: Element, parents: dict[Element, Element], title: str, base_urn: str, version_date: date) -> list[Unit]:
    body = act.find(_q("body"))
    candidates = _descendants(body, "article") if body is not None else _descendants(act, "article")
    articles = [a for a in candidates if not _inside(a, "attachment", parents)]
    units: list[Unit] = []
    for article in articles:
        art_num = _normalize_num(_value(article.find(_q("num")))) or ""
        heading = _value(article.find(_q("heading")))
        rubrica = None if heading is None else heading.strip()
        article_urn = f"{base_urn}~art{art_num}"
        units.extend(_paragraph_units(article, article.findall(_q("paragraph")), title, article_urn, version_date, art_num, rubrica))
    return units


def _paragraph_units(  # noqa: PLR0913, PLR0917 - mirrors the legacy per-article loop
    owner: Element, paragraphs: list[Element], title: str, article_urn: str, version_date: date, art_num: str, rubrica: str | None
) -> list[Unit]:
    # standard layout: direct paragraph children of the article; codici layout: every paragraph below the attachment
    if not paragraphs:
        return [create_unit(title, article_urn, version_date, art_num, None, rubrica, _text_of(owner))]
    units: list[Unit] = []
    for index, paragraph in enumerate(paragraphs, start=1):
        comma = _normalize_num(_value(paragraph.find(_q("num")))) or str(index)
        units.append(create_unit(title, article_urn, version_date, art_num, comma, rubrica, _text_of(paragraph)))
    return units


def _inside(element: Element, name: str, parents: dict[Element, Element]) -> bool:
    current = parents.get(element)
    while current is not None:
        if current.tag == _q(name):
            return True
        current = parents.get(current)
    return False


def _read_codice(act: Element, title: str, base_urn: str, version_date: date) -> list[Unit]:
    attachments = _descendants(act, "attachment")
    if not attachments:
        return []

    # Multi-partition acts reuse article numbers across partitions under one act URN: only the DOMINANT partition (most
    # attachments) gets canonical ~artN URNs; the others keep their own attachment URI (searchable, not citable).
    parsed: list[tuple[Element, str, str | None]] = []
    for att in attachments:
        partition, name_num = _doc_name_parts(att)
        art_num = (
            _article_num_from_eid(att)
            or name_num
            or _article_num_from_urn(_attachment_urn(att) or "")
            or _normalize_num(_value(_first(att, "num")))
        )
        parsed.append((att, partition, art_num))

    counts: dict[str, int] = {}
    for _, partition, _ in parsed:
        counts[partition] = counts.get(partition, 0) + 1
    # most attachments wins; a tie keeps the partition seen first (stable, like LINQ OrderByDescending)
    dominant = max(counts, key=lambda p: counts[p])

    units: list[Unit] = []
    for att, partition, art_num in parsed:
        canonical = partition == dominant and art_num is not None
        article_urn = f"{base_urn}~art{art_num}" if canonical else (_attachment_urn(att) or base_urn)
        heading = _value(_first(att, "heading"))
        rubrica = None if heading is None else heading.strip()
        units.extend(_paragraph_units(att, _descendants(att, "paragraph"), title, article_urn, version_date, art_num or "", rubrica))
    return units


def _doc_name_parts(att: Element) -> tuple[str, str | None]:
    """Normattiva codici attachments carry ``doc name="PARTIZIONE-art. N"``; returns (partition, article number)."""
    doc = _first(att, "doc")
    name = doc.get("name") if doc is not None else None
    if name is None or not name.strip():
        return "", None
    match = _DOC_NAME_ART.match(name)
    if match is None:
        return name.strip(), None
    num = match.group("num").strip().strip(".").strip().replace(" ", "-")
    return match.group("pre").strip().strip("-").strip(), (num or None)


def _article_num_from_eid(att: Element) -> str | None:
    article = _first(att, "article")
    eid = article.get("eId") if article is not None else None
    if eid is None:
        return None
    match = _ART_IN_EID.match(eid)
    return match.group(1) if match else None


def _attachment_urn(att: Element) -> str | None:
    expression = _first(att, "FRBRExpression")
    this = _first(expression, "FRBRthis") if expression is not None else None
    if this is None:
        this = _first(att, "FRBRthis")
    return this.get("value") if this is not None else None


def _article_num_from_urn(urn: str) -> str | None:
    match = _ART_IN_URN.search(urn)
    return match.group(1) if match else None
