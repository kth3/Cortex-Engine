import os
import importlib
import pkgutil
import sys

class ParserRegistry:
    def __init__(self):
        self.parsers = {}
        # 초기화 시점에 파서 로드
        self._load_parsers()

    def _load_parsers(self):
        """scripts/cortex/parsers 폴더 내의 *_parser.py 모듈들을 동적으로 로드합니다."""
        parsers_dir = os.path.dirname(__file__)
        package_name = "cortex.parsers"

        for _, module_name, _ in pkgutil.iter_modules([parsers_dir]):
            if module_name.endswith("_parser"):
                try:
                    # 동적 임포트
                    full_module_name = f"{package_name}.{module_name}"
                    module = importlib.import_module(full_module_name)
                    
                    # 모듈 내의 SUPPORTED_EXTENSIONS 딕셔너리 확인
                    if hasattr(module, "SUPPORTED_EXTENSIONS"):
                        ext_map = getattr(module, "SUPPORTED_EXTENSIONS")
                        for ext, info in ext_map.items():
                            # info는 (language_name, parser_function) 튜플이어야 함
                            self.parsers[ext] = info
                except Exception as e:
                    sys.stderr.write(f"[ParserRegistry] Failed to load {module_name}: {e}\n")

    def get_parser(self, ext):
        """확장자에 해당하는 (language, parser_func) 반환. 없으면 (None, None)"""
        return self.parsers.get(ext, (None, None))

    def get_supported_extensions(self):
        """지원하는 모든 확장자 목록 반환"""
        return list(self.parsers.keys())

# 싱글톤 인스턴스로 제공
registry = ParserRegistry()
