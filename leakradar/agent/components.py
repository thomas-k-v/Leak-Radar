"""The fixed component library. The agent renders UI only through these 6 schemas; props are IDs.

Mirrored by web/src/schemas/components.ts (zod). Both are checked against the shared fixtures in
tests/fixtures/components/.
"""
from __future__ import annotations

import sqlite3
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError

from leakradar.common import repo

LeakId = Annotated[str, StringConstraints(pattern=r"^L\d{4}$")]
PitchId = Annotated[str, StringConstraints(pattern=r"^P\d{4}$")]
Metric = Literal["failure_rate", "collect_share", "recurring_fail_rate"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LeakIdsProps(_Strict):
    leak_ids: list[LeakId] = Field(min_length=1, max_length=25)


class LeakProps(_Strict):
    leak_id: LeakId


class TrendProps(_Strict):
    leak_id: LeakId
    metric: Metric


class PitchProps(_Strict):
    pitch_id: PitchId


class MerchantLeakTable(_Strict):
    type: Literal["MerchantLeakTable"]
    props: LeakIdsProps


class LeakCard(_Strict):
    type: Literal["LeakCard"]
    props: LeakProps


class RootCauseCard(_Strict):
    type: Literal["RootCauseCard"]
    props: LeakProps


class TrendChart(_Strict):
    type: Literal["TrendChart"]
    props: TrendProps


class DraftMessage(_Strict):
    type: Literal["DraftMessage"]
    props: PitchProps


class EvidenceDrawer(_Strict):
    type: Literal["EvidenceDrawer"]
    props: LeakProps


Component = Annotated[Union[MerchantLeakTable, LeakCard, RootCauseCard, TrendChart, DraftMessage, EvidenceDrawer],
                      Field(discriminator="type")]
COMPONENT_ADAPTER: TypeAdapter = TypeAdapter(Component)
COMPONENT_TYPES = ["MerchantLeakTable", "LeakCard", "RootCauseCard", "TrendChart", "DraftMessage", "EvidenceDrawer"]


class ComponentError(ValueError):
    pass


def validate_schema(type_: str, props: dict) -> dict:
    """Schema-only validation (no DB). Returns the normalised {type, props}."""
    try:
        c = COMPONENT_ADAPTER.validate_python({"type": type_, "props": props})
    except ValidationError as e:
        msgs = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        raise ComponentError(f"invalid component: {msgs}. Allowed types: {', '.join(COMPONENT_TYPES)}") from None
    return c.model_dump()


def validate(conn: sqlite3.Connection, type_: str, props: dict) -> dict:
    """Schema validation + every referenced ID must exist + metric must fit the leak's detector."""
    c = validate_schema(type_, props)
    p = c["props"]
    leak_ids = p.get("leak_ids") or ([p["leak_id"]] if "leak_id" in p else [])
    for lid in leak_ids:
        det = repo.leak_exists(conn, lid)
        if det is None:
            raise ComponentError(f"unknown leak_id {lid}")
        if "metric" in p and repo.METRIC_FOR_DETECTOR[det] != p["metric"]:
            raise ComponentError(f"metric {p['metric']} does not apply to leak {lid}; "
                                 f"use {repo.METRIC_FOR_DETECTOR[det]}")
    if "pitch_id" in p:
        try:
            repo.get_pitch(conn, p["pitch_id"])
        except repo.NotFound:
            raise ComponentError(f"unknown pitch_id {p['pitch_id']}") from None
    return c


def json_schema() -> dict:
    return COMPONENT_ADAPTER.json_schema()
