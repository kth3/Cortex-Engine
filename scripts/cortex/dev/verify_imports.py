import sys
import os

# scripts 폴더를 sys.path에 추가하여 cortex 모듈들을 임포트할 수 있도록 함
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

OPTIONAL_DEPENDENCIES = {
    "numpy",
    "torch",
    "sentence_transformers",
    "transformers",
    "tree_sitter",
    "tree_sitter_c_sharp",
    "tree_sitter_typescript",
    "pypdf",
    "sqlite_vec",
    "kuzu",
    "huggingface_hub",
}

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

    ok_count = 0
    skipped_count = 0
    failed_count = 0

    for name in modules:
        try:
            __import__(name)
            print(f"ok {name}")
            ok_count += 1
        except ModuleNotFoundError as e:
            missing = e.name or ""
            if missing.startswith("cortex") or missing not in OPTIONAL_DEPENDENCIES:
                print(f"FAIL {name} (missing required module: {missing})")
                failed_count += 1
            else:
                print(f"SKIP {name} (optional dependency missing: {missing})")
                skipped_count += 1
        except Exception as e:
            print(f"FAIL {name}: {e}")
            failed_count += 1

    print("\n---")
    print(f"ok: {ok_count}")
    print(f"skipped optional: {skipped_count}")
    print(f"failed: {failed_count}")

    if failed_count == 0:
        print("cortex import verification ok")
    else:
        print(f"cortex import verification failed with {failed_count} errors")
        sys.exit(1)

if __name__ == "__main__":
    main()
