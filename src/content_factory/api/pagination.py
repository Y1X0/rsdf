"""Shared `limit`/`offset` query params for list endpoints (Production
Hardening Sprint H5 — closes the production readiness review's P3
finding: every list endpoint returned its full table with no upper bound).

One dependency, reused across every router, so the bounds are identical
everywhere rather than fourteen slightly-different copies of the same
`Query(...)` call."""

from dataclasses import dataclass

from fastapi import Query


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int


def pagination_params(
    limit: int = Query(50, ge=1, le=200, description="Max rows to return"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
) -> Pagination:
    return Pagination(limit=limit, offset=offset)
