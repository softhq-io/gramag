"""FalkorDB connection wrapper for the proto graph (shared FalkorDB instance)."""

import os
import sys
import time

from falkordb import FalkorDB
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

from config import FALKORDB_HOST, FALKORDB_PORT
from db_helpers import result_value
from proto import PROTO_GRAPH_NAME


class ProtoGraphConnection:
    def __init__(self, graph_name: str = PROTO_GRAPH_NAME):
        self.host = FALKORDB_HOST
        self.port = FALKORDB_PORT
        self.graph_name = graph_name
        self._db = None
        self.graph = None

    def connect(self):
        if not self.graph:
            socket_timeout = float(os.getenv("PROTO_DB_SOCKET_TIMEOUT", "120"))
            socket_connect_timeout = float(os.getenv("PROTO_DB_SOCKET_CONNECT_TIMEOUT", "15"))
            self._db = FalkorDB(
                host=self.host, port=self.port,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_connect_timeout,
            )
            self.graph = self._db.select_graph(self.graph_name)
        return self.graph

    def reconnect(self):
        self._db = None
        self.graph = None
        return self.connect()

    def reset(self):
        self._db = None
        self.graph = None

    def _is_retryable_error(self, err: Exception) -> bool:
        msg = str(err).lower()
        return any(
            fragment in msg
            for fragment in (
                "busy",
                "closed",
                "connection",
                "defunct",
                "loading",
                "reset",
                "temporarily",
                "timeout",
                "try again",
            )
        )

    def _with_retry(self, fn, max_retries: int | None = None):
        if max_retries is None:
            max_retries = int(os.getenv("PROTO_DB_MAX_RETRIES", "12"))
        base_delay = float(os.getenv("PROTO_DB_RETRY_BASE_DELAY", "1.5"))
        max_delay = float(os.getenv("PROTO_DB_RETRY_MAX_DELAY", "60"))
        last = None
        for i in range(max_retries + 1):
            try:
                return fn()
            except (RedisConnectionError, RedisTimeoutError) as e:
                last = e
                if i >= max_retries:
                    raise
                delay = min(max_delay, base_delay * (2 ** i))
                print(
                    f"FalkorDB retryable error ({i + 1}/{max_retries}); "
                    f"sleeping {delay:.1f}s: {str(e)[:180]}",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(delay)
                self.reset()
            except Exception as e:
                if self._is_retryable_error(e):
                    last = e
                    if i >= max_retries:
                        raise
                    delay = min(max_delay, base_delay * (2 ** i))
                    print(
                        f"FalkorDB retryable error ({i + 1}/{max_retries}); "
                        f"sleeping {delay:.1f}s: {str(e)[:180]}",
                        file=sys.stderr,
                        flush=True,
                    )
                    time.sleep(delay)
                    self.reset()
                else:
                    raise
        raise last

    def query(self, cypher: str, params: dict | None = None):
        return self._with_retry(lambda: self.connect().query(cypher, params=params))

    def write(self, cypher: str, params: dict | None = None):
        return self.query(cypher, params)

    def node_count(self, label: str | None = None) -> int:
        q = f"MATCH (n:{label}) RETURN count(n) AS c" if label else "MATCH (n) RETURN count(n) AS c"
        return result_value(self.query(q), "c", 0)

    def rel_count(self, rel: str | None = None) -> int:
        q = f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c" if rel else "MATCH ()-[r]->() RETURN count(r) AS c"
        return result_value(self.query(q), "c", 0)

    def stats(self) -> dict:
        try:
            labels = [row[0] for row in (self.query("CALL db.labels()").result_set or [])]
        except Exception:
            labels = []
        try:
            rels = [row[0] for row in (self.query("CALL db.relationshipTypes()").result_set or [])]
        except Exception:
            rels = []
        nodes = {l: c for l in labels if (c := self.node_count(l)) > 0}
        relationships = {r: c for r in rels if (c := self.rel_count(r)) > 0}
        return {"nodes": nodes, "relationships": relationships}


proto_db = ProtoGraphConnection()
