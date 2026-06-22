"""
tool_handlers.py — 3개 discovery agent 공통 도구 핸들러

agents/cross_platform · community · producer가 동일하게 사용하는
3개 도구 (add_to_watchlist, flag_signal, save_result)를 단일 진입점으로 통합.

각 agent는 source 이름과 결과 저장 경로만 다름.
"""

import json
from datetime import datetime
from pathlib import Path

from utils.watchlist import add_to_watchlist_cumulative


def handle_tool_call(
    tool_name: str,
    tool_input: dict,
    watchlist: dict,
    signals: list,
    source: str,
    result_path: Path,
    save_watchlist_fn,
) -> str:
    """
    3개 discovery agent 공통 도구 핸들러.
    
    Parameters:
        tool_name: 도구 이름 (add_to_watchlist | flag_signal | save_result)
        tool_input: 도구 입력 dict
        watchlist: 현재 워치리스트 (in-memory dict)
        signals: 현재 신호 리스트 (in-memory list, mutated)
        source: agent 식별자 ("cross_platform" | "community" | "producer")
        result_path: save_result 시 저장할 경로
        save_watchlist_fn: 워치리스트 저장 함수 (agent마다 동일하지만 import 의존성으로 인자 전달)
    
    Returns: 사람이 읽을 수 있는 결과 메시지
    """
    if tool_name == "add_to_watchlist":
        result = add_to_watchlist_cumulative(watchlist, tool_input)
        save_watchlist_fn(watchlist)
        return result

    if tool_name == "flag_signal":
        today_str = datetime.now().strftime("%Y-%m-%d")
        if any(
            s["artist_or_genre"] == tool_input["artist_or_genre"]
            and s.get("flagged_at", "")[:10] == today_str
            for s in signals
        ):
            return f"이미 플래그됨: {tool_input['artist_or_genre']}"
        signal = {
            **tool_input,
            "source": source,
            "flagged_at": datetime.now().isoformat(),
        }
        signals.append(signal)
        return (
            f"🚩 [{tool_input['confidence']}] "
            f"{tool_input['artist_or_genre']} | {tool_input['media_status']}"
        )

    if tool_name == "save_result":
        result = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "agent": source,
            "summary": tool_input["summary"],
            "signals": signals,
            "saved_at": datetime.now().isoformat(),
        }
        result_path.parent.mkdir(exist_ok=True)
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return f"✅ 저장: {len(signals)}개 신호"

    return f"알 수 없는 tool: {tool_name}"
