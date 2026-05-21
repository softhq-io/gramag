"""Gramag — Predictive Maintenance / Fleet Health analytics.

Pure Cypher + Python scoring engine. No ML — just graph analytics
based on service history, MTBR (Mean Time Between Replacement),
and risk factors.
"""

from datetime import datetime, timedelta
from db import db
from db_helpers import result_to_dicts, result_value


# ── helpers ────────────────────────────────────────────────────────────

def _parse_date(d: str | None) -> datetime | None:
    """Parse ERP date string (various formats) into datetime."""
    if not d:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(d.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _days_between(d1: datetime, d2: datetime) -> int:
    return abs((d2 - d1).days)


def _linear_scale(value: float, low: float, high: float) -> float:
    """Map value to 0.0–1.0 linearly between low and high."""
    if value <= low:
        return 0.0
    if value >= high:
        return 1.0
    return (value - low) / (high - low)


# ── MTBR ───────────────────────────────────────────────────────────────

def compute_mtbr(erp_id: str) -> list[dict]:
    """Compute Mean Time Between Replacement for parts used on a machine.

    Returns list of parts with avg replacement interval and predicted
    next replacement date.
    """
    result = db.query("""
        MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m:Machine {erp_id: $erp_id})
        MATCH (sj)-[:USED_PART]->(p:Part)
        WHERE NOT p.noise
        WITH p, sj ORDER BY sj.date
        WITH p, collect(sj.date) AS dates
        WHERE size(dates) >= 2
        RETURN p.nummer AS part_nummer,
               p.titel AS part_name,
               dates
    """, {"erp_id": erp_id})

    rows = result_to_dicts(result)
    predictions = []

    for row in rows:
        dates_raw = row.get("dates", [])
        dates = [_parse_date(d) for d in dates_raw]
        dates = [d for d in dates if d is not None]
        dates.sort()

        if len(dates) < 2:
            continue

        intervals = []
        for i in range(1, len(dates)):
            days = _days_between(dates[i - 1], dates[i])
            if days > 0:  # skip same-day duplicates
                intervals.append(days)

        if not intervals:
            continue

        avg_days = sum(intervals) / len(intervals)
        last_replaced = dates[-1]
        next_predicted = last_replaced + timedelta(days=avg_days)

        # Confidence based on sample size and variance
        if len(intervals) >= 4:
            confidence = "high"
        elif len(intervals) >= 2:
            confidence = "medium"
        else:
            confidence = "low"

        predictions.append({
            "part_nummer": row["part_nummer"],
            "part_name": row["part_name"] or "",
            "avg_days": round(avg_days),
            "last_replaced": last_replaced.strftime("%Y-%m-%d"),
            "next_predicted": next_predicted.strftime("%Y-%m-%d"),
            "confidence": confidence,
        })

    # Sort by nearest predicted date
    predictions.sort(key=lambda p: p["next_predicted"])
    return predictions


# ── Risk Score ─────────────────────────────────────────────────────────

def compute_risk_score(erp_id: str) -> dict:
    """Compute composite risk score (0–100) for a machine.

    5 factors (weights sum to 1.0):
    1. Time since last service      (0.30)
    2. MTBR overdue parts           (0.25)
    3. Service frequency            (0.20)
    4. Last service type            (0.10)
    5. Machine age                  (0.15)
    """
    now = datetime.now()
    factors = []

    # ── Factor 1: Time since last service (weight 0.30) ──────────────
    last_service_result = db.query("""
        MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m:Machine {erp_id: $erp_id})
        RETURN max(sj.date) AS last_date
    """, {"erp_id": erp_id})
    last_date_str = result_value(last_service_result, "last_date")
    last_service_dt = _parse_date(last_date_str)

    if last_service_dt:
        days_since = _days_between(now, last_service_dt)
        f1_value = _linear_scale(days_since, 180, 365)
    else:
        days_since = None
        f1_value = 1.0  # no service history = max risk

    factors.append({
        "name": "serviceInterval",
        "value": round(f1_value, 2),
        "weight": 0.30,
        "contribution": round(f1_value * 0.30, 3),
    })

    # ── Factor 2: MTBR overdue parts (weight 0.25) ───────────────────
    mtbr_parts = compute_mtbr(erp_id)
    overdue_count = 0
    soon_count = 0
    for p in mtbr_parts:
        pred = _parse_date(p["next_predicted"])
        if pred and pred < now:
            overdue_count += 1
        elif pred and pred < now + timedelta(days=30):
            soon_count += 1

    if overdue_count > 0:
        f2_value = min(1.0, 0.7 + overdue_count * 0.1)
    elif soon_count > 0:
        f2_value = min(0.7, 0.3 + soon_count * 0.1)
    elif mtbr_parts:
        # Parts tracked but none overdue
        nearest = _parse_date(mtbr_parts[0]["next_predicted"])
        if nearest:
            days_to = _days_between(now, nearest)
            f2_value = _linear_scale(90 - min(days_to, 90), 0, 90) * 0.3
        else:
            f2_value = 0.0
    else:
        f2_value = 0.2  # no data = slight risk

    factors.append({
        "name": "mtbrOverdue",
        "value": round(f2_value, 2),
        "weight": 0.25,
        "contribution": round(f2_value * 0.25, 3),
    })

    # ── Factor 3: Service frequency (weight 0.20) ────────────────────
    freq_result = db.query("""
        MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m:Machine {erp_id: $erp_id})
        WITH sj ORDER BY sj.date
        WITH collect(sj.date) AS dates, count(sj) AS total
        RETURN total, dates[0] AS first_date
    """, {"erp_id": erp_id})
    freq_row = result_to_dicts(freq_result)

    if freq_row and freq_row[0].get("total", 0) > 0:
        total_jobs = freq_row[0]["total"]
        first_dt = _parse_date(freq_row[0].get("first_date"))
        if first_dt and _days_between(now, first_dt) > 30:
            years_active = max(_days_between(now, first_dt) / 365.0, 0.5)
            freq = total_jobs / years_active
            if freq > 4:
                f3_value = 1.0
            elif freq > 2:
                f3_value = 0.5
            else:
                f3_value = 0.2
        else:
            f3_value = 0.2
    else:
        f3_value = 0.1

    factors.append({
        "name": "frequency",
        "value": round(f3_value, 2),
        "weight": 0.20,
        "contribution": round(f3_value * 0.20, 3),
    })

    # ── Factor 4: Last service type (weight 0.10) ────────────────────
    # Check title keywords from last service job
    last_job_result = db.query("""
        MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m:Machine {erp_id: $erp_id})
        RETURN sj.title AS title
        ORDER BY sj.date DESC
        LIMIT 1
    """, {"erp_id": erp_id})
    last_job = result_to_dicts(last_job_result)

    if last_job:
        title = (last_job[0].get("title") or "").lower()
        if any(kw in title for kw in ("reparatur", "repair", "störung", "defekt", "notfall")):
            f4_value = 0.8
        elif any(kw in title for kw in ("wartung", "maintenance", "inspektion")):
            f4_value = 0.3
        elif any(kw in title for kw in ("installation", "inbetriebnahme", "aufstellung")):
            f4_value = 0.1
        else:
            f4_value = 0.4  # unknown type
    else:
        f4_value = 0.5

    factors.append({
        "name": "lastType",
        "value": round(f4_value, 2),
        "weight": 0.10,
        "contribution": round(f4_value * 0.10, 3),
    })

    # ── Factor 5: Machine age (weight 0.15) ──────────────────────────
    # Use first service date as proxy for machine start
    age_result = db.query("""
        MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m:Machine {erp_id: $erp_id})
        RETURN min(sj.date) AS first_service
    """, {"erp_id": erp_id})
    first_service_str = result_value(age_result, "first_service")
    first_service = _parse_date(first_service_str)

    if first_service:
        age_years = _days_between(now, first_service) / 365.0
        if age_years > 15:
            f5_value = 1.0
        elif age_years > 10:
            f5_value = 0.6
        elif age_years > 5:
            f5_value = 0.3
        else:
            f5_value = 0.1
    else:
        f5_value = 0.5

    factors.append({
        "name": "machineAge",
        "value": round(f5_value, 2),
        "weight": 0.15,
        "contribution": round(f5_value * 0.15, 3),
    })

    # ── Composite score ──────────────────────────────────────────────
    score = sum(f["contribution"] for f in factors)
    score_100 = round(score * 100)
    score_100 = max(0, min(100, score_100))

    if score_100 >= 70:
        level = "critical"
    elif score_100 >= 40:
        level = "warning"
    else:
        level = "good"

    # Build next_predicted from MTBR
    next_predicted = None
    if mtbr_parts:
        next_predicted = mtbr_parts[0]["next_predicted"]

    return {
        "erp_id": erp_id,
        "risk_score": score_100,
        "risk_level": level,
        "factors": factors,
        "last_service": last_date_str,
        "next_predicted": next_predicted,
    }


# ── Fleet Dashboard ───────────────────────────────────────────────────

def get_fleet_dashboard(
    customer_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    q: str | None = None,
) -> dict:
    """Build fleet health dashboard with risk scores for all machines.

    Optionally filter by customer_id.
    """
    search = (q or "").strip().lower()
    params = {
        "customer_id": customer_id,
        "limit": limit,
        "offset": offset,
        "q": search,
    }
    search_clause = """
        ($q = '' OR
         toLower(coalesce(m.title, '')) CONTAINS $q OR
         toLower(coalesce(m.erp_id, '')) CONTAINS $q OR
         toLower(coalesce(m.serial_number, '')) CONTAINS $q OR
         toLower(coalesce(c.name, '')) CONTAINS $q)
    """

    if customer_id:
        machines_result = db.query("""
            MATCH (c:Customer {erp_id: $customer_id})-[:OWNS]->(m:Machine)
            OPTIONAL MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m)
            WITH m, c, count(sj) AS job_count
            WHERE job_count > 0 AND
                  ($q = '' OR
                   toLower(coalesce(m.title, '')) CONTAINS $q OR
                   toLower(coalesce(m.erp_id, '')) CONTAINS $q OR
                   toLower(coalesce(m.serial_number, '')) CONTAINS $q OR
                   toLower(coalesce(c.name, '')) CONTAINS $q)
            RETURN m.erp_id AS erp_id, m.title AS name,
                   c.name AS customer, c.erp_id AS customer_id
            ORDER BY m.title
            SKIP $offset
            LIMIT $limit
        """, params)
        count_result = db.query(f"""
            MATCH (c:Customer {{erp_id: $customer_id}})-[:OWNS]->(m:Machine)
            MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m)
            WITH DISTINCT m, c
            WHERE {search_clause}
            RETURN count(DISTINCT m) AS total
        """, params)
    else:
        machines_result = db.query("""
            MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m:Machine)
            OPTIONAL MATCH (c:Customer)-[:OWNS]->(m)
            WITH m, c, count(sj) AS job_count
            WHERE job_count > 0 AND
                  ($q = '' OR
                   toLower(coalesce(m.title, '')) CONTAINS $q OR
                   toLower(coalesce(m.erp_id, '')) CONTAINS $q OR
                   toLower(coalesce(m.serial_number, '')) CONTAINS $q OR
                   toLower(coalesce(c.name, '')) CONTAINS $q)
            RETURN DISTINCT m.erp_id AS erp_id, m.title AS name,
                   c.name AS customer, c.erp_id AS customer_id
            ORDER BY m.title
            SKIP $offset
            LIMIT $limit
        """, params)
        count_result = db.query(f"""
            MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m:Machine)
            OPTIONAL MATCH (c:Customer)-[:OWNS]->(m)
            WITH DISTINCT m, c
            WHERE {search_clause}
            RETURN count(DISTINCT m) AS total
        """, params)

    machines = result_to_dicts(machines_result)
    total = result_value(count_result, "total", 0)

    # Compute risk for the current page only. Full-fleet risk distribution should
    # be precomputed before we sort/filter by risk globally.
    results = []
    summary = {"total": total, "critical": 0, "warning": 0, "good": 0}

    for m in machines:
        erp_id = m["erp_id"]
        try:
            risk = compute_risk_score(erp_id)
            results.append({
                "erp_id": erp_id,
                "name": m.get("name") or "",
                "customer": m.get("customer") or "",
                "customer_id": m.get("customer_id") or "",
                "risk_score": risk["risk_score"],
                "risk_level": risk["risk_level"],
                "last_service": risk["last_service"],
                "next_predicted": risk["next_predicted"],
            })
            summary[risk["risk_level"]] += 1
        except Exception:
            # Skip machines with query errors
            continue

    pagination = {
        "limit": limit,
        "offset": offset,
        "returned": len(results),
        "has_more": offset + len(results) < total,
    }

    return {"summary": summary, "pagination": pagination, "machines": results}


def get_customers_list() -> list[dict]:
    """Return list of customers that have machines with service history."""
    result = db.query("""
        MATCH (c:Customer)-[:OWNS]->(m:Machine)<-[:FOR_MACHINE]-(sj:ServiceJob)
        WITH c, count(DISTINCT m) AS machine_count
        WHERE machine_count > 0
        RETURN c.erp_id AS erp_id, c.name AS name, machine_count
        ORDER BY c.name
    """)
    return result_to_dicts(result)
