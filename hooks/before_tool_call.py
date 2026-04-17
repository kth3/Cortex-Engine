#!/usr/bin/env python3
"""
before_tool_call.py - 예방적 파라미터 가드 훅
위험한 도구 호출 전 인자들의 유효성을 검사합니다.
"""
import sys
import json
import os

def validate(tool_name, args_json):
    try:
        args = json.loads(args_json)
        
        # [Keyword Detector] 특정 키워드 감지 시 룰 자동 추천
        keywords = {
            "deslop": "rule::ai-slop-cleaner",
            "refactor": "rule::ai-slop-cleaner",
            "cleanup": "rule::ai-slop-cleaner",
            "deep dive": "protocol::deep-dive",
            "trace": "protocol::deep-dive",
            "why": "protocol::deep-dive",
            "계획": "protocol::progress-tracking",
            "진행": "protocol::progress-tracking",
            "추적": "protocol::progress-tracking",
            "plan": "protocol::progress-tracking",
            "track": "protocol::progress-tracking"
        }
        
        suggested_rules = []
        full_text = args_json.lower()
        for kw, rule_key in keywords.items():
            if kw in full_text:
                suggested_rules.append(rule_key)
        
        if suggested_rules:
            # 에러는 아니지만 경고/안내 메시지로 출력하여 에이전트에게 인지시킴
            print(f"Info: Keywords detected. You should check these rules: {', '.join(suggested_rules)}")

        if tool_name == "pc_strict_replace":
            file_path = args.get("file_path")
            old_content = args.get("old_content")
            new_content = args.get("new_content")
            
            if not file_path or not old_content or not new_content:
                print("Error: Missing required parameters for pc_strict_replace.")
                return
            
            if len(old_content.strip()) < 5:
                print("Error: old_content is too short. Risk of ambiguous replacement.")
                return
            
            # 파일 존재 여부 등 추가 검증 가능
            
        elif tool_name == "pc_create_contract":
            if not args.get("lane_id") or not args.get("task_name"):
                print("Error: Missing lane_id or task_name for contract.")
                return

        # 모든 검증 통과 시 아무것도 출력하지 않거나 성공 메시지 출력
        
    except Exception as e:
        print(f"Error: Internal validation error - {str(e)}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(0)
    
    validate(sys.argv[1], sys.argv[2])
