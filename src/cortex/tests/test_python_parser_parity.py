from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
from pathlib import Path


def _simplify_nodes(nodes: list[dict]) -> list[dict]:
    return [
        {
            "id": node["id"],
            "type": node["type"],
            "name": node["name"],
            "fqn": node["fqn"],
            "start_line": node["start_line"],
            "end_line": node["end_line"],
            "signature": node.get("signature"),
            "docstring": node.get("docstring"),
            "is_async": node.get("is_async"),
            "is_test": node.get("is_test"),
        }
        for node in nodes
    ]


def _simplify_edges(edges: list[dict]) -> list[dict]:
    return [
        {
            "source_id": edge["source_id"],
            "target_id": edge["target_id"],
            "type": edge["type"],
            "target_name": edge.get("target_name"),
            "target_kind_hint": edge.get("target_kind_hint"),
            "target_fqn_hint": edge.get("target_fqn_hint"),
            "call_site_line": edge.get("call_site_line"),
        }
        for edge in edges
    ]


def _parse_python(source: str, rel_path: str) -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        source_path = tmp_path / "sample.py"
        source_path.write_text(source, encoding="utf-8")

        env = os.environ.copy()
        env["CORTEX_HOME"] = str(tmp_path / ".cortex")
        env["CORTEX_NO_FILE_LOG"] = "1"

        proc = subprocess.run(
            [
                "cargo",
                "run",
                "--quiet",
                "--manifest-path",
                str(Path("rust") / "Cargo.toml"),
                "-p",
                "cortex-watcher",
                "--",
                "parse-file",
                "--rel",
                rel_path,
                "--file",
                str(source_path),
            ],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        return json.loads(proc.stdout)


def test_python_parser_parity_core_cases():
    cases = [
        {
            "name": "module import and class method call",
            "source": '''
                """module doc"""
                from pkg.helper import helper as h
                import os.path

                class Demo:
                    """class doc"""

                    def run(self, value: Foo) -> Bar:
                        h(value)
                        return value
            ''',
            "rel_path": "src/app.py",
            "nodes": [
                {
                    "type": "module",
                    "name": "app",
                    "fqn": "src/app.py",
                    "start_line": 1,
                    "end_line": 10,
                    "signature": None,
                    "docstring": "module doc",
                    "is_async": 0,
                    "is_test": 0,
                },
                {
                    "type": "class",
                    "name": "Demo",
                    "fqn": "src/app.py::Demo",
                    "start_line": 5,
                    "end_line": 10,
                    "signature": "class Demo:",
                    "docstring": "class doc",
                    "is_async": 0,
                    "is_test": 0,
                },
                {
                    "type": "method",
                    "name": "run",
                    "fqn": "src/app.py::Demo::run",
                    "start_line": 8,
                    "end_line": 10,
                    "signature": "def run(self, value: Foo) -> Bar:",
                    "docstring": "",
                    "is_async": 0,
                    "is_test": 0,
                },
            ],
            "edges": [
                {
                    "type": "IMPORTS",
                    "target_name": "pkg.helper",
                    "target_kind_hint": "module",
                    "target_fqn_hint": "pkg.helper.pkg.helper",
                    "call_site_line": 2,
                },
                {
                    "type": "IMPORTS",
                    "target_name": "h",
                    "target_kind_hint": "module",
                    "target_fqn_hint": "pkg.helper.helper",
                    "call_site_line": 2,
                },
                {
                    "type": "IMPORTS",
                    "target_name": "os",
                    "target_kind_hint": "module",
                    "target_fqn_hint": None,
                    "call_site_line": 3,
                },
                {
                    "type": "ANNOTATED_WITH",
                    "target_name": "Foo",
                    "target_kind_hint": "type",
                    "target_fqn_hint": None,
                    "call_site_line": 8,
                },
                {
                    "type": "ANNOTATED_WITH",
                    "target_name": "Bar",
                    "target_kind_hint": "type",
                    "target_fqn_hint": None,
                    "call_site_line": 8,
                },
                {
                    "type": "CONTAINS",
                    "target_name": None,
                    "target_kind_hint": None,
                    "target_fqn_hint": None,
                    "call_site_line": 8,
                },
                {
                    "type": "CALLS",
                    "target_name": "h",
                    "target_kind_hint": "function|method",
                    "target_fqn_hint": None,
                    "call_site_line": 9,
                },
            ],
        },
        {
            "name": "async function and test detection",
            "source": '''
                async def test_fetch(client: ApiClient) -> Response:
                    await client.fetch()
                    return client.response
            ''',
            "rel_path": "tests/test_api.py",
            "nodes": [
                {
                    "type": "module",
                    "name": "test_api",
                    "fqn": "tests/test_api.py",
                    "start_line": 1,
                    "end_line": 3,
                    "signature": None,
                    "docstring": "",
                    "is_async": 0,
                    "is_test": 0,
                },
                {
                    "type": "function",
                    "name": "test_fetch",
                    "fqn": "tests/test_api.py::test_fetch",
                    "start_line": 1,
                    "end_line": 3,
                    "signature": "async def test_fetch(client: ApiClient) -> Response:",
                    "docstring": "",
                    "is_async": 1,
                    "is_test": 1,
                },
            ],
            "edges": [
                {
                    "type": "ANNOTATED_WITH",
                    "target_name": "ApiClient",
                    "target_kind_hint": "type",
                    "target_fqn_hint": None,
                    "call_site_line": 1,
                },
                {
                    "type": "ANNOTATED_WITH",
                    "target_name": "Response",
                    "target_kind_hint": "type",
                    "target_fqn_hint": None,
                    "call_site_line": 1,
                },
                {
                    "type": "CALLS",
                    "target_name": "fetch",
                    "target_kind_hint": "function|method",
                    "target_fqn_hint": None,
                    "call_site_line": 2,
                },
            ],
        },
        {
            "name": "test class and nested method",
            "source": '''
                class TestService:
                    def test_execute(self):
                        helper()
            ''',
            "rel_path": "tests/test_service.py",
            "nodes": [
                {
                    "type": "module",
                    "name": "test_service",
                    "fqn": "tests/test_service.py",
                    "start_line": 1,
                    "end_line": 3,
                    "signature": None,
                    "docstring": "",
                    "is_async": 0,
                    "is_test": 0,
                },
                {
                    "type": "class",
                    "name": "TestService",
                    "fqn": "tests/test_service.py::TestService",
                    "start_line": 1,
                    "end_line": 3,
                    "signature": "class TestService:",
                    "docstring": "",
                    "is_async": 0,
                    "is_test": 1,
                },
                {
                    "type": "method",
                    "name": "test_execute",
                    "fqn": "tests/test_service.py::TestService::test_execute",
                    "start_line": 2,
                    "end_line": 3,
                    "signature": "def test_execute(self):",
                    "docstring": "",
                    "is_async": 0,
                    "is_test": 1,
                },
            ],
            "edges": [
                {
                    "type": "CONTAINS",
                    "target_name": None,
                    "target_kind_hint": None,
                    "target_fqn_hint": None,
                    "call_site_line": 2,
                },
                {
                    "type": "CALLS",
                    "target_name": "helper",
                    "target_kind_hint": "function|method",
                    "target_fqn_hint": None,
                    "call_site_line": 3,
                },
            ],
        },
    ]

    for case in cases:
        result = _parse_python(textwrap.dedent(case["source"]).strip("\n") + "\n", case["rel_path"])

        nodes = result["nodes"]
        edges = result["edges"]

        expected_nodes = []
        for item in case["nodes"]:
            expected_nodes.append(
                {
                    "id": None,
                    "type": item["type"],
                    "name": item["name"],
                    "fqn": item["fqn"],
                    "start_line": item["start_line"],
                    "end_line": item["end_line"],
                    "signature": item["signature"],
                    "docstring": item["docstring"],
                    "is_async": item["is_async"],
                    "is_test": item["is_test"],
                }
            )

        simplified_nodes = _simplify_nodes(nodes)
        assert [node["type"] for node in simplified_nodes] == [node["type"] for node in expected_nodes], case["name"]
        assert [node["name"] for node in simplified_nodes] == [node["name"] for node in expected_nodes], case["name"]
        assert [node["fqn"] for node in simplified_nodes] == [node["fqn"] for node in expected_nodes], case["name"]
        assert [node["start_line"] for node in simplified_nodes] == [node["start_line"] for node in expected_nodes], case["name"]
        assert [node["end_line"] for node in simplified_nodes] == [node["end_line"] for node in expected_nodes], case["name"]
        assert [node["signature"] for node in simplified_nodes] == [node["signature"] for node in expected_nodes], case["name"]
        assert [node["docstring"] for node in simplified_nodes] == [node["docstring"] for node in expected_nodes], case["name"]
        assert [node["is_async"] for node in simplified_nodes] == [node["is_async"] for node in expected_nodes], case["name"]
        assert [node["is_test"] for node in simplified_nodes] == [node["is_test"] for node in expected_nodes], case["name"]

        simplified_edges = _simplify_edges(edges)
        assert [edge["type"] for edge in simplified_edges] == [edge["type"] for edge in case["edges"]], case["name"]
        assert [edge["target_name"] for edge in simplified_edges] == [edge["target_name"] for edge in case["edges"]], case["name"]
        assert [edge["target_kind_hint"] for edge in simplified_edges] == [edge["target_kind_hint"] for edge in case["edges"]], case["name"]
        assert [edge["target_fqn_hint"] for edge in simplified_edges] == [edge["target_fqn_hint"] for edge in case["edges"]], case["name"]
        assert [edge["call_site_line"] for edge in simplified_edges] == [edge["call_site_line"] for edge in case["edges"]], case["name"]
