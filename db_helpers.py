"""FalkorDB result conversion helpers.

Convert FalkorDB's list-of-lists result format (QueryResult) into dicts.

Usage:
    result = graph.query(cypher, params=params)
    rows = result_to_dicts(result)          # list of dicts
    row  = result_single(result)            # first row or None
    val  = result_value(result, "count", 0) # single value by column name
"""

from __future__ import annotations


def _unwrap_value(val):
    """Convert FalkorDB Node/Edge objects to plain dicts."""
    if val is None:
        return None
    if hasattr(val, 'properties') and hasattr(val, 'labels'):
        return {"_id": val.id, "_labels": val.labels, **val.properties}
    if hasattr(val, 'properties') and hasattr(val, 'relation'):
        return {"_id": val.id, "_type": val.relation, **val.properties}
    return val


def result_to_dicts(result) -> list[dict]:
    """Convert a FalkorDB QueryResult to list[dict]."""
    if not hasattr(result, 'result_set') or not result.result_set:
        return []
    headers = [h[1] for h in result.header]
    rows = []
    for row in result.result_set:
        d = {}
        for i, h in enumerate(headers):
            d[h] = _unwrap_value(row[i])
        rows.append(d)
    return rows


def result_single(result) -> dict | None:
    """First row as dict, or None."""
    rows = result_to_dicts(result)
    return rows[0] if rows else None


def result_value(result, key: str, default=None):
    """Single value from first row by column name."""
    row = result_single(result)
    if row is None:
        return default
    return row.get(key, default)
