    # Agent Optimization & Token Economy Protocol

    1. **Batch Commits (파편화 방지)**:
       - 동일한 파일이나 연관된 기능(예: 테스트 코드 추가)을 작업할 때는 여러 번의 도구 호출이나 커밋으로
      쪼개지 마십시오.
       - 모든 수정 사항을 메모리(컨텍스트) 내에서 종합한 뒤, 단일 Batch 작업으로 묶어 한 번의 통합된 Git
      Commit을 생성하여 토큰과 히스토리 낭비를 최소화하십시오.

    2. **No File-based Memory (`.jules/bolt.md` 생성 금지)**:
       - 학습한 내용, 성능 벤치마크 결과, 또는 단기 작업 요약을 저장하기 위해 `.jules/bolt.md` 등 별도의
      평문(Markdown) 파일을 생성하거나 커밋하지 마십시오.
       - 프로젝트의 상태 기록이나 결정 사항은 오직 제공된 MCP 도구(`pc_save_observation` 또는
      `pc_memory_write`)를 통해서만 시스템 DB에 영구 저장(은닉)해야 합니다.

   3. **Diff Truncation Safety**:
      - 코드 리뷰나 분석 요청 시, Diff나 파일 내용을 단순히 글자 수 단위(예: `[:15000]`)로 자르지
      마십시오. 구문이 중간에 잘려 할루시네이션(환각)을 유발할 수 있습니다. 반드시 라인(Line) 단위로
      안전하게 자르거나 중요 컨텍스트를 우선 포함하십시오.
   4. Co-authored-by: google-labs-jules[bot] 등의 기고자 이메일을 커밋 메시지에 작성하지 마시오.
   5. 이모지를 작성하지 마시오.
