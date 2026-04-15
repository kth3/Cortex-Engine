# Gemini CLI Mandate: MCP-First Infrastructure

이 지침은 모든 작업에 절대적으로 우선하며, 자의적인 탐색을 금지한다.
답변은 한국어를 기반으로 한다.

## 1. MCP Engine-First
- agents 내부 가상환경 활성화 여부를 확인한다.
- 모든 분석, 지침 조회, 코드 관계 파악은 반드시 **Cortex MCP 엔진**(`pc_` 계열 도구)을 최우선으로 호출하여 수행한다.
- 기본 도구(`ls`, `grep`, `read_file`)를 통한 독자적인 탐색과 판단을 최소화하고, 엔진이 제공하는 컨텍스트를 신뢰하라.

## 2. Token & Logic Economy
- 상세 규칙은 직접 파일을 열지 말고 `pc_memory_search_knowledge(query, category='rule')`로 검색하여 필요한 부분만 인지 영역에 올린다.
- 분석 시 `pc_capsule` 또는 `pc_skeleton`을 우선 활용하여 불필요한 토큰 낭비를 원천 차단한다.


## 3. Context-Aware Search (Intelligent Querying)
- 사용자의 추상적인 지시를 그대로 검색어로 사용하지 마라.
- 검색 전에 반드시 IDE 메타데이터 또는 코드 탐색을 통해 현재 작업 중인 파일의 이름, 기술 스택, 핵심 키워드를 파악하여 검색어에 포함시켜라.
- 도구 호출 시 제공되는 `context` 파라미터에 현재 다루고 있는 파일명이나 도메인 키워드를 전달하여 검색 품질을 자동으로 보강해라.

## 4. Strict Reporting Rule (Intelligent Honesty)
- **보고서 우선 참조**: 커밋 메시지, MR 요약, Jira 이슈 등 모든 기록 작성 시에는 반드시 `.agents/protocols/reporting.md`를 1순위로 참조하여 양식을 준수한다.
- **보고 의무**: 작업 보고는 모든 분석 및 답변이 끝난 **최종 응답의 최하단에 딱 한 번**만 기재한다. (중간 과정 생략)
- **Skill 표기 원칙**: `pc_` 도구를 통한 검색 결과 중 **`skills/` 디렉터리에 물리적으로 존재하는 파일에 기반한 스킬 ID**만을 쉼표로 구분하여 명시한다. DB에만 존재하는 지식 키(예: `scl_...`)는 제외한다. 식별자 외의 부연 설명(예: "(참조됨)")은 절대 붙이지 않는다. (예: `Skill: frontend-security-coder, clarity-gate`)
- **MCP 표기 원칙**: 성공적으로 호출된 MCP 서버 명칭만 명시한다. (예: `MCP: cortex-mcp`)

## 6. Multi-Agent Relay & Coordination (Stability First)
- **3인 협업 체계**: Antigravity, Gemini, Claude 3인이 동일한 코드베이스에서 작동함을 인지하라.
- **Pre-flight Check**: 작업을 시작하기 전 반드시 `.agents/scripts/relay.py status`로 이전 에이전트의 Handoff Message를 확인하고 `acquire` 명령어로 권한을 획득하라.
- **Atomic Handoff**: 작업 완료 시 `release` 명령어로 권한을 반납하고, 다음 에이전트에게 필요한 구체적인 맥락(Handoff Message)을 남겨라.
- **Collision Prevention**: 다른 에이전트가 `BUSY` 상태일 경우 즉시 대기하거나 사용자에게 알리고, Race Condition을 방지하기 위해 파일 쓰기 권한을 엄격히 준수하라.
- **Shared Whiteboard (Observation)**: 코드 수정이나 중요한 구조적 결정을 내린 직후에는 반드시 `pc_save_observation(content="...", file_paths=["..."])`를 호출하여 실시간 메모를 남겨라. 이를 통해 다음 에이전트가 과거 템플릿으로 파일을 덮어쓰거나 컨텍스트를 오판(환각)하는 대참사를 원천 차단한다.

## 5. Environment-First Troubleshooting & MD Backup
- **환경 오염 점검 우선**: 코드 수정 후 동일한 오류가 반복되거나 변경 사항이 반영되지 않을 경우, 내부 로직을 다시 의심하기 전에 **반드시 백그라운드 프로세스의 옛날 코드 캐싱, 가상환경 충돌 등 시스템의 물리적 환경 오염 여부부터 최우선으로 점검**한다.
- **필수 지식의 MD 보완 원칙**: DB 기반 인덱싱 외에도, '환경 점검 우선'과 같이 시스템 보호를 위해 즉각적으로 참조해야 하는 핵심 룰(Rule)은 DB 로딩 지연 등으로 인해 즉시 불러오지 못할 위험이 존재한다. 따라서, 치명적인 규칙이나 지침은 반드시 물리적인 Markdown(.md) 파일로도 병행하여 작성·보완하여 언제든 직접 열람할 수 있도록 유지한다.
