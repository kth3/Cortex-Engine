import unittest
from cortex.parsers.treesitter_ts_parser import parse_ts_file

class TestTypeScriptParser(unittest.TestCase):
    def test_parse_module_node(self):
        source = "export class MyClass {}"
        result = parse_ts_file("test.ts", source)
        nodes = result["nodes"]
        module_node = next((n for n in nodes if n["type"] == "module"), None)
        self.assertIsNotNone(module_node)

    def test_edge_source_integrity(self):
        source = """
        import { Bar } from './bar';
        export class A {
            foo() {}
        }
        """
        result = parse_ts_file("A.ts", source)
        nodes = result["nodes"]
        edges = result["edges"]
        node_ids = {n["id"] for n in nodes}
        for edge in edges:
            self.assertIn(edge["source_id"], node_ids, f"Edge source_id {edge['source_id']} not in nodes!")

if __name__ == '__main__':
    unittest.main()
