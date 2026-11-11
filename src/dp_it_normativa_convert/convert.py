"""The ``execute`` action: per item, the grab zip becomes ``<stem>.plan.json``, ``<stem>.akn.xml`` (latest) and ``<stem>.convert.json``."""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dp_it_normativa_convert.akn import AknError
from dp_it_normativa_convert.plan import ActRow, write_plan
from dp_it_normativa_convert.versions import ActPlan, PlanError, ZipLayoutError, build_plan, read_zip

if TYPE_CHECKING:
    from collections.abc import Callable

CONFIG_ERROR = "config_error"
SYSTEM_ERROR = "system_error"
KIND_NORMATTIVA = "normattiva"
DEFAULT_COLLECTION = "NORMATTIVA"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


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


def _validate(params: dict[str, Any]) -> tuple[list[dict[str, Any]], Path, Path, str]:
    items = params.get("items")
    if not isinstance(items, list) or not items:
        raise ConvertError(CONFIG_ERROR, "items must be a non-empty array")
    for entry in items:
        meta = entry.get("meta") if isinstance(entry, dict) else None
        if not isinstance(entry, dict) or not entry.get("key") or not isinstance(meta, dict) or meta.get("kind") != KIND_NORMATTIVA:
            raise ConvertError(CONFIG_ERROR, f"item needs a key and meta.kind '{KIND_NORMATTIVA}': {entry!r}")
        if not isinstance(meta.get("actId"), str) or not meta["actId"]:
            raise ConvertError(CONFIG_ERROR, f"item {entry['key']}: meta.actId is required")
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
    from datetime import date  # noqa: PLC0415 - only the two ISO dates are needed here

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


def convert_item(entry: dict[str, Any], dir_in: Path, dir_out: Path, collection: str) -> tuple[dict[str, str], dict[str, int]]:
    meta = entry["meta"]
    stem = safe_name(meta["actId"])
    zip_path = dir_in / f"{stem}.zip"
    if not zip_path.exists():
        msg = f"{zip_path.name} not found"
        raise FileNotFoundError(msg)
    contents = read_zip(zip_path)
    if len(contents.acts) != 1:
        msg = f"zip holds {len(contents.acts)} acts, expected 1"
        raise ZipLayoutError(msg)
    act = contents.acts[0]
    plan = build_plan(act, collection)
    if not plan.units:
        msg = "the last version has no units"
        raise PlanError(msg)

    latest = act.versions[-1]
    akn = latest.read_xml().encode("utf-8")
    row = _act_row(meta, plan, act.versions[0].valid_from.isoformat(), latest.valid_from.isoformat(), collection)
    plan_path, akn_path, meta_path = (dir_out / f"{stem}{suffix}" for suffix in (".plan.json", ".akn.xml", ".convert.json"))
    write_plan(plan_path, plan, row)
    _write_atomic(akn_path, akn)
    info = {
        "act_id": meta["actId"],
        "act_urn": row.act_urn,
        "akn_file": akn_path.name,
        "sha256": hashlib.sha256(akn).hexdigest(),
        "version_date": latest.valid_from.isoformat(),
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
