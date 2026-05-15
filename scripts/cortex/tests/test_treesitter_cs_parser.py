import unittest
from cortex.parsers.treesitter_cs_parser import parse_csharp_file

class TestCSharpParser(unittest.TestCase):
    def test_parse_module_node(self):
        source = "public class MyClass {}"
        result = parse_csharp_file("test.cs", source)
        nodes = result["nodes"]
        module_node = next((n for n in nodes if n["type"] == "module"), None)
        self.assertIsNotNone(module_node)
        self.assertEqual(module_node["id"], nodes[0]["id"]) # usually first

    def test_unity_lifecycle_and_coroutine(self):
        source = """
        using UnityEngine;
        using System.Collections;
        public class MyGame : MonoBehaviour {
            void Start() {}
            IEnumerator MyCoroutine() { yield return null; }
        }
        """
        result = parse_csharp_file("MyGame.cs", source)
        nodes = result["nodes"]
        start_method = next((n for n in nodes if n["name"] == "Start"), None)
        self.assertIsNotNone(start_method)
        self.assertTrue(start_method["unity_lifecycle"])

        coroutine_method = next((n for n in nodes if n["name"] == "MyCoroutine"), None)
        self.assertIsNotNone(coroutine_method)
        self.assertEqual(coroutine_method["is_async"], 1)
        self.assertTrue(coroutine_method["unity_coroutine"])

        class_node = next((n for n in nodes if n["type"] == "class" and n["name"] == "MyGame"), None)
        self.assertIsNotNone(class_node)
        self.assertTrue(class_node["unity_mono"])
        self.assertEqual(class_node["unity_bases"].strip(), "MonoBehaviour")

    def test_edge_source_integrity(self):
        source = """
        public class A : B {
            public void Foo() { Bar(); }
        }
        """
        result = parse_csharp_file("A.cs", source)
        nodes = result["nodes"]
        edges = result["edges"]
        node_ids = {n["id"] for n in nodes}
        for edge in edges:
            self.assertIn(edge["source_id"], node_ids, f"Edge source_id {edge['source_id']} not in nodes!")
            self.assertIn("target_name", edge)
            self.assertIn("target_kind_hint", edge)
            self.assertTrue(edge["target_id"].startswith("__unresolved__::"))

if __name__ == '__main__':
    unittest.main()
