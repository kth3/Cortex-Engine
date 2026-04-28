import os
import sys
import kuzu
from collections import defaultdict
from typing import Optional


def get_graph_db_path(workspace: str) -> str:
    """kuzu DB 경로 반환"""
    if workspace.endswith(".agents"):
        base_dir = workspace
    else:
        base_dir = os.path.join(workspace, ".agents")

    data_dir = os.path.join(base_dir, "data")
    db_dir = os.path.join(data_dir, "graph_db_store")
    os.makedirs(data_dir, exist_ok=True)
    return db_dir


def _kuzu_table(ntype: str) -> Optional[str]:
    """노드 타입 문자열 → Kuzu 테이블명"""
    t = (ntype or "").upper()
    if t in ("FUNCTION", "METHOD"): return "Function"
    if t == "CLASS":                 return "Class"
    if t in ("MODULE", "FILE"):      return "Module"
    if t == "EXTERNAL":              return "External"
    return None


class GraphDB:
    def __init__(self, workspace: str):
        self.db_path = get_graph_db_path(workspace)
        self.db = kuzu.Database(self.db_path)
        self.conn = kuzu.Connection(self.db)
        self._init_schema()

    def _init_schema(self):
        """Kuzu 그래프 스키마 생성"""
        try:
            self.conn.execute("CREATE NODE TABLE IF NOT EXISTS Module (name STRING, file_path STRING, PRIMARY KEY (name))")
            self.conn.execute("CREATE NODE TABLE IF NOT EXISTS Function (fqn STRING, name STRING, file_path STRING, PRIMARY KEY (fqn))")
            self.conn.execute("CREATE NODE TABLE IF NOT EXISTS Class (fqn STRING, name STRING, file_path STRING, PRIMARY KEY (fqn))")
            self.conn.execute("CREATE NODE TABLE IF NOT EXISTS External (fqn STRING, name STRING, PRIMARY KEY (fqn))")

            self.conn.execute("CREATE REL TABLE IF NOT EXISTS Imports (FROM Module TO Module, FROM Module TO External)")
            self.conn.execute("CREATE REL TABLE IF NOT EXISTS Calls (FROM Function TO Function, FROM Function TO Class, FROM Class TO Function, FROM Class TO Class, FROM Function TO External, FROM Class TO External, FROM Module TO External, FROM Module TO Function, FROM Module TO Class)")
            self.conn.execute("CREATE REL TABLE IF NOT EXISTS Defines (FROM Module TO Function, FROM Module TO Class)")
            self.conn.execute("CREATE REL TABLE IF NOT EXISTS Contains (FROM Class TO Function, FROM Class TO Class)")
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────
    # 배치 UPSERT API (UNWIND 기반, N+1 → O(type 수) 쿼리)
    # ──────────────────────────────────────────────────────────────────

    def batch_upsert_nodes(self, nodes: list) -> int:
        """
        UNWIND 배치로 노드 upsert.
        노드 N개를 타입별로 그룹핑 → 타입 수만큼의 쿼리(≤4)로 처리.

        Args:
            nodes: [{"fqn": str, "name": str, "file_path": str, "type": str}, ...]
        Returns:
            처리된 노드 수
        """
        by_type: dict[str, list] = defaultdict(list)
        for n in nodes:
            tbl = _kuzu_table(n.get("type", ""))
            if tbl:
                by_type[tbl].append(n)

        total = 0
        for tbl, rows in by_type.items():
            if not rows:
                continue
            try:
                if tbl == "Module":
                    self.conn.execute(
                        "UNWIND $rows AS row MERGE (n:Module {name: row.fqn}) SET n.file_path = row.fp",
                        {"rows": [{"fqn": r["fqn"], "fp": r.get("file_path", "")} for r in rows]}
                    )
                else:
                    self.conn.execute(
                        f"UNWIND $rows AS row MERGE (n:{tbl} {{fqn: row.fqn}}) SET n.name = row.name, n.file_path = row.fp",
                        {"rows": [{"fqn": r["fqn"], "name": r.get("name", ""), "fp": r.get("file_path", "")} for r in rows]}
                    )
                total += len(rows)
            except Exception as e:
                sys.stderr.write(f"[graph_db] batch_upsert_nodes({tbl}): {e}\n")

        return total

    def batch_upsert_edges(self, edges: list) -> int:
        """
        UNWIND 배치로 엣지 upsert.
        엣지 M개를 (src_type, tgt_type, rel) 조합별로 그룹핑 → 조합 수만큼의 쿼리로 처리.

        Args:
            edges: [{"src_fqn": str, "src_type": str,
                     "tgt_fqn": str, "tgt_type": str,
                     "edge_type": str}, ...]
        Returns:
            처리된 엣지 수
        """
        REL_MAP = {"CALLS": "Calls", "IMPORTS": "Imports",
                   "CONTAINS": "Contains", "DEFINES": "Defines"}

        # External 노드를 먼저 일괄 upsert (MATCH 전에 노드가 존재해야 함)
        externals = [e for e in edges if _kuzu_table(e.get("tgt_type", "")) == "External"]
        if externals:
            try:
                self.conn.execute(
                    "UNWIND $rows AS row MERGE (n:External {fqn: row.fqn}) SET n.name = row.name",
                    {"rows": [{"fqn": e["tgt_fqn"],
                                "name": e["tgt_fqn"].split("::")[-1]} for e in externals]}
                )
            except Exception as e:
                sys.stderr.write(f"[graph_db] batch_upsert_edges(External nodes): {e}\n")

        # (src_tbl, tgt_tbl, rel) 그룹별 UNWIND MATCH + MERGE
        groups: dict[tuple, list] = defaultdict(list)
        for e in edges:
            src_tbl = _kuzu_table(e.get("src_type", ""))
            tgt_tbl = _kuzu_table(e.get("tgt_type", ""))
            rel     = REL_MAP.get((e.get("edge_type") or "CALLS").upper())
            if src_tbl and tgt_tbl and rel:
                groups[(src_tbl, tgt_tbl, rel)].append(e)

        total = 0
        for (src_tbl, tgt_tbl, rel), group in groups.items():
            try:
                # Module 테이블의 PK는 name (fqn 아님) — MATCH 조건 분기
                if src_tbl == "Module":
                    self.conn.execute(
                        f"UNWIND $rows AS row "
                        f"MATCH (a:{src_tbl} {{name: row.s}}), (b:{tgt_tbl} {{fqn: row.t}}) "
                        f"MERGE (a)-[:{rel}]->(b)",
                        {"rows": [{"s": e["src_fqn"], "t": e["tgt_fqn"]} for e in group]}
                    )
                elif tgt_tbl == "Module":
                    self.conn.execute(
                        f"UNWIND $rows AS row "
                        f"MATCH (a:{src_tbl} {{fqn: row.s}}), (b:{tgt_tbl} {{name: row.t}}) "
                        f"MERGE (a)-[:{rel}]->(b)",
                        {"rows": [{"s": e["src_fqn"], "t": e["tgt_fqn"]} for e in group]}
                    )
                else:
                    self.conn.execute(
                        f"UNWIND $rows AS row "
                        f"MATCH (a:{src_tbl} {{fqn: row.s}}), (b:{tgt_tbl} {{fqn: row.t}}) "
                        f"MERGE (a)-[:{rel}]->(b)",
                        {"rows": [{"s": e["src_fqn"], "t": e["tgt_fqn"]} for e in group]}
                    )
                total += len(group)
            except Exception as e:
                sys.stderr.write(
                    f"[graph_db] batch_upsert_edges({src_tbl}->{tgt_tbl}[{rel}]): {e}\n"
                )

        return total

    def build_from_sqlite(self, sqlite_conn) -> dict:
        """
        SQLite nodes/edges 테이블을 읽어 Kuzu 그래프 DB 구축.
        [성능] UNWIND 배치 upsert로 N+1 쿼리 제거.
        """
        stats = {"nodes": 0, "edges": 0, "errors": 0}

        try:
            # 1. 기존 데이터 전체 삭제 (재빌드)
            for tbl in ["Calls", "Imports", "Defines", "Contains"]:
                try: self.conn.execute(f"MATCH ()-[r:{tbl}]->() DELETE r")
                except Exception: pass
            for tbl in ["Function", "Class", "Module", "External"]:
                try: self.conn.execute(f"MATCH (n:{tbl}) DELETE n")
                except Exception: pass

            # 2. nodes → 배치 upsert (1000행 청크, 타입별 UNWIND)
            cursor = sqlite_conn.execute(
                "SELECT fqn, name, file_path, type FROM nodes WHERE fqn IS NOT NULL AND fqn != ''"
            )
            while True:
                rows = cursor.fetchmany(1000)
                if not rows:
                    break
                node_batch = [
                    {"fqn": r[0], "name": r[1], "file_path": r[2] or "", "type": r[3]}
                    for r in rows
                ]
                n = self.batch_upsert_nodes(node_batch)
                stats["nodes"] += n

            # 3. edges → 배치 upsert (1000행 청크, 조합별 UNWIND)
            edge_cursor = sqlite_conn.execute(
                """SELECT n1.fqn, n1.type,
                          COALESCE(n2.fqn, e.target_id),
                          COALESCE(n2.type, 'EXTERNAL'),
                          e.type as etype
                   FROM edges e
                   JOIN nodes n1 ON n1.id = e.source_id
                   LEFT JOIN nodes n2 ON n2.id = e.target_id
                   WHERE n1.fqn IS NOT NULL
                     AND (n2.fqn IS NOT NULL OR e.target_id LIKE '__unresolved__%')"""
            )
            while True:
                edge_rows = edge_cursor.fetchmany(1000)
                if not edge_rows:
                    break
                edge_batch = [
                    {
                        "src_fqn":   r[0],
                        "src_type":  r[1],
                        "tgt_fqn":   r[2],
                        "tgt_type":  r[3],
                        "edge_type": r[4],
                    }
                    for r in edge_rows
                ]
                e = self.batch_upsert_edges(edge_batch)
                stats["edges"] += e

        except Exception as e:
            sys.stderr.write(f"[graph_db] build_from_sqlite error: {e}\n")
            stats["errors"] += 1

        return stats

    def execute(self, query: str, parameters: dict = None):
        """Cypher 쿼리 단건 실행 (하위 호환 유지)"""
        if parameters is None:
            parameters = {}
        return self.conn.execute(query, parameters)
