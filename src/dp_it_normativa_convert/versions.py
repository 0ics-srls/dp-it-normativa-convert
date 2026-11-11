"""Zip layout of a Normattiva multivigente export and the SCD-2 history plan.

Ports of the legacy ``ZipCorpusSource`` (layout) and ``VersionOrchestrator`` (plan). A zip holds one directory per act,
``<DENOMINAZIONE>_<yyyyMMdd>_<num>``, with one monovigente AKN file per version: ``..._ORIGINALE_V<n>.xml`` (valid from the act
date) and ``..._VIGENZA_<yyyy-MM-dd>_V<n>.xml``. The plan turns that chain into the vigente units (last version) and, per URN,
the history rows: a row stays open until the text changes or the URN disappears, then it is closed at that version's date.
"""

from __future__ import annotations

import base64
import re
import zipfile
from dataclasses import dataclass, field, replace
from datetime import date
from typing import TYPE_CHECKING

from dp_it_normativa_convert.akn import AknDocument, Unit, read_document

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_ORIGINALE = re.compile(r"_ORIGINALE_V(\d+)\.xml$", re.IGNORECASE)
_VIGENZA = re.compile(r"_VIGENZA_(\d{4}-\d{2}-\d{2})_V(\d+)\.xml$", re.IGNORECASE)


class ZipLayoutError(ValueError):
    """The zip does not follow the Normattiva export layout."""


class PlanError(ValueError):
    """A version plan cannot be built."""


@dataclass(frozen=True)
class VersionEntry:
    """One monovigente file of an act; ``read_xml`` loads the XML text on demand."""

    file_name: str
    number: int
    valid_from: date
    read_xml: Callable[[], str]


@dataclass(frozen=True)
class ActEntry:
    act_dir: str
    versions: list[VersionEntry]


@dataclass(frozen=True)
class ZipContents:
    acts: list[ActEntry]
    ignored_files: int


@dataclass(frozen=True)
class VersionRow:
    """History of one unit text: valid from ``valid_from`` until ``valid_to`` (``None`` = in force)."""

    urn: str
    valid_from: date
    valid_to: date | None
    text_raw: str


@dataclass(frozen=True)
class ActPlan:
    units: list[Unit]
    version_rows: list[VersionRow]
    versions_count: int
    seen_urns: list[str]
    dropped_units: int = field(default=0, compare=False)


def parse_act_date(act_dir: str) -> date:
    """The yyyyMMdd token at the second-to-last position of ``<DENOMINAZIONE>_<yyyyMMdd>_<num>``."""
    tokens = act_dir.split("_")
    if len(tokens) < 3:  # noqa: PLR2004 - denominazione, date, number
        raise ZipLayoutError(f"act directory '{act_dir}' is not <DENOMINAZIONE>_<yyyyMMdd>_<num>")
    token = tokens[-2]
    try:
        if len(token) != 8 or not token.isdigit():  # noqa: PLR2004 - yyyyMMdd
            raise ValueError(token)  # noqa: TRY301
        return date(int(token[:4]), int(token[4:6]), int(token[6:]))
    except ValueError as error:
        raise ZipLayoutError(f"act directory '{act_dir}' has no yyyyMMdd date token") from error


def parse_version_name(file_name: str, act_date: date) -> tuple[int, date] | None:
    """(version number, valid-from date) of a version file name, or ``None`` when the name is no version."""
    original = _ORIGINALE.search(file_name)
    if original:
        return int(original.group(1)), act_date
    vigenza = _VIGENZA.search(file_name)
    if vigenza:
        try:
            return int(vigenza.group(2)), date.fromisoformat(vigenza.group(1))
        except ValueError:
            return None
    return None


def read_zip(path: Path) -> ZipContents:
    """List the acts of a zip with their versions ordered by (date, number); other files are counted, not read."""
    by_act: dict[str, list[str]] = {}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir() or "/" not in info.filename:
                continue
            act_dir = info.filename.split("/", 1)[0]
            by_act.setdefault(act_dir, []).append(info.filename)

    ignored = 0
    acts: list[ActEntry] = []
    for act_dir, names in by_act.items():
        act_date = parse_act_date(act_dir)
        versions: list[VersionEntry] = []
        for full_name in names:
            parsed = parse_version_name(full_name.rsplit("/", 1)[-1], act_date)
            if parsed is None:
                ignored += 1
                continue
            number, valid_from = parsed
            versions.append(VersionEntry(full_name.rsplit("/", 1)[-1], number, valid_from, _entry_reader(path, full_name)))
        if not versions:
            continue
        # by vigenza DATE then number: version numbers of several redazionale documents can interleave with zig-zag dates
        versions.sort(key=lambda v: (v.valid_from, v.number))
        acts.append(ActEntry(act_dir, versions))
    return ZipContents(acts, ignored)


def _entry_reader(path: Path, full_name: str) -> Callable[[], str]:
    def read() -> str:
        with zipfile.ZipFile(path) as archive:
            raw = archive.read(full_name).decode("utf-8-sig")
        if raw.lstrip().startswith("<"):
            return raw
        return base64.b64decode(raw).decode("utf-8")  # base64 anomaly of some exports

    return read


def build_plan(act: ActEntry, collection: str, reader: Callable[[str], AknDocument] = read_document) -> ActPlan:
    """Vigente units plus the SCD-2 history of an act (``VersionOrchestrator.BuildPlan``)."""
    if not collection.strip():
        raise PlanError("collection is required")
    if not act.versions:
        raise PlanError(f"act {act.act_dir} has no versions")

    open_rows: dict[str, tuple[str, date]] = {}
    rows: list[VersionRow] = []
    seen: dict[str, None] = {}
    last_units: list[Unit] = []
    dropped = 0

    for version in act.versions:
        current, version_dropped = _parse_version(version, reader)
        dropped += version_dropped
        last_units = current
        seen.update(dict.fromkeys(unit.urn for unit in current))
        _advance(open_rows, rows, current, version.valid_from)

    rows.extend(VersionRow(urn, valid_from, None, text) for urn, (text, valid_from) in open_rows.items())
    rows.sort(key=lambda r: (r.urn, r.valid_from))
    units = [replace(unit, collection=collection, version_date=open_rows[unit.urn][1]) for unit in last_units]
    return ActPlan(units, rows, len(act.versions), list(seen), dropped)


def _advance(open_rows: dict[str, tuple[str, date]], rows: list[VersionRow], current: list[Unit], valid_from: date) -> None:
    """Apply one version (valid from ``valid_from``) to the open rows, appending the rows it closes."""
    present = {unit.urn for unit in current}

    # URNs that vanished close at this version's date; a row opened at this same date would have zero duration
    # (never in force): drop it, or a later rebirth at the same date collides with it on the (urn, valid_from) key
    for urn in [u for u in open_rows if u not in present]:
        text, opened = open_rows.pop(urn)
        if opened != valid_from:
            rows.append(VersionRow(urn, opened, valid_from, text))

    for unit in current:
        existing = open_rows.get(unit.urn)
        if existing is not None and existing[0] == unit.text_raw:
            continue
        # several snapshots can share a date: overwrite instead of close + reopen (two rows with one key)
        if existing is not None and existing[1] != valid_from:
            rows.append(VersionRow(unit.urn, existing[1], valid_from, existing[0]))
        open_rows[unit.urn] = (unit.text_raw, valid_from)


def _parse_version(version: VersionEntry, reader: Callable[[str], AknDocument]) -> tuple[list[Unit], int]:
    try:
        parsed = reader(version.read_xml()).units
    except Exception as error:
        raise PlanError(f"Failed to parse version {version.file_name}: {error}") from error

    # same rules as the legacy ingest: drop empty-text units, collapse duplicate URNs (last wins, first position)
    deduped: dict[str, Unit] = {}
    for unit in parsed:
        if unit.text_contextual.strip():
            deduped[unit.urn] = unit
    return list(deduped.values()), len(parsed) - len(deduped)
