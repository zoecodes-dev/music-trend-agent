"""
watchlist_utils.py — 워치리스트 공통 유틸

목적: agents/cross_platform · producer · community에 중복 구현된
add_to_watchlist 핸들러를 단일 진입점으로 통합.

스키마 변경:
  Before: {value, reason: str, confidence, added_at}
  After:  {value, confidence, added_at, last_signal_at, history: [{date, signal}]}

핵심 동작:
  - 신규: history 리스트에 첫 신호 추가
  - 기존: history에 누적 (덮어쓰기 X). 같은 날 중복은 방지.
  - confidence는 강한 쪽으로만 업데이트 (🟢 → 🟡 → 🔴 한방향)

호환성:
  - 이전 스키마(reason 필드만 있는 항목) → 첫 누적 시 자동 마이그레이션
  - get_latest_signal()로 새/구 스키마 모두에서 최신 사유 추출
"""

from datetime import datetime

from utils.normalize import canonicalize


CONFIDENCE_RANK = {"🟢": 1, "🟡": 2, "🔴": 3}


def add_to_watchlist_cumulative(watchlist: dict, tool_input: dict) -> str:
    """
    add_to_watchlist 도구 호출을 처리. 기존 항목이면 history에 누적, 없으면 신규 등록.
    
    tool_input 형식: {"type": "artist"|"genre"|"song"|"search_query",
                      "value": str, "reason": str, "confidence": "🟢"|"🟡"|"🔴"}
    
    Returns: 사람이 읽을 수 있는 결과 메시지.
    
    Note: save_watchlist는 호출자 측에서 처리 (테스트 가능성 + 트랜잭션 제어).
    """
    key = f"{tool_input['type']}s"
    today = datetime.now().strftime("%Y-%m-%d")
    now_iso = datetime.now().isoformat()

    # 유입 시점 정규화 — 매칭/저장/반환이 전부 대표명을 쓰도록 한 번만 덮어씀.
    # 'artist' 타입만 정규화 (genre/song/search_query는 대상 아님).
    if tool_input.get("type") == "artist":
        tool_input["value"] = canonicalize(tool_input["value"])

    # 기존 항목 검색
    existing = next(
        (x for x in watchlist.get(key, [])
         if isinstance(x, dict) and x["value"] == tool_input["value"]),
        None
    )

    if existing:
        # 이전 스키마 lazy 마이그레이션 (reason → history)
        if "history" not in existing:
            existing["history"] = [{
                "date": existing.get("added_at", now_iso)[:10],
                "signal": existing.get("reason", ""),
            }]
            existing.pop("reason", None)  # 정리

        # 같은 날 중복 신호는 추가 안 함
        if not any(h["date"] == today for h in existing["history"]):
            existing["history"].append({
                "date": today,
                "signal": tool_input["reason"],
            })

        existing["last_signal_at"] = now_iso

        # confidence 상향만 (강해지는 방향만)
        new_rank = CONFIDENCE_RANK.get(tool_input["confidence"], 0)
        old_rank = CONFIDENCE_RANK.get(existing.get("confidence", "🟢"), 0)
        if new_rank > old_rank:
            existing["confidence"] = tool_input["confidence"]

        return (
            f"✅ 누적: [{existing['confidence']}] {tool_input['value']} "
            f"(총 {len(existing['history'])}회 신호, 첫 추적 {existing['history'][0]['date']})"
        )

    # 신규 등록
    if key not in watchlist:
        watchlist[key] = []
    watchlist[key].append({
        "value": tool_input["value"],
        "confidence": tool_input["confidence"],
        "added_at": now_iso,
        "last_signal_at": now_iso,
        "history": [{"date": today, "signal": tool_input["reason"]}],
    })
    return f"✅ 신규: [{tool_input['confidence']}] {tool_input['value']}"


def get_latest_signal(item: dict) -> str:
    """
    워치리스트 항목에서 최신 사유 텍스트 추출. 새/구 스키마 모두 지원.
    
    - 새 스키마: history 리스트의 마지막 항목 signal
    - 구 스키마: reason 필드 그대로
    """
    if not isinstance(item, dict):
        return ""
    if "history" in item and item["history"]:
        return item["history"][-1].get("signal", "")
    return item.get("reason", "")


def get_first_seen(item: dict) -> str:
    """최초 추적 시작일 (YYYY-MM-DD). 호환성 보장."""
    if not isinstance(item, dict):
        return ""
    if "history" in item and item["history"]:
        return item["history"][0].get("date", "")
    return item.get("added_at", "")[:10]


def get_signal_count(item: dict) -> int:
    """누적 신호 수. 구 스키마는 1로 간주."""
    if not isinstance(item, dict):
        return 0
    if "history" in item:
        return len(item["history"])
    return 1 if item.get("reason") else 0


def migrate_watchlist_inplace(watchlist: dict) -> int:
    """
    구 스키마 → 새 스키마 일괄 변환.
    
    Returns: 변환된 항목 수.
    """
    migrated = 0
    for key in ["artists", "genres", "songs", "search_queries"]:
        for item in watchlist.get(key, []):
            if not isinstance(item, dict):
                continue
            if "history" in item:
                continue  # 이미 새 스키마
            
            added_at = item.get("added_at", "")
            item["history"] = [{
                "date": added_at[:10] if added_at else "",
                "signal": item.get("reason", ""),
            }]
            item["last_signal_at"] = added_at if added_at else datetime.now().isoformat()
            item.pop("reason", None)
            migrated += 1
    return migrated