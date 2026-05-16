def _string_property(description=None, enum=None, default=None):
    prop = {"type": "string"}
    if description is not None:
        prop["description"] = description
    if enum is not None:
        prop["enum"] = list(enum)
    if default is not None:
        prop["default"] = default
    return prop


def _integer_property(description=None, default=None):
    prop = {"type": "integer"}
    if description is not None:
        prop["description"] = description
    if default is not None:
        prop["default"] = default
    return prop


def _boolean_property(description=None, default=None):
    prop = {"type": "boolean"}
    if description is not None:
        prop["description"] = description
    if default is not None:
        prop["default"] = default
    return prop


def _array_string_property():
    return {
        "type": "array",
        "items": {"type": "string"},
    }


def _object_property():
    return {"type": "object"}


def _input_schema(properties=None, required=None):
    schema = {"type": "object"}
    if properties:
        schema["properties"] = properties
    if required:
        schema["required"] = list(required)
    return schema


def _tool(name, description, properties=None, required=None):
    return {
        "name": name,
        "description": description,
        "inputSchema": _input_schema(properties, required),
    }


TOOL_PC_REINDEX = "pc_reindex"
TOOL_PC_INDEX_STATUS = "pc_index_status"
TOOL_PC_INDEX_ROOTS_LIST = "pc_index_roots_list"
TOOL_PC_INDEX_ROOTS_ADD = "pc_index_roots_add"
TOOL_PC_INDEX_ROOTS_REMOVE = "pc_index_roots_remove"
TOOL_PC_CAPSULE = "pc_capsule"
TOOL_PC_SKELETON = "pc_skeleton"
TOOL_PC_IMPACT_GRAPH = "pc_impact_graph"
TOOL_PC_LOGIC_FLOW = "pc_logic_flow"
TOOL_PC_GIT_LOG = "pc_git_log"
TOOL_PC_RUN_PIPELINE = "pc_run_pipeline"
TOOL_PC_AUTO_CONTEXT = "pc_auto_context"
TOOL_PC_READ_WITH_HASH = "pc_read_with_hash"
TOOL_PC_STRICT_REPLACE = "pc_strict_replace"
TOOL_PC_CREATE_CONTRACT = "pc_create_contract"
TOOL_PC_TODO_MANAGER = "pc_todo_manager"
TOOL_PC_SESSION_SYNC = "pc_session_sync"
TOOL_PC_MEMORY_WRITE = "pc_memory_write"
TOOL_PC_MEMORY_CONSOLIDATE = "pc_memory_consolidate"
TOOL_PC_MEMORY_READ = "pc_memory_read"
TOOL_PC_SAVE_OBSERVATION = "pc_save_observation"
TOOL_PC_MEMORY_SEARCH_KNOWLEDGE = "pc_memory_search_knowledge"

DEFAULT_INDEX_ROOT_DRY_RUN = True
DEFAULT_CAPSULE_TOKEN_BUDGET = 4000
DEFAULT_CAPSULE_AUTO_CHAIN = False
DEFAULT_SKELETON_DETAIL = "standard"
SKELETON_DETAIL_LEVELS = ("minimal", "standard", "detailed")
DEFAULT_IMPACT_DIRECTION = "both"
IMPACT_DIRECTIONS = ("callers", "callees", "both")
DEFAULT_IMPACT_MAX_DEPTH = 2
DEFAULT_IMPACT_MAX_NODES = 50
DEFAULT_LOGIC_MAX_DEPTH = 6
DEFAULT_LOGIC_MAX_NODES = 200
DEFAULT_GIT_LOG_LIMIT = 5
DEFAULT_PIPELINE_LIMIT = 5
DEFAULT_AUTO_CONTEXT_TOKEN_BUDGET = 2000
DEFAULT_MEMORY_CONSOLIDATE_DRY_RUN = True


def _pc_reindex_tool():
    return _tool(
        TOOL_PC_REINDEX,
        "⚠️ DESTRUCTIVE — 인덱스 전체 재구성. 일상 워크플로에서는 watcher 기반 증분 인덱싱이 자동 동작하므로 호출 불필요. 파서 수정·DB 오염·스키마 마이그레이션 같은 명시적 사유가 있을 때만 사용. force=true는 file_cache 전체 무효화 + 모든 파일 재파싱·재임베딩(GPU 비용) 발생.",
        {
            "force": _boolean_property(
                "⚠️ destructive. 호출 시 사유(파서 수정/DB 오염 등)를 명시할 것"
            )
        },
    )


def _pc_index_status_tool():
    return _tool(
        TOOL_PC_INDEX_STATUS,
        "인덱스 상태",
    )


def _pc_index_roots_list_tool():
    return _tool(
        TOOL_PC_INDEX_ROOTS_LIST,
        "현재 인덱싱 루트 설정 조회",
    )


def _pc_index_roots_add_tool():
    return _tool(
        TOOL_PC_INDEX_ROOTS_ADD,
        "settings.local.yaml에 인덱싱 루트 추가. 기본 dry_run=true로 스캔 수만 계산.",
        {
            "path": _string_property("워크스페이스 기준 상대 경로 또는 워크스페이스 내부 절대 경로"),
            "dry_run": _boolean_property(default=DEFAULT_INDEX_ROOT_DRY_RUN),
        },
        ["path"],
    )


def _pc_index_roots_remove_tool():
    return _tool(
        TOOL_PC_INDEX_ROOTS_REMOVE,
        "settings.local.yaml의 인덱싱 루트 제거. 기본 dry_run=true로 스캔 수만 계산.",
        {
            "path": _string_property("제거할 인덱싱 루트"),
            "dry_run": _boolean_property(default=DEFAULT_INDEX_ROOT_DRY_RUN),
        },
        ["path"],
    )


def _pc_capsule_tool():
    return _tool(
        TOOL_PC_CAPSULE,
        "1순위 검색. token_budget는 chars/4 추정 기반(정확한 토크나이저 아님). auto_chain=true 시 짧은 capsule 감지 후 impact_graph+memory 자동 체이닝 + observation 기록을 수행한다. 응답에 chars_used/tokens_estimated 포함.",
        {
            "query": _string_property(),
            "token_budget": _integer_property(
                "토큰 예산 (approximate via chars/4)",
                DEFAULT_CAPSULE_TOKEN_BUDGET,
            ),
            "auto_chain": _boolean_property(
                "짧은 capsule 시 자동 체이닝 활성화",
                DEFAULT_CAPSULE_AUTO_CHAIN,
            ),
        },
        ["query"],
    )


def _pc_skeleton_tool():
    return _tool(
        TOOL_PC_SKELETON,
        "파일 스켈레톤 출력.",
        {
            "file_path": _string_property("파일 경로"),
            "detail": _string_property(
                "상세 수준",
                enum=SKELETON_DETAIL_LEVELS,
                default=DEFAULT_SKELETON_DETAIL,
            ),
        },
        ["file_path"],
    )


def _pc_impact_graph_tool():
    return _tool(
        TOOL_PC_IMPACT_GRAPH,
        "영향 범위 추적. 응답에 truncated/limit/returned_count/total_seen 포함.",
        {
            "fqn": _string_property("함수/클래스의 FQN"),
            "direction": _string_property(
                "추적 방향",
                enum=IMPACT_DIRECTIONS,
                default=DEFAULT_IMPACT_DIRECTION,
            ),
            "max_depth": _integer_property("최대 깊이", DEFAULT_IMPACT_MAX_DEPTH),
            "max_nodes": _integer_property("최대 반환 노드 수", DEFAULT_IMPACT_MAX_NODES),
        },
        ["fqn"],
    )


def _pc_logic_flow_tool():
    return _tool(
        TOOL_PC_LOGIC_FLOW,
        "두 기능 간 실행 경로 탐색. 응답에 truncated/limit/returned_count/total_seen 포함.",
        {
            "from_fqn": _string_property("시작 지점 FQN"),
            "to_fqn": _string_property("종료 지점 FQN"),
            "max_depth": _integer_property("경로 최대 깊이", DEFAULT_LOGIC_MAX_DEPTH),
            "max_nodes": _integer_property("탐색 최대 노드 수", DEFAULT_LOGIC_MAX_NODES),
        },
        ["from_fqn", "to_fqn"],
    )


def _pc_git_log_tool():
    return _tool(
        TOOL_PC_GIT_LOG,
        "특정 파일의 상세 Git 수정 이력 조회.",
        {
            "file_path": _string_property("파일 경로"),
            "limit": _integer_property("최대 로그 수", DEFAULT_GIT_LOG_LIMIT),
        },
        ["file_path"],
    )


def _pc_run_pipeline_tool():
    return _tool(
        TOOL_PC_RUN_PIPELINE,
        "캡슐+임팩트+메모리 통합 검색 (고급 종합 탐색 진입점). 코드+그래프+메모리 종합 맥락이 필요한 경우 사용. 응답에 truncated/limit/returned_count/total_seen 포함.",
        {
            "query": _string_property("통합 검색 쿼리"),
            "limit": _integer_property("unified_context 항목 수 제한", DEFAULT_PIPELINE_LIMIT),
        },
        ["query"],
    )


def _pc_auto_context_tool():
    return _tool(
        TOOL_PC_AUTO_CONTEXT,
        "세션 시작 시 최신 결정사항과 인기 지식을 요약하여 제공 (맥락 복원).",
        {
            "token_budget": _integer_property("토큰 예산", DEFAULT_AUTO_CONTEXT_TOKEN_BUDGET),
        },
    )


def _pc_read_with_hash_tool():
    return _tool(
        TOOL_PC_READ_WITH_HASH,
        "해시 포함 읽기",
        {"file_path": _string_property()},
        ["file_path"],
    )


def _pc_strict_replace_tool():
    return _tool(
        TOOL_PC_STRICT_REPLACE,
        "정밀 편집",
        {
            "file_path": _string_property(),
            "old_content": _string_property(),
            "new_content": _string_property(),
        },
        ["file_path", "old_content", "new_content"],
    )


def _pc_create_contract_tool():
    return _tool(
        TOOL_PC_CREATE_CONTRACT,
        "계약 생성",
        {
            "lane_id": _string_property(),
            "task_name": _string_property(),
            "instructions": _string_property(),
            "files_to_modify": _array_string_property(),
        },
        ["lane_id", "task_name", "instructions"],
    )


def _pc_todo_manager_tool():
    return _tool(
        TOOL_PC_TODO_MANAGER,
        "Todo 관리",
        {
            "action": _string_property("add | check | clear"),
            "task": _string_property("add 시 등록할 태스크 내용"),
            "task_id": _string_property("check 시 완료 표시할 태스크 ID"),
        },
        ["action"],
    )


def _pc_session_sync_tool():
    return _tool(
        TOOL_PC_SESSION_SYNC,
        "작업 종료 시 Git 상태와 변경 파일을 스캔하여 세션 메모리를 자동 동기화합니다.",
        {
            "task_desc": _string_property("작업 요약"),
        },
        ["task_desc"],
    )


def _pc_memory_write_tool():
    return _tool(
        TOOL_PC_MEMORY_WRITE,
        "지식 저장 및 마크다운 승격(decisions/patterns.md)",
        {
            "key": _string_property(),
            "category": _string_property(),
            "content": _string_property(),
            "tags": _array_string_property(),
            "relationships": _object_property(),
        },
        ["key", "category", "content"],
    )


def _pc_memory_consolidate_tool():
    return _tool(
        TOOL_PC_MEMORY_CONSOLIDATE,
        "파편화된 과거 지식을 하나의 새로운 규칙으로 병합. dry_run=true 기본 — 후보만 반환(would_delete/would_write/executed=false). 실행하려면 dry_run=false 명시. 자동 트리거 금지.",
        {
            "new_key": _string_property(),
            "category": _string_property(),
            "content": _string_property(),
            "old_keys": _array_string_property(),
            "tags": _array_string_property(),
            "relationships": _object_property(),
            "dry_run": _boolean_property(
                "기본 true. false 명시 시에만 실제 삭제·병합 수행",
                DEFAULT_MEMORY_CONSOLIDATE_DRY_RUN,
            ),
        },
        ["new_key", "category", "content", "old_keys"],
    )


def _pc_memory_read_tool():
    return _tool(
        TOOL_PC_MEMORY_READ,
        "지식 조회",
        {"key": _string_property()},
        ["key"],
    )


def _pc_save_observation_tool():
    return _tool(
        TOOL_PC_SAVE_OBSERVATION,
        "인사이트 저장",
        {"content": _string_property()},
        ["content"],
    )


def _pc_memory_search_knowledge_tool():
    return _tool(
        TOOL_PC_MEMORY_SEARCH_KNOWLEDGE,
        "영구 지식, 규칙 및 스킬 하이브리드 검색",
        {
            "query": _string_property(),
            "category": _string_property(),
        },
        ["query"],
    )


TOOLS = [
    _pc_reindex_tool(),
    _pc_index_status_tool(),
    _pc_index_roots_list_tool(),
    _pc_index_roots_add_tool(),
    _pc_index_roots_remove_tool(),
    _pc_capsule_tool(),
    _pc_skeleton_tool(),
    _pc_impact_graph_tool(),
    _pc_logic_flow_tool(),
    _pc_git_log_tool(),
    _pc_run_pipeline_tool(),
    _pc_auto_context_tool(),
    _pc_read_with_hash_tool(),
    _pc_strict_replace_tool(),
    _pc_create_contract_tool(),
    _pc_todo_manager_tool(),
    _pc_session_sync_tool(),
    _pc_memory_write_tool(),
    _pc_memory_consolidate_tool(),
    _pc_memory_read_tool(),
    _pc_save_observation_tool(),
    _pc_memory_search_knowledge_tool(),
]


def list_tools():
    return TOOLS
