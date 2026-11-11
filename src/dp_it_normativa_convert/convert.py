"""The ``execute`` action: per item, the grab file becomes ``<stem>.plan.json``, ``<stem>.akn.xml`` (latest) and ``<stem>.convert.json``.

``kind=normattiva``: the export zip (``<stem>.zip``), the full version history. ``kind=gu``: the Gazzetta print (``<codice>.html`` plus the
``<codice>.meta.json`` sidecar), one version valid from the vigenza date; the source HTML is what is stored in ``<stem>.akn.xml``.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dp_it_normativa_convert import gu_html
from dp_it_normativa_convert.akn import AknError
from dp_it_normativa_convert.plan import ActRow, write_plan
from dp_it_normativa_convert.versions import ActEntry, ActPlan, PlanError, VersionEntry, ZipLayoutError, build_plan, read_zip

if TYPE_CHECKING:
    from collections.abc import Callable

CONFIG_ERROR = "config_error"
SYSTEM_ERROR = "system_error"
KIND_NORMATTIVA = "normattiva"
KIND_GU = "gu"
KINDS = (KIND_NORMATTIVA, KIND_GU)
DEFAULT_COLLECTION = "NORMATTIVA"
GU_SOURCE_URL = "https://www.gazzettaufficiale.it/atto/serie_generale/caricaDettaglioAtto/originario"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_CODICE = re.compile(r"^[0-9A-Z]{6,12}$")
_NIR_ACT = re.compile(r"^urn:nir:stato:(?P<kind>legge|decreto\.legge):(?P<year>\d{4})-\d{2}-\d{2};(?P<number>\d+)$")


class ConvertError(Exception):
    """A run-level failure carrying one of the protocol error codes."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def safe_name(act_id: str) -> str:
    """File stem of an act id (identical to the grab class): spaces become hyphens, other unsafe characters underscores."""
    return _UNSAFE.sub("_", act_id.replace(" ", "-"))


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_item(entry: Any) -> None:
    meta = entry.get("meta") if isinstance(entry, dict) else None
    if not isinstance(entry, dict) or not entry.get("key") or not isinstance(meta, dict) or meta.get("kind") not in KINDS:
        raise ConvertError(CONFIG_ERROR, f"item needs a key and meta.kind one of {list(KINDS)}: {entry!r}")
    if not isinstance(meta.get("actId"), str) or not meta["actId"]:
        raise ConvertError(CONFIG_ERROR, f"item {entry['key']}: meta.actId is required")
    if meta["kind"] == KIND_GU and not (isinstance(meta.get("codiceRedazionale"), str) and _CODICE.match(meta["codiceRedazionale"])):
        raise ConvertError(CONFIG_ERROR, f"item {entry['key']}: a gu item needs meta.codiceRedazionale")


def _validate(params: dict[str, Any]) -> tuple[list[dict[str, Any]], Path, Path, str]:
    items = params.get("items")
    if not isinstance(items, list) or not items:
        raise ConvertError(CONFIG_ERROR, "items must be a non-empty array")
    for entry in items:
        _validate_item(entry)
    for name in ("dirIn", "dirOut"):
        if not isinstance(params.get(name), str) or not params[name]:
            raise ConvertError(CONFIG_ERROR, f"{name} is required")
    collection = params.get("collection", DEFAULT_COLLECTION)
    if not isinstance(collection, str) or not collection.strip():
        raise ConvertError(CONFIG_ERROR, "collection must be a non-blank string")
    return items, Path(params["dirIn"]), Path(params["dirOut"]), collection


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_bytes(data)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _act_row(meta: dict[str, Any], plan: ActPlan, first: str, last: str, collection: str) -> ActRow:
    act_urn = plan.units[0].act_urn
    return ActRow(
        act_urn=act_urn,
        title=plan.units[0].act,
        denominazione=meta.get("denominazione"),
        year=meta.get("anno") if _is_int(meta.get("anno")) else None,
        number=meta.get("numero") if _is_int(meta.get("numero")) else None,
        kind=KIND_NORMATTIVA,
        collection=collection,
        versions_count=plan.versions_count,
        first_valid_from=date.fromisoformat(first),
        last_valid_from=date.fromisoformat(last),
        units_count=len(plan.units),
        source_url="https://www.normattiva.it/uri-res/N2Ls?" + act_urn if act_urn.startswith("urn:nir:") else None,
    )


def _gu_act_row(meta: dict[str, Any], plan: ActPlan, vigenza: date, collection: str) -> ActRow:
    act_urn = plan.units[0].act_urn
    found = _NIR_ACT.match(act_urn)
    return ActRow(
        act_urn=act_urn,
        title=plan.units[0].act,
        denominazione=None if found is None else ("LEGGE" if found["kind"] == "legge" else "DECRETO-LEGGE"),
        year=None if found is None else int(found["year"]),
        number=None if found is None else int(found["number"]),
        kind=KIND_GU,
        collection=collection,
        versions_count=plan.versions_count,
        first_valid_from=vigenza,
        last_valid_from=vigenza,
        units_count=len(plan.units),
        source_url=f"{GU_SOURCE_URL}?atto.dataPubblicazioneGazzetta={meta.get('dataGU')}&atto.codiceRedazionale={meta['codiceRedazionale']}",
    )


def _require_units(plan: ActPlan) -> None:
    if not plan.units:
        msg = "the last version has no units"
        raise PlanError(msg)


def _read_normattiva(meta: dict[str, Any], dir_in: Path, collection: str) -> tuple[ActPlan, ActRow, bytes, date]:
    zip_path = dir_in / f"{safe_name(meta['actId'])}.zip"
    if not zip_path.exists():
        msg = f"{zip_path.name} not found"
        raise FileNotFoundError(msg)
    contents = read_zip(zip_path)
    if len(contents.acts) != 1:
        msg = f"zip holds {len(contents.acts)} acts, expected 1"
        raise ZipLayoutError(msg)
    act = contents.acts[0]
    plan = build_plan(act, collection)
    latest = act.versions[-1]
    _require_units(plan)
    row = _act_row(meta, plan, act.versions[0].valid_from.isoformat(), latest.valid_from.isoformat(), collection)
    return plan, row, latest.read_xml().encode("utf-8"), latest.valid_from


def _gu_vigenza(meta: dict[str, Any], sidecar: dict[str, Any] | None, code: str) -> date:
    given = meta.get("vigenza") or (sidecar or {}).get("vigenza")
    if not isinstance(given, str) or not given:
        msg = f"no vigenza date: neither the item nor {code}.meta.json gives one"
        raise ValueError(msg)
    return date.fromisoformat(given)


def _read_gu(meta: dict[str, Any], dir_in: Path, collection: str) -> tuple[ActPlan, ActRow, bytes, date]:
    code = meta["codiceRedazionale"]
    html_path, sidecar_path = dir_in / f"{code}.html", dir_in / f"{code}.meta.json"
    if not html_path.exists():
        msg = f"{html_path.name} not found"
        raise FileNotFoundError(msg)
    raw = html_path.read_bytes()
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8")) if sidecar_path.exists() else None
    vigenza = _gu_vigenza(meta, sidecar, code)
    entry = ActEntry(code, [VersionEntry(html_path.name, 1, vigenza, lambda: raw.decode("utf-8"))])
    plan = build_plan(entry, collection, lambda page: gu_html.read_document(page, sidecar, vigenza))
    _require_units(plan)
    row = _gu_act_row(meta, plan, vigenza, collection)
    return plan, row, raw, vigenza


def convert_item(entry: dict[str, Any], dir_in: Path, dir_out: Path, collection: str) -> tuple[dict[str, str], dict[str, int]]:
    meta = entry["meta"]
    stem = safe_name(meta["actId"])
    plan, row, akn, version_date = (_read_gu if meta["kind"] == KIND_GU else _read_normattiva)(meta, dir_in, collection)

    plan_path, akn_path, meta_path = (dir_out / f"{stem}{suffix}" for suffix in (".plan.json", ".akn.xml", ".convert.json"))
    write_plan(plan_path, plan, row)
    _write_atomic(akn_path, akn)
    info = {
        "act_id": meta["actId"],
        "act_urn": row.act_urn,
        "akn_file": akn_path.name,
        "sha256": hashlib.sha256(akn).hexdigest(),
        "version_date": version_date.isoformat(),
        "units": len(plan.units),
        "versions": plan.versions_count,
        "version_rows": len(plan.version_rows),
    }
    _write_atomic(meta_path, json.dumps(info, ensure_ascii=False, indent=2).encode("utf-8"))
    counts = {"units": len(plan.units), "versions": plan.versions_count, "versionRows": len(plan.version_rows)}
    return {"key": entry["key"], "plan": str(plan_path), "akn": str(akn_path), "meta": str(meta_path)}, counts


def run_convert(params: dict[str, Any], on_progress: Callable[[int, str], None]) -> dict[str, Any]:
    """Converts every item; failed items are collected and raised as one system_error after the good ones were written."""
    items, dir_in, dir_out, collection = _validate(params)
    converted: list[dict[str, str]] = []
    totals = {"acts": 0, "units": 0, "versions": 0, "versionRows": 0}
    failures: list[str] = []
    for index, entry in enumerate(items):
        try:
            files, counts = convert_item(entry, dir_in, dir_out, collection)
        except (OSError, zipfile.BadZipFile, ZipLayoutError, PlanError, AknError, ValueError) as error:
            failures.append(f"{entry['key']} ({error})")
        else:
            converted.append(files)
            totals["acts"] += 1
            for name, value in counts.items():
                totals[name] += value
        on_progress(int((index + 1) * 100 / len(items)), f"{index + 1}/{len(items)} {entry['key']}")
    if failures:
        msg = f"{len(failures)} of {len(items)} acts failed: {'; '.join(failures)}"
        raise ConvertError(SYSTEM_ERROR, msg)
    return {"dirOut": str(dir_out), "converted": converted, "metrics": totals}
