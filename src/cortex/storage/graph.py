from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def _kuzu_table(ntype: str | None) -> str | None:
    t = (ntype or "").upper()
    if t in ("FUNCTION", "METHOD"):
        return "Function"
    if t == "CLASS":
        return "Class"
    if t in ("MODULE", "FILE"):
        return "Module"
    if t == "EXTERNAL":
        return "External"
    return None


def _rel_table(edge_type: str | None) -> str | None:
    return {
        "CALLS": "Calls",
        "IMPORTS": "Imports",
        "CONTAINS": "Contains",
        "DEFINES": "Defines",
        "INHERITS": "Inherits",
        "ANNOTATED_WITH": "AnnotatedWith",
    }.get((edge_type or "CALLS").upper())


class GraphDB:
    def __init__(self, graph_path: str | Path):
        import kuzu

        self.db_path = str(graph_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = kuzu.Database(self.db_path)
        self.conn = kuzu.Connection(self.db)
        self._init_schema()

    def _execute_ignore(self, query: str, parameters: dict[str, Any] | None = None) -> None:
        try:
            self.conn.execute(query, parameters or {})
        except Exception:
            pass

    def _init_schema(self) -> None:
        self._execute_ignore("CREATE NODE TABLE IF NOT EXISTS Module (name STRING, file_path STRING, PRIMARY KEY (name))")
        self._execute_ignore("CREATE NODE TABLE IF NOT EXISTS Function (fqn STRING, name STRING, file_path STRING, PRIMARY KEY (fqn))")
        self._execute_ignore("CREATE NODE TABLE IF NOT EXISTS Class (fqn STRING, name STRING, file_path STRING, PRIMARY KEY (fqn))")
        self._execute_ignore("CREATE NODE TABLE IF NOT EXISTS External (fqn STRING, name STRING, PRIMARY KEY (fqn))")
        self._execute_ignore("CREATE REL TABLE IF NOT EXISTS Imports (FROM Module TO Module, FROM Module TO External)")
        self._execute_ignore("CREATE REL TABLE IF NOT EXISTS Calls (FROM Function TO Function, FROM Function TO Class, FROM Class TO Function, FROM Class TO Class, FROM Function TO External, FROM Class TO External, FROM Module TO External, FROM Module TO Function, FROM Module TO Class)")
        self._execute_ignore("CREATE REL TABLE IF NOT EXISTS Defines (FROM Module TO Function, FROM Module TO Class)")
        self._execute_ignore("CREATE REL TABLE IF NOT EXISTS Contains (FROM Class TO Function, FROM Class TO Class)")
        self._execute_ignore("CREATE REL TABLE IF NOT EXISTS Inherits (FROM Class TO Class, FROM Class TO External)")
        self._execute_ignore("CREATE REL TABLE IF NOT EXISTS AnnotatedWith (FROM Function TO Class, FROM Function TO External, FROM Class TO Class, FROM Class TO External)")

    def clear(self) -> None:
        for tbl in ["Calls", "Imports", "Defines", "Contains", "Inherits", "AnnotatedWith"]:
            self._execute_ignore(f"MATCH ()-[r:{tbl}]->() DELETE r")
        for tbl in ["Function", "Class", "Module", "External"]:
            self._execute_ignore(f"MATCH (n:{tbl}) DETACH DELETE n")

    def batch_upsert_nodes(self, nodes: list[dict[str, Any]]) -> int:
        by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in nodes:
            tbl = _kuzu_table(node.get("type"))
            if tbl:
                by_type[tbl].append(node)

        total = 0
        for tbl, rows in by_type.items():
            if tbl == "Module":
                self.conn.execute(
                    "UNWIND $rows AS row MERGE (n:Module {name: row.fqn}) SET n.file_path = row.fp",
                    {"rows": [{"fqn": r["fqn"], "fp": r.get("file_path", "")} for r in rows]},
                )
            else:
                self.conn.execute(
                    f"UNWIND $rows AS row MERGE (n:{tbl} {{fqn: row.fqn}}) SET n.name = row.name, n.file_path = row.fp",
                    {"rows": [{"fqn": r["fqn"], "name": r.get("name", ""), "fp": r.get("file_path", "")} for r in rows]},
                )
            total += len(rows)
        return total

    def batch_upsert_edges(self, edges: list[dict[str, Any]]) -> int:
        externals = [e for e in edges if _kuzu_table(e.get("tgt_type")) == "External"]
        if externals:
            self.conn.execute(
                "UNWIND $rows AS row MERGE (n:External {fqn: row.fqn}) SET n.name = row.name",
                {"rows": [{"fqn": e["tgt_fqn"], "name": e.get("tgt_name") or e["tgt_fqn"].split("::")[-1]} for e in externals]},
            )

        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for edge in edges:
            src_tbl = _kuzu_table(edge.get("src_type"))
            tgt_tbl = _kuzu_table(edge.get("tgt_type"))
            rel = _rel_table(edge.get("edge_type"))
            if src_tbl and tgt_tbl and rel:
                groups[(src_tbl, tgt_tbl, rel)].append(edge)

        total = 0
        for (src_tbl, tgt_tbl, rel), group in groups.items():
            if src_tbl == "Module" and tgt_tbl == "Module":
                query = f"UNWIND $rows AS row MATCH (a:Module {{name: row.s}}), (b:Module {{name: row.t}}) MERGE (a)-[:{rel}]->(b)"
            elif src_tbl == "Module":
                query = f"UNWIND $rows AS row MATCH (a:Module {{name: row.s}}), (b:{tgt_tbl} {{fqn: row.t}}) MERGE (a)-[:{rel}]->(b)"
            elif tgt_tbl == "Module":
                query = f"UNWIND $rows AS row MATCH (a:{src_tbl} {{fqn: row.s}}), (b:Module {{name: row.t}}) MERGE (a)-[:{rel}]->(b)"
            else:
                query = f"UNWIND $rows AS row MATCH (a:{src_tbl} {{fqn: row.s}}), (b:{tgt_tbl} {{fqn: row.t}}) MERGE (a)-[:{rel}]->(b)"
            self.conn.execute(query, {"rows": [{"s": e["src_fqn"], "t": e["tgt_fqn"]} for e in group]})
            total += len(group)
        return total

    def build_from_sqlite(self, sqlite_path: str | Path) -> dict[str, int]:
        stats = {"nodes": 0, "edges": 0, "errors": 0}
        conn = sqlite3.connect(str(sqlite_path))
        try:
            self.clear()
            cursor = conn.execute("SELECT fqn, name, file_path, type FROM nodes WHERE category = 'SOURCE' AND fqn IS NOT NULL AND fqn != ''")
            while rows := cursor.fetchmany(1000):
                stats["nodes"] += self.batch_upsert_nodes([
                    {"fqn": r[0], "name": r[1], "file_path": r[2] or "", "type": r[3]} for r in rows
                ])

            edge_cursor = conn.execute(
                """SELECT n1.fqn, n1.type,
                          COALESCE(n2.fqn, e.target_fqn_hint, e.target_id),
                          COALESCE(n2.type, CASE WHEN e.target_id LIKE '__unresolved%' THEN 'EXTERNAL' ELSE e.target_kind_hint END),
                          e.type,
                          e.target_name
                   FROM edges e
                   JOIN nodes n1 ON n1.id = e.source_id
                   LEFT JOIN nodes n2 ON n2.id = e.target_id
                   WHERE n1.fqn IS NOT NULL
                     AND (n2.fqn IS NOT NULL OR e.resolution_status = 'unresolved')"""
            )
            while rows := edge_cursor.fetchmany(1000):
                stats["edges"] += self.batch_upsert_edges([
                    {
                        "src_fqn": r[0],
                        "src_type": r[1],
                        "tgt_fqn": r[2],
                        "tgt_type": r[3],
                        "edge_type": r[4],
                        "tgt_name": r[5],
                    }
                    for r in rows
                ])
        except Exception as exc:
            print(f"[graph_db] build_from_sqlite error: {exc}", file=sys.stderr)
            stats["errors"] += 1
        finally:
            conn.close()
        return stats

    def neighbors(self, node_fqn: str, direction: str, limit: int) -> list[str]:
        tables = ["Function", "Class", "Module", "External"]
        out: list[str] = []
        if direction in ("callees", "both"):
            for src_tbl in tables:
                for tgt_tbl in tables:
                    out.extend(self._query_neighbors(src_tbl, tgt_tbl, node_fqn, outgoing=True, limit=limit))
        if len(out) < limit and direction in ("callers", "both"):
            for src_tbl in tables:
                for tgt_tbl in tables:
                    out.extend(self._query_neighbors(src_tbl, tgt_tbl, node_fqn, outgoing=False, limit=limit - len(out)))
        return list(dict.fromkeys(out))[:limit]

    def _query_neighbors(self, src_tbl: str, tgt_tbl: str, node_fqn: str, outgoing: bool, limit: int) -> list[str]:
        if limit <= 0:
            return []
        if outgoing:
            where_field = "name" if src_tbl == "Module" else "fqn"
            return_field = "name" if tgt_tbl == "Module" else "fqn"
            query = f"MATCH (src:{src_tbl})-[]->(dst:{tgt_tbl}) WHERE src.{where_field} = $id RETURN dst.{return_field}"
        else:
            where_field = "name" if tgt_tbl == "Module" else "fqn"
            return_field = "name" if src_tbl == "Module" else "fqn"
            query = f"MATCH (src:{src_tbl})-[]->(dst:{tgt_tbl}) WHERE dst.{where_field} = $id RETURN src.{return_field}"
        try:
            result = self.conn.execute(query, {"id": node_fqn})
            values = []
            while result.has_next() and len(values) < limit:
                row = result.get_next()
                values.append(str(row[0]))
            return values
        except Exception:
            return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cortex.storage.graph")
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync")
    sync.add_argument("--sqlite", required=True)
    sync.add_argument("--graph", required=True)
    neighbors = sub.add_parser("neighbors")
    neighbors.add_argument("--graph", required=True)
    neighbors.add_argument("--node", required=True)
    neighbors.add_argument("--direction", default="both", choices=["callers", "callees", "both"])
    neighbors.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)

    graph = GraphDB(args.graph)
    if args.command == "sync":
        print(json.dumps(graph.build_from_sqlite(args.sqlite)))
    else:
        print(json.dumps({"neighbors": graph.neighbors(args.node, args.direction, args.limit)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
