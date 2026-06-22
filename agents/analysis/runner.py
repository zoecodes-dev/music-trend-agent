"""LLM 멀티턴 루프 오케스트레이션 — 데이터 로드 → 프롬프트 구성 → 리포트 생성."""
import time
from datetime import datetime

import anthropic
from dotenv import load_dotenv

from .loaders import (
    load_latest_snapshot, load_discovery_results, load_artist_cache,
    load_watchlist, load_history, load_latest_report,
    get_artists_on_chart, update_cache_last_seen,
)
from .summaries import (
    prepare_snapshot_summary, prepare_discovery_summary,
    prepare_history_summary, prepare_watchlist_today_changes,
)
from .prompts import TOOLS, SYSTEM_PROMPT
from .tools import execute_tool

load_dotenv()

client = anthropic.Anthropic()


def create_message_with_retry(client, max_retries=2, wait_seconds=65, **kwargs):
    """rate limit 시 대기 후 재시도."""
    import anthropic as _anthropic
    for attempt in range(max_retries + 1):
        try:
            return client.messages.create(**kwargs)
        except _anthropic.RateLimitError as e:
            if attempt < max_retries:
                print(f"  ⏳ rate limit — {wait_seconds}초 대기 후 재시도 ({attempt+1}/{max_retries})")
                time.sleep(wait_seconds)
            else:
                raise


def run_analysis():
    print("📊 Analysis Agent 시작\n")

    snapshots = load_latest_snapshot(2)
    if not snapshots:
        print("  ❌ 스냅샷 없음")
        return

    today_snapshot = snapshots[0]
    discovery_results = load_discovery_results()
    cache = load_artist_cache()
    watchlist = load_watchlist()
    history = load_history()
    yesterday_report = load_latest_report()

    artists_on_chart = get_artists_on_chart(today_snapshot)
    update_cache_last_seen(cache, artists_on_chart)

    snapshot_text = prepare_snapshot_summary(snapshots, cache)
    discovery_text = prepare_discovery_summary(discovery_results)
    history_text = prepare_history_summary(history)
    watchlist_changes_text = prepare_watchlist_today_changes(watchlist, today_snapshot)

    # 워치리스트: last_signal_at 내림차순 정렬 → 신선도 라벨링
    today = datetime.now().date()

    def _freshness_label(last_signal_iso: str) -> str:
        if not last_signal_iso:
            return "🕸️ stale"
        try:
            d = datetime.fromisoformat(last_signal_iso).date()
        except Exception:
            return "🕸️ stale"
        delta = (today - d).days
        if delta <= 0:
            return "🆕 오늘 신호"
        if delta <= 6:
            return f"🔁 {delta}일 전 신호"
        return f"🕸️ {delta}일째 stale"

    def _discovery_label(n: int) -> str:
        # 발굴 우위 축 — confidence(검증 강도)와 직교. 코드가 결정, LLM 판단 배제.
        # ⭐ 선행 = 신선한 발굴(1~2회) / ➿ 누적 = 신선도 소진(3회+, ##1톱·##6메인 금지 대상)
        return "⭐ 선행" if n <= 2 else f"➿ 누적 {n}회"

    def _format_watch_artist(a: dict) -> str:
        hist = a.get("history", [])
        n = len(hist)
        first_sig = hist[0]["signal"][:60] if hist else ""
        last_sig = hist[-1]["signal"][:80] if hist else ""
        fresh = _freshness_label(a.get("last_signal_at", ""))
        disc = _discovery_label(n)
        # 1회 신호면 첫=마지막 → 마지막만 출력
        if n <= 1:
            return f"  [{a['confidence']}] {disc} | {a['value']} | {fresh} | {last_sig}"
        return (
            f"  [{a['confidence']}] {disc} | {a['value']} (총 {n}회 신호) | {fresh}\n"
            f"      └ 첫 신호: {first_sig}\n"
            f"      └ 마지막 신호: {last_sig}"
        )

    artists = [a for a in watchlist.get("artists", []) if isinstance(a, dict)]
    artists.sort(key=lambda a: a.get("last_signal_at", ""), reverse=True)

    watchlist_context = "\n## 현재 워치리스트 (최근 신호순 상위 12명):\n"
    watchlist_context += (
        "_라벨 2축 (직교): [confidence]=검증 강도(🔴확실/🟡주시/🟢초기) / [discovery]=발굴 우위(⭐선행=신선한 발굴 / ➿누적=신선도 소진).\n"
        "  두 축은 독립이다. 🔴➿(검증됐지만 소진) ≠ 🟢⭐(초기지만 우리가 먼저)를 혼동 금지.\n"
        "  ➿누적 항목은 confidence가 🔴여도 '신규/첫 발견' 톤 금지 + ##1 톱·##6 인사이트 메인 소재 금지(##3 신예 레이더·##5·##8 또는 ##6 대비 레퍼런스로는 가능)._\n"
    )
    for a in artists[:12]:
        watchlist_context += _format_watch_artist(a) + "\n"

    genres = [g for g in watchlist.get("genres", []) if isinstance(g, dict)]
    genres.sort(key=lambda g: g.get("last_signal_at", ""), reverse=True)
    if genres:
        watchlist_context += "\n## 현재 워치리스트 (장르 상위 5개):\n"
        for g in genres[:5]:
            watchlist_context += f"  [{g['confidence']}] 장르: {g['value']} | {_freshness_label(g.get('last_signal_at', ''))}\n"
           
    yesterday_context = ""
    if yesterday_report:
        yesterday_context = f"\n## 어제 리포트 (참고):\n{yesterday_report[:1200]}"

    user_message = f"""Discovery Agent 결과를 종합해 A&R 뉴스레터를 작성하세요.
Discovery 신호를 핵심으로. 캐시 있는 아티스트는 캐시만. search_web 금지.

{snapshot_text}
{discovery_text}
{history_text}
{watchlist_changes_text}
{watchlist_context}
{yesterday_context}"""

    messages = [{"role": "user", "content": user_message}]
    max_iter = 8
    iteration = 0
    report_generated = False

    while iteration < max_iter and not report_generated:
        iteration += 1
        print(f"  [Analysis 턴 {iteration}]")

        # 마지막 턴에서는 tool_choice로 generate_report 강제
        turns_left = max_iter - iteration
        extra_kwargs = {}
        if turns_left <= 2 and not report_generated:
            extra_kwargs["tool_choice"] = {"type": "tool", "name": "generate_report"}
            print(f"  🎯 남은 턴 {turns_left}회 — generate_report 강제 호출")

        response = create_message_with_retry(
            client,
            model="claude-opus-4-8",
            max_tokens=8000,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"}
                }
            ],
            tools=TOOLS,
            messages=messages,
            **extra_kwargs,
        )
        
        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if hasattr(block, "text") and block.text:
                print(f"  💭 {block.text[:150]}...")

        if response.stop_reason == "end_turn":
            print(f"  ⚠️ Claude가 tool 호출 없이 end_turn — 리포트 미생성 가능")
            break
        if response.stop_reason == "max_tokens":
            print(f"  ⚠️ max_tokens({response.usage.output_tokens}) 도달 — 출력 잘림")
            break
        if response.stop_reason != "tool_use":
            print(f"  ⚠️ 예상치 못한 stop_reason: {response.stop_reason}")
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = execute_tool(block.name, block.input, cache)
                print(f"  🔧 {block.name}: {result[:80]}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
                if block.name == "generate_report":
                    report_generated = True

        messages.append({"role": "user", "content": tool_results})
        time.sleep(0.3)

    print(f"\n  ✅ Analysis 완료 | {iteration}턴 | 캐시: {len(cache)}명")
