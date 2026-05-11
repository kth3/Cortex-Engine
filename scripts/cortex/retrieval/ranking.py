def _heuristic_boost(item_key: str, item_category: str, query: str) -> float:
    """휴리스틱 가중치 계산
    
    [수정] 외부 레퍼런스(resource, example)에 페널티를 부여하여
    프로젝트 규칙/프로토콜/스킬이 검색 결과 상단에 오도록 보장.
    """
    boost = 0.0
    q_low = query.lower()
    k_low = item_key.lower()
    if k_low == q_low:
        boost += 0.5
    elif q_low in k_low:
        boost += 0.1
    if item_category in ["rule", "skill", "decision", "protocol"]:
        boost += 0.05
    # 외부 레퍼런스 페널티: RRF Hub 편향 현상 방지
    if item_category in ["resource", "example"]:
        boost -= 0.3
    return boost
