"""Gramag Knowledge Graph — Co-occurrence mining + session layer.

L3 Playbook: Compute OFTEN_USED_WITH edges from service history.
L4 Session: Track multi-turn conversations in the graph.
"""

import time
import uuid
from db import db
from db_helpers import result_to_dicts, result_value


# ── L3: Part co-occurrence mining ────────────────────────────────────

def mine_co_occurrence(min_count: int = 3):
    """Find parts frequently used together in service jobs.

    Creates OFTEN_USED_WITH edges between parts that co-occur in >= min_count jobs.
    Excludes noise articles (shipping, travel, etc.).
    """
    print("Mining part co-occurrence...")
    t0 = time.time()

    # First, remove old co-occurrence edges
    db.write("MATCH ()-[r:OFTEN_USED_WITH]->() DELETE r")

    # Create new edges
    result = db.write("""
        MATCH (p1:Part)<-[:USED_PART]-(sj:ServiceJob)-[:USED_PART]->(p2:Part)
        WHERE p1.erp_id < p2.erp_id AND NOT p1.noise AND NOT p2.noise
        WITH p1, p2, count(sj) AS co_count
        WHERE co_count >= $min_count
        MERGE (p1)-[r:OFTEN_USED_WITH]->(p2)
        SET r.count = co_count
        RETURN count(r) AS edges_created
    """, {"min_count": min_count})

    edges = result_value(result, "edges_created", 0)
    elapsed = time.time() - t0
    print(f"  Created {edges} OFTEN_USED_WITH edges (min co-occurrence: {min_count})")
    print(f"  Done in {elapsed:.1f}s")

    # Show top co-occurring pairs
    result = db.query("""
        MATCH (p1:Part)-[r:OFTEN_USED_WITH]->(p2:Part)
        RETURN p1.nummer AS nr1, p1.titel AS part1,
               p2.nummer AS nr2, p2.titel AS part2,
               r.count AS count
        ORDER BY r.count DESC LIMIT 15
    """)
    rows = result_to_dicts(result)
    if rows:
        print("\n  Top co-occurring part pairs:")
        for r in rows:
            print(f"    {r['count']:>3}x  [{r['nr1']}] {r['part1'][:40]}")
            print(f"         [{r['nr2']}] {r['part2'][:40]}")

    return edges


# ── L4: Session layer ────────────────────────────────────────────────

def create_session(user_id: str = "default") -> str:
    """Create a new conversation session in the graph."""
    session_id = str(uuid.uuid4())[:8]
    db.write("""
        MERGE (s:Session {id: $id})
        SET s.user_id = $user_id,
            s.created = datetime(),
            s.last_active = datetime(),
            s.turn_count = 0
    """, {"id": session_id, "user_id": user_id})
    return session_id


def add_turn(session_id: str, query: str, entities: dict):
    """Record a conversation turn and link discussed entities.

    entities: {machines: [erp_id, ...], parts: [erp_id, ...], ...}
    """
    db.write("""
        MATCH (s:Session {id: $sid})
        SET s.last_active = datetime(),
            s.turn_count = s.turn_count + 1
    """, {"sid": session_id})

    # Link machines discussed in this session
    for machine_id in entities.get("machines", []):
        db.write("""
            MATCH (s:Session {id: $sid})
            MATCH (m:Machine {erp_id: $mid})
            MERGE (s)-[:DISCUSSED]->(m)
        """, {"sid": session_id, "mid": machine_id})

    # Link parts discussed
    for part_id in entities.get("parts", []):
        db.write("""
            MATCH (s:Session {id: $sid})
            MATCH (p:Part {erp_id: $pid})
            MERGE (s)-[:DISCUSSED]->(p)
        """, {"sid": session_id, "pid": part_id})


def get_session_context(session_id: str) -> dict:
    """Get entities discussed in this session so far."""
    result = db.query("""
        MATCH (s:Session {id: $sid})
        OPTIONAL MATCH (s)-[:DISCUSSED]->(m:Machine)
        OPTIONAL MATCH (s)-[:DISCUSSED]->(p:Part)
        RETURN s.turn_count AS turns,
               collect(DISTINCT m.title) AS machines,
               collect(DISTINCT p.titel) AS parts
    """, {"sid": session_id})
    row = result_to_dicts(result)
    return row[0] if row else {}


def cleanup_old_sessions(max_age_hours: int = 24):
    """Remove sessions older than max_age_hours."""
    result = db.write("""
        MATCH (s:Session)
        WHERE s.last_active < datetime() - duration({hours: $hours})
        DETACH DELETE s
        RETURN count(s) AS deleted
    """, {"hours": max_age_hours})
    deleted = result_value(result, "deleted", 0)
    if deleted:
        print(f"  Cleaned up {deleted} old sessions")
    return deleted


# ── Main ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  Gramag Knowledge Graph — Mining & Session Setup")
    print("=" * 60)

    db.connect()

    # L3: Co-occurrence mining
    mine_co_occurrence(min_count=3)

    # Show graph stats after mining
    print("\n  Graph stats after mining:")
    stats = db.stats()
    for label, count in sorted(stats["nodes"].items()):
        print(f"    {label}: {count:,}")
    for rel, count in sorted(stats["relationships"].items()):
        print(f"    {rel}: {count:,}")
