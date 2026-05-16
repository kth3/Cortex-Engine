---
trigger: model_decision
description: Cortex Architectural Integrity Rule (v1.0)
---

# Cortex Architectural Integrity Rule (v1.0)

> [!IMPORTANT]
> 이 규칙은 AI 에이전트의 "최소 저항 경로 본능"에 의한 시스템 비대화를 방지하기 위해 설계되었습니다.

## 1. 모노글롯(Monoglot) 유지 원칙
- **100% Python**: 모든 오케스트레이션 로직, 파서, 훅은 Python으로 작성합니다.
- **이유**: AI의 코드 수정 성공률(AI Ergonomics) 극대화, 의존성 지옥 및 IPC 병목 회피.

## 2. 모놀리식 하드코딩 금지 (Anti-Monolithic)
- **Core Separation**: MCP 진입점과 `cortex.indexing.cli`에는 직접적인 비즈니스 로직이나 언어별 상세 파싱 로직을 추가하지 않습니다. 인덱싱 흐름은 `cortex.indexing.*` 파이프라인 모듈로 분리합니다.
- **Side-Effect Isolation**: 파일 수정 후의 동기화, 알림, 추가 검증 등 모든 사후 처리는 반드시 `hooks/` 폴더의 독립된 스크립트로 분리합니다.

## 3. 전략 패턴(Strategy Pattern) 기반 확장
- **Parser Registry**: 신규 언어 지원 시 `.cortex/scripts/cortex/parsers/`에 `*_parser.py` 모듈을 추가하고 `SUPPORTED_EXTENSIONS`를 정의하십시오.
- **Dynamic Dispatch**: `HookManager`를 통해 런타임에 필요한 훅만 호출되도록 설계하여, 엔진의 복잡도를 낮게 유지하십시오.

## 4. 예방적 방어 (Preventive Defense)
- **Guard Hooks**: 위험한 도구(`pc_strict_replace` 등) 호출 전에는 `before_tool_call` 훅을 통해 파라미터의 정당성을 자율 검증해야 합니다.

## 5. 개발/자가 모드 정책 (Development / Self Mode)
- `.cortex` 소스 트리 자체에서 개발할 때는 `uv run ...`으로 실행하고, 실제 작업 대상은 `CORTEX_WORKSPACE` 또는 현재 cwd의 상위 `.git` 탐색 결과로 결정합니다.
- 데이터는 기본적으로 `CORTEX_DATA_HOME` 아래 `workspaces/<key>/`에 격리합니다. 여러 checkout을 같은 워크스페이스로 묶을 때만 `CORTEX_WORKSPACE_KEY`를 명시합니다.
- WSL2에서는 Linux 홈 디렉터리 아래 checkout을 권장합니다. `/mnt/c/...` 같은 Windows mounted drive에서는 `portalocker` 기반 advisory lock 동작이 파일시스템 경계에서 불안정할 수 있으므로 장시간 daemon/worker 검증 경로로 쓰지 않습니다.
