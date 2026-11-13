"""Whitelisted evidence filter DSL → parameterised SQL.

A leak's `evidence_query` is a JSON object such as
    {"merchant_id": "M014", "method": ["card"], "issuer_bank": ["BANK_C"],
     "hours": [18, 22], "is_recurring": 0, "attempt_no": 1}
Only the keys below are accepted; values are always bound as SQL parameters.
"""
from __future__ import annotations

from typing import Any

_LIST_COLUMNS = {"method", "issuer_bank", "device", "status", "error_code", "error_layer"}
_SCALAR_COLUMNS = {"merchant_id", "is_recurring", "attempt_no"}
ALLOWED_KEYS = _LIST_COLUMNS | _SCALAR_COLUMNS | {"hours", "dates"}


class EvidenceQueryError(ValueError):
    pass


def compile_where(q: dict[str, Any]) -> tuple[str, list[Any]]:
    unknown = set(q) - ALLOWED_KEYS
    if unknown:
        raise EvidenceQueryError(f"unknown evidence keys: {sorted(unknown)}")
    if "merchant_id" not in q:
        raise EvidenceQueryError("evidence query must be scoped to a merchant_id")
    clauses: list[str] = []
    params: list[Any] = []
    for key in sorted(q):
        val = q[key]
        if key in _SCALAR_COLUMNS:
            clauses.append(f"{key} = ?")
            params.append(val)
        elif key in _LIST_COLUMNS:
            vals = val if isinstance(val, list) else [val]
            if not vals:
                continue
            clauses.append(f"{key} IN ({','.join('?' for _ in vals)})")
            params.extend(vals)
        elif key == "hours":
            start, end = int(val[0]), int(val[1])
            clauses.append("CAST(substr(ts, 12, 2) AS INTEGER) >= ? AND CAST(substr(ts, 12, 2) AS INTEGER) < ?")
            params.extend([start, end])
        elif key == "dates":
            clauses.append(f"substr(ts, 1, 10) IN ({','.join('?' for _ in val)})")
            params.extend(val)
    return " AND ".join(clauses), params
