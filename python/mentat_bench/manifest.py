"""Resource profile: docs/ir.md caps plus the build deadline and spend ceiling (spec section 9).

Bounds mirror the Rust `Profile` fields: integer caps are positive integers within u64 (max_weight_bits within
u32); every number is finite; query_timeout_s is in (0, 1e7] seconds.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

PROFILE_TYPES: dict[str, type] = {
    "max_expr_nodes": int,
    "max_bytes": int,
    "max_interpretations": int,
    "max_weight_bits": int,
    "query_timeout_s": float,
    "build_deadline_s": float,
    "spend_ceiling_usd": float,
}
OPTIONAL_PROFILE_TYPES: dict[str, type] = {"max_expr_depth": int, "max_certificate_refs": int}  # read by mb-engine; absent = engine default / unlimited
INT_MAX = {"max_expr_nodes": 2**64 - 1, "max_bytes": 2**64 - 1, "max_interpretations": 2**64 - 1,
           "max_weight_bits": 2**32 - 1, "max_expr_depth": 2**64 - 1, "max_certificate_refs": 2**64 - 1}
MAX_QUERY_TIMEOUT_S = 1e7

DEFAULT_PROFILE: dict[str, Any] = {
    "max_expr_nodes": 2_000_000,
    "max_bytes": 512 * 2**20,
    "max_interpretations": 8,
    "max_weight_bits": 32,
    "query_timeout_s": 60.0,
    "build_deadline_s": 3600.0,
    "spend_ceiling_usd": 25.0,
}


class ProfileError(ValueError):
    pass


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-standard JSON constant {token}")


def validate_profile(profile: Any) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise ProfileError("profile must be a JSON object")
    if not set(PROFILE_TYPES) <= set(profile) <= set(PROFILE_TYPES) | set(OPTIONAL_PROFILE_TYPES):
        raise ProfileError(
            f"profile keys must be {sorted(PROFILE_TYPES)} plus optionally {sorted(OPTIONAL_PROFILE_TYPES)}; got {sorted(profile)}"
        )
    out: dict[str, Any] = {}
    types = {**PROFILE_TYPES, **{k: t for k, t in OPTIONAL_PROFILE_TYPES.items() if k in profile}}
    for key, typ in types.items():
        value = profile[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProfileError(f"{key} must be a number")
        if typ is int:
            if not isinstance(value, int) or not 1 <= value <= INT_MAX[key]:
                raise ProfileError(f"{key} must be an integer from 1 to {INT_MAX[key]}")
            out[key] = value
            continue
        try:
            number = float(value)
        except OverflowError:
            raise ProfileError(f"{key} must be finite") from None
        if not math.isfinite(number):
            raise ProfileError(f"{key} must be finite")
        out[key] = number
    if not 0 < out["query_timeout_s"] <= MAX_QUERY_TIMEOUT_S:
        raise ProfileError(f"query_timeout_s must be in (0, {MAX_QUERY_TIMEOUT_S:g}] seconds")
    if out["build_deadline_s"] <= 0:
        raise ProfileError("build_deadline_s must be positive")
    if out["spend_ceiling_usd"] < 0:
        raise ProfileError("spend_ceiling_usd must be non-negative")
    return out


def load_profile(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return dict(DEFAULT_PROFILE)
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
        return validate_profile(json.loads(text, parse_constant=_reject_constant))
    except (OSError, ValueError) as exc:
        if isinstance(exc, ProfileError):
            raise
        raise ProfileError(f"cannot read profile {path}: {exc}") from exc
