import sys
import os

# scripts 폴더를 sys.path에 추가하여 cortex 모듈들을 임포트할 수 있도록 함
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

def main():
    modules = [
        "cortex.indexer_utils",
        "cortex.search_engine",
        "cortex.vector_engine",
        "cortex.vectorizer",
        "cortex.db",
        "cortex.graph_db",
        "cortex.memory",
        "cortex.persistent_memory",
        "cortex.skill_manager",
        "cortex.parsers",
        "cortex.indexing",
        "cortex.embeddings",
        "cortex.retrieval",
        "cortex.storage",
        "cortex.memories",
        "cortex.config",
        "cortex.scanner",
        "cortex.utils",
    ]

    failed = 0
    for name in modules:
        try:
            __import__(name)
            print(f"ok {name}")
        except ModuleNotFoundError as e:
            print(f"SKIP {name} (optional dependency missing): {e}")
        except Exception as e:
            print(f"FAIL {name}: {e}")
            failed += 1

    if failed == 0:
        print("cortex import verification ok")
    else:
        print(f"cortex import verification failed with {failed} errors")
        sys.exit(1)

if __name__ == "__main__":
    main()
