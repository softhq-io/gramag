"""FalkorDB connection wrapper for Gramag Knowledge Graph."""

import os
import time
from falkordb import FalkorDB
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
from db_helpers import result_to_dicts, result_single, result_value
from config import FALKORDB_HOST, FALKORDB_PORT, FALKORDB_GRAPH

VECTOR_INDEX_NAME = "manual_section_embeddings"
VECTOR_DIMENSIONS = 3072


class GraphConnection:
    def __init__(self):
        self.host = FALKORDB_HOST
        self.port = FALKORDB_PORT
        self.graph_name = FALKORDB_GRAPH
        self._db = None
        self.graph = None

    def connect(self):
        """Connect to FalkorDB, returns graph handle."""
        if not self.graph:
            self._db = FalkorDB(
                host=self.host,
                port=self.port,
                socket_timeout=30,
                socket_connect_timeout=10,
            )
            self.graph = self._db.select_graph(self.graph_name)
        return self.graph

    def reconnect(self):
        """Force reconnection."""
        self._db = None
        self.graph = None
        return self.connect()

    def close(self):
        self._db = None
        self.graph = None

    def _execute_with_retry(self, query_func, max_retries=2):
        """Execute query with automatic retry on connection failure."""
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return query_func()
            except (RedisConnectionError, RedisTimeoutError) as e:
                last_error = e
                if attempt < max_retries:
                    self.reconnect()
                else:
                    raise
            except Exception as e:
                error_msg = str(e).lower()
                if "defunct" in error_msg or "connection" in error_msg:
                    last_error = e
                    if attempt < max_retries:
                        self.reconnect()
                    else:
                        raise
                else:
                    raise
        raise last_error

    def query(self, cypher: str, params: dict | None = None):
        """Execute a read query with retry."""
        def _q():
            graph = self.connect()
            return graph.query(cypher, params=params)
        return self._execute_with_retry(_q)

    def write(self, cypher: str, params: dict | None = None):
        """Execute a write query with retry."""
        return self.query(cypher, params)

    def verify(self) -> bool:
        """Verify the connection works."""
        try:
            result = self.query("RETURN 1 AS test")
            return result_value(result, "test") == 1
        except Exception:
            return False

    def node_count(self, label: str | None = None) -> int:
        """Count nodes, optionally filtered by label."""
        if label:
            result = self.query(f"MATCH (n:{label}) RETURN count(n) AS c")
        else:
            result = self.query("MATCH (n) RETURN count(n) AS c")
        return result_value(result, "c", 0)

    def rel_count(self, rel_type: str | None = None) -> int:
        """Count relationships, optionally filtered by type."""
        if rel_type:
            result = self.query(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS c")
        else:
            result = self.query("MATCH ()-[r]->() RETURN count(r) AS c")
        return result_value(result, "c", 0)

    def stats(self) -> dict:
        """Return node and relationship counts by type (dynamic discovery)."""
        # Discover all labels dynamically
        try:
            label_result = self.query("CALL db.labels()")
            labels = [row[0] for row in (label_result.result_set or [])]
        except Exception:
            labels = []

        # Discover all relationship types dynamically
        try:
            rel_result = self.query("CALL db.relationshipTypes()")
            rels = [row[0] for row in (rel_result.result_set or [])]
        except Exception:
            rels = []

        nodes = {}
        for l in labels:
            c = self.node_count(l)
            if c > 0:
                nodes[l] = c
        relationships = {}
        for r in rels:
            c = self.rel_count(r)
            if c > 0:
                relationships[r] = c
        return {"nodes": nodes, "relationships": relationships}


# Singleton
db = GraphConnection()
