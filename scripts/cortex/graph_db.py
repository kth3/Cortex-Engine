import os
import kuzu
import sys
from typing import Optional

def get_graph_db_path(workspace: str) -> str:
    """kuzu DB 경로 반환"""
    if workspace.endswith(".cortex"):
        base_dir = workspace
    else:
        base_dir = os.path.join(workspace, ".cortex")
    
    db_dir = os.path.join(base_dir, "graph.kuzu")
    os.makedirs(base_dir, exist_ok=True)
    return db_dir

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
            
            # Edge Tables
            self.conn.execute("CREATE REL TABLE IF NOT EXISTS Imports (FROM Module TO Module, FROM Module TO External)")
            self.conn.execute("CREATE REL TABLE IF NOT EXISTS Calls (FROM Function TO Function, FROM Function TO Class, FROM Class TO Function, FROM Class TO Class, FROM Function TO External, FROM Class TO External, FROM Module TO External, FROM Module TO Function, FROM Module TO Class)")
            self.conn.execute("CREATE REL TABLE IF NOT EXISTS Defines (FROM Module TO Function, FROM Module TO Class)")
            self.conn.execute("CREATE REL TABLE IF NOT EXISTS Contains (FROM Class TO Function, FROM Class TO Class)")
        except Exception:
            pass

    def build_from_sqlite(self, sqlite_conn) -> dict:
        """SQLite nodes/edges 테이블을 읽어 Kuzu 그래프 DB 구축"""
        stats = {"nodes": 0, "edges": 0, "errors": 0}

        try:
            # 1. 기존 데이터 전체 삭제 (재빌드)
            for tbl in ["Calls", "Imports", "Defines", "Contains"]:
                try:
                    self.conn.execute(f"MATCH ()-[r:{tbl}]->() DELETE r")
                except Exception:
                    pass
            for tbl in ["Function", "Class", "Module", "External"]:
                try:
                    self.conn.execute(f"MATCH (n:{tbl}) DELETE n")
                except Exception:
                    pass

            # 2. nodes → Kuzu 노드 삽입
            cursor = sqlite_conn.execute("SELECT fqn, name, file_path, type FROM nodes WHERE fqn IS NOT NULL AND fqn != ''")
            while True:
                rows = cursor.fetchmany(1000)
                if not rows:
                    break
                for row in rows:
                    fqn, name, file_path, ntype = row[0], row[1], row[2], row[3]
                    ntype_upper = (ntype or "").upper()
                    try:
                        if ntype_upper in ("FUNCTION", "METHOD"):
                            self.conn.execute(
                                "MERGE (n:Function {fqn: $fqn}) SET n.name = $name, n.file_path = $fp",
                                {"fqn": fqn, "name": name, "fp": file_path or ""}
                            )
                        elif ntype_upper == "CLASS":
                            self.conn.execute(
                                "MERGE (n:Class {fqn: $fqn}) SET n.name = $name, n.file_path = $fp",
                                {"fqn": fqn, "name": name, "fp": file_path or ""}
                            )
                        elif ntype_upper in ("MODULE", "FILE"):
                            self.conn.execute(
                                "MERGE (n:Module {name: $fqn}) SET n.file_path = $fp",
                                {"fqn": fqn, "fp": file_path or ""}
                            )
                        stats["nodes"] += 1
                    except Exception as e:
                        stats["errors"] += 1

            # 3. edges → Kuzu 엣지 삽입
            edge_cursor = sqlite_conn.execute(
                """SELECT n1.fqn, n1.type, COALESCE(n2.fqn, e.target_id), COALESCE(n2.type, 'EXTERNAL'), e.type as etype
                   FROM edges e
                   JOIN nodes n1 ON n1.id = e.source_id
                   LEFT JOIN nodes n2 ON n2.id = e.target_id
                   WHERE n1.fqn IS NOT NULL AND (n2.fqn IS NOT NULL OR e.target_id LIKE '__unresolved__%')"""
            )

            def _kuzu_table(ntype: str) -> Optional[str]:
                t = (ntype or "").upper()
                if t in ("FUNCTION", "METHOD"): return "Function"
                if t == "CLASS": return "Class"
                if t in ("MODULE", "FILE"): return "Module"
                if t == "EXTERNAL": return "External"
                return None

            while True:
                edge_rows = edge_cursor.fetchmany(1000)
                if not edge_rows:
                    break
                for row in edge_rows:
                    src_fqn, src_type, tgt_fqn, tgt_type, etype = row
                    src_tbl = _kuzu_table(src_type)
                    tgt_tbl = _kuzu_table(tgt_type)
                    if not src_tbl or not tgt_tbl:
                        continue
                    if tgt_tbl == "External":
                        clean_name = tgt_fqn.split("::")[-1] if "::" in tgt_fqn else tgt_fqn
                        self.conn.execute(
                            "MERGE (n:External {fqn: $fqn}) SET n.name = $name",
                            {"fqn": tgt_fqn, "name": clean_name}
                        )
                    try:
                        if etype == "CALLS":
                            self.conn.execute(
                                f"MATCH (a:{src_tbl} {{fqn: $s}}), (b:{tgt_tbl} {{fqn: $t}}) MERGE (a)-[:Calls]->(b)",
                                {"s": src_fqn, "t": tgt_fqn}
                            )
                        elif etype == "IMPORTS":
                            self.conn.execute(
                                f"MATCH (a:{src_tbl} {{fqn: $s}}), (b:{tgt_tbl} {{fqn: $t}}) MERGE (a)-[:Imports]->(b)",
                                {"s": src_fqn, "t": tgt_fqn}
                            )
                        elif etype == "CONTAINS":
                            self.conn.execute(
                                f"MATCH (a:{src_tbl} {{fqn: $s}}), (b:{tgt_tbl} {{fqn: $t}}) MERGE (a)-[:Contains]->(b)",
                                {"s": src_fqn, "t": tgt_fqn}
                            )
                        stats["edges"] += 1
                    except Exception:
                        stats["errors"] += 1

        except Exception as e:
            sys.stderr.write(f"[graph_db] build_from_sqlite error: {e}\n")
            stats["errors"] += 1

        return stats

    def execute(self, query: str, parameters: dict = None):
        """Cypher 쿼리 실행"""
        if parameters is None:
            parameters = {}
        return self.conn.execute(query, parameters)
