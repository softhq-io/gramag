"""FalkorDB connection wrapper for the proto graph (shared FalkorDB instance)."""

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
            self._db = FalkorDB(
                host=self.host, port=self.port,
                socket_timeout=30, socket_connect_timeout=10,
            )
            self.graph = self._db.select_graph(self.graph_name)
        return self.graph

    def reconnect(self):
        self._db = None
        self.graph = None
        return self.connect()

    def _with_retry(self, fn, max_retries=2):
        last = None
        for i in range(max_retries + 1):
            try:
                return fn()
            except (RedisConnectionError, RedisTimeoutError) as e:
                last = e
                if i < max_retries:
                    self.reconnect()
                else:
                    raise
            except Exception as e:
                msg = str(e).lower()
                if "defunct" in msg or "connection" in msg:
                    last = e
                    if i < max_retries:
                        self.reconnect()
                    else:
                        raise
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
