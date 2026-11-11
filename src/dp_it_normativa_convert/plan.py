"""The ``<actId>.plan.json`` document that convert writes and ingest loads (no XML knowledge in ingest)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from dp_it_normativa_convert.akn import Unit
from dp_it_normativa_convert.versions import ActPlan, VersionRow

if TYPE_CHECKING:
    from pathlib import Path

PLAN_SCHEMA_VERSION = 1


class PlanFormatError(ValueError):
    """The plan document is malformed or of an unknown schema version."""


@dataclass(frozen=True)
class ActRow:
    """The ``acts`` table row of one act (``ingested_at`` is set by ingest)."""

    act_urn: str
    title: str
    denominazione: str | None
    year: int | None
    number: int | None
    kind: str
    collection: str | None
    versions_count: int
    first_valid_from: date | None
    last_valid_from: date | None
    units_count: int
    source_url: str | None


def _iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def plan_to_dict(plan: ActPlan, act: ActRow) -> dict[str, Any]:
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "act": {**act.__dict__, "first_valid_from": _iso(act.first_valid_from), "last_valid_from": _iso(act.last_valid_from)},
        "units": [
            {
                "urn": u.urn,
                "act": u.act,
                "act_urn": u.act_urn,
                "article_num": u.article_num,
                "comma_num": u.comma_num,
                "materialized_path": u.materialized_path,
                "rubrica": u.rubrica,
                "text_raw": u.text_raw,
                "text_contextual": u.text_contextual,
                "version_date": u.version_date.isoformat(),
                "collection": u.collection,
            }
            for u in plan.units
        ],
        "version_rows": [
            {"urn": r.urn, "valid_from": r.valid_from.isoformat(), "valid_to": _iso(r.valid_to), "text_raw": r.text_raw}
            for r in plan.version_rows
        ],
        "seen_urns": list(plan.seen_urns),
        "versions_count": plan.versions_count,
    }


def plan_from_dict(document: dict[str, Any]) -> tuple[ActPlan, ActRow]:
    try:
        if document["schema_version"] != PLAN_SCHEMA_VERSION:
            raise PlanFormatError(f"unsupported plan schema_version {document['schema_version']}")
        act = {**document["act"]}
        act["first_valid_from"] = _date(act["first_valid_from"])
        act["last_valid_from"] = _date(act["last_valid_from"])
        units = [Unit(**{**u, "version_date": date.fromisoformat(u["version_date"])}) for u in document["units"]]
        rows = [
            VersionRow(r["urn"], date.fromisoformat(r["valid_from"]), _date(r["valid_to"]), r["text_raw"]) for r in document["version_rows"]
        ]
        plan = ActPlan(units, rows, int(document["versions_count"]), list(document["seen_urns"]))
        return plan, ActRow(**act)
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, PlanFormatError):
            raise
        raise PlanFormatError(f"malformed plan document: {error}") from error


def write_plan(path: Path, plan: ActPlan, act: ActRow) -> None:
    """Write the plan atomically and deterministically (UTF-8, no ASCII escaping)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(plan_to_dict(plan, act), ensure_ascii=False, separators=(",", ":"))
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def read_plan(path: Path) -> tuple[ActPlan, ActRow]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PlanFormatError(f"cannot read plan {path.name}: {error}") from error
    if not isinstance(document, dict):
        raise PlanFormatError("plan document must be an object")
    return plan_from_dict(document)
