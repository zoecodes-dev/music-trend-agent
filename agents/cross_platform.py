"""
Music Trend Agent — Cross Platform Agent
역할: 오늘 처음 여러 플랫폼에서 겹치기 시작한 아티스트 탐색.
메이저 매체 미보도 + velocity NEW 우선.
결과: discovery_results/cross_platform.json
"""

import json
import os
import time
from collections import defaultdict
from datetime import datetime
import sys
from pathlib import Path

# 패키지 하위에서 직접 실행해도 루트 기준 import·데이터 경로가 동작하도록
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import anthropic
from dotenv import load_dotenv
from tavily import TavilyClient
import anthropic as _anthropic
from utils.tool_handlers import handle_tool_call

def create_message_with_retry(client, max_retries=2, wait_seconds=65, **kwargs):
    """rate limit 시 대기 후 재시도."""
    for attempt in range(max_retries + 1):
        try:
            return client.messages.create(**kwargs)
        except _anthropic.RateLimitError as e:
            if attempt < max_retries:
                print(f"  ⏳ rate limit — {wait_seconds}초 대기 후 재시도 ({attempt+1}/{max_retries})")
                time.sleep(wait_seconds)
            else:
                raise

load_dotenv()

DATA_DIR = ROOT / "snapshots"
WATCHLIST_PATH = ROOT / "data" / "watchlist.json"
RESULT_PATH = ROOT / "discovery_results" / "cross_platform.json"

client = anthropic.Anthropic()
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))


# ──────────────────────────────────────────────
# 데이터 로드
# ──────────────────────────────────────────────

def load_latest_snapshot() -> dict:
    files = sorted(DATA_DIR.glob("snapshot_*.json"), reverse=True)
    if not files:
        return {}
    with open(files[0], "r", encoding="utf-8") as f:
        return json.load(f)


def load_watchlist() -> dict:
    if WATCHLIST_PATH.exists():
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"artists": [], "genres": [], "signals": [], "songs": []}


def save_watchlist(watchlist: dict):
    watchlist["updated_at"] = datetime.now().isoformat()
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)  # fresh clone: data/ 없을 수 있음
    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(watchlist, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
# 교차 소스 집계
# ──────────────────────────────────────────────

def compute_cross_signals(snapshot: dict) -> list:
    """
    아티스트별 교차 소스 집계.
    2개 이상 소스 + NEW 진입 우선 정렬.
    """
    artist_sources = defaultdict(list)
    artist_new = defaultdict(bool)

    source_map = {
        "apple_kr": "Apple KR", "apple_us": "Apple US",
        "apple_gb": "Apple GB", "apple_jp": "Apple JP",
        "lastfm_global": "Last.fm Global",
        "lastfm_us": "Last.fm US", "lastfm_uk": "Last.fm UK",
        "kworb_apple_ww": "Kworb WW", "youtube_kr": "YouTube KR",
    }

    for source_key, label in source_map.items():
        for t in snapshot.get("sources", {}).get(source_key, []):
            artist = t.get("artist", t.get("channel", "")).strip()
            if not artist:
                continue
            vel = t.get("velocity", "")
            vel_str = " [NEW]" if vel == "NEW" else (f" [{vel:+d}]" if isinstance(vel, int) and vel != 0 else "")
            artist_sources[artist].append(f"{label}#{t.get('rank','?')}{vel_str}")
            if vel == "NEW":
                artist_new[artist] = True

    for v in snapshot.get("sources", {}).get("youtube_rising", []):
        channel = v.get("channel", "").strip()
        if not channel:
            continue
        vpd = v.get("views_per_day", 0)
        delta = v.get("vpd_delta")
        is_new = delta is None
        artist_sources[channel].append(f"YouTube Rising {vpd:,}/day{' [신규]' if is_new else f' [Δ{delta:+,}]'}")
        if is_new:
            artist_new[channel] = True

    cross = [
        {
            "artist": a,
            "sources": srcs,
            "source_count": len(srcs),
            "has_new": artist_new[a],
        }
        for a, srcs in artist_sources.items() if len(srcs) >= 2
    ]
    cross.sort(key=lambda x: (x["has_new"], x["source_count"]), reverse=True)
    return cross


# ──────────────────────────────────────────────
# 워치리스트 변화 체크
# ──────────────────────────────────────────────

def check_watchlist_changes(snapshot: dict, watchlist: dict) -> str:
    if not watchlist.get("artists"):
        return ""

    today_date = datetime.strptime(snapshot["date"], "%Y-%m-%d")
    lines = ["\n## 워치리스트 변화 체크:"]

    for artist_obj in [a for a in watchlist.get("artists", []) if isinstance(a, dict)]:
        artist = artist_obj["value"]
        artist_lower = artist.lower()
        added_at = datetime.strptime(artist_obj["added_at"][:10], "%Y-%m-%d")
        days_tracked = (today_date - added_at).days
        changes = []

        for country in ["kr", "us", "jp", "gb"]:
            for t in snapshot.get("sources", {}).get(f"apple_{country}", []):
                if artist_lower in t.get("artist", "").lower():
                    vel = t.get("velocity")
                    if vel == "NEW":
                        changes.append(f"Apple {country.upper()} 신규 #{t['rank']}")
                    elif isinstance(vel, int) and vel != 0:
                        changes.append(f"Apple {country.upper()} {vel:+d} #{t['rank']}")

        for v in snapshot.get("sources", {}).get("youtube_rising", []):
            if artist_lower in v.get("channel", "").lower():
                delta = v.get("vpd_delta")
                if delta is None:
                    changes.append(f"YouTube Rising 신규 {v['views_per_day']:,}/day")
                elif isinstance(delta, int) and delta > v["views_per_day"]:
                    changes.append(f"YouTube Rising 2배 급등")

        if changes:
                lines.append(f"  🔔 {artist}: {', '.join(changes)} → search_web 권장")
        else:
                lines.append(f"  ✅ {artist}: 변동 없음 ({days_tracked}일차) → 추적 지속")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# Tools
# ──────────────────────────────────────────────

TOOLS = [
    {
        "name": "search_web",
        "description": "신호 검증용 웹 검색. 오늘 NEW 진입 아티스트 중 맥락이 필요한 것만. 한 아티스트 1회.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 3},
            },
            "required": ["query"],
        },
    },
    {
        "name": "add_to_watchlist",
        "description": "내일도 추적할 가치 있는 아티스트/장르 추가.",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["artist", "genre", "song"]},
                "value": {"type": "string"},
                "reason": {"type": "string"},
                "confidence": {"type": "string", "enum": ["🔴", "🟡", "🟢"]},
            },
            "required": ["type", "value", "reason", "confidence"],
        },
    },
    {
        "name": "flag_signal",
        "description": "교차 신호 플래그. Analysis Agent에 전달.",
        "input_schema": {
            "type": "object",
            "properties": {
                "artist_or_genre": {"type": "string"},
                "evidence": {"type": "string"},
                "confidence": {"type": "string", "enum": ["🔴", "🟡", "🟢"]},
                "media_status": {
                    "type": "string",
                    "enum": ["우리가 먼저", "매체 동시", "매체가 먼저", "확인 불가"],
                    "description": (
                        "판단 기준: "
                        "'우리가 먼저' = 메이저 매체(Billboard/Rolling Stone/Pitchfork) 최초 보도 날짜를 확인했고 "
                        "워치리스트 추가일이 그보다 빠른 경우에만. "
                        "'매체가 먼저' = 매체 보도일이 워치리스트 추가일보다 빠름. "
                        "'매체 동시' = 7일 이내 차이. "
                        "'확인 불가' = 매체 최초 보도일 미확인. 확신 없으면 이것을 기본값으로."
                    ),
                },
            },
            "required": ["artist_or_genre", "evidence", "confidence", "media_status"],
        },
    },
    {
        "name": "save_result",
        "description": "탐색 완료 후 결과 저장. 마지막에 1회만 호출.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
            },
            "required": ["summary"],
        },
    },
]


def execute_tool(tool_name, tool_input, watchlist, signals):
    if tool_name == "search_web":
        try:
            result = tavily.search(
                query=tool_input["query"],
                search_depth="basic",
                max_results=tool_input.get("max_results", 3),
                include_answer=True,
            )
            return f"{result.get('answer', '')}\n" + "\n".join(
                r.get("content", "")[:150] for r in result.get("results", [])
            )
        except Exception as e:
            return f"검색 실패: {e}"

    elif tool_name in ("add_to_watchlist", "flag_signal", "save_result"):
        return handle_tool_call(
            tool_name, tool_input, watchlist, signals,
            source="cross_platform",
            result_path=RESULT_PATH,
            save_watchlist_fn=save_watchlist,
        )


# ──────────────────────────────────────────────
# 시스템 프롬프트
# ──────────────────────────────────────────────

SYSTEM_PROMPT = """당신은 Cross-Platform Signal Agent입니다.
오늘 처음 여러 플랫폼에서 겹치기 시작한 아티스트를 찾아냅니다.

핵심 기준:
- velocity == NEW인 것 우선 — 오늘 처음 등장한 신호
- 소스가 많을수록 강한 신호
- 메이저 매체 미보도 아티스트 우선
- 이미 차트에 안정적으로 있던 아티스트는 관심 없음

media_status 원칙:
- 기본값은 "확인 불가"입니다. 확신이 없으면 절대 "우리가 먼저"로 찍지 않습니다.
- "우리가 먼저"는 History Agent의 검증 결과가 있을 때만 사용합니다.
- 메이저 아티스트(차트 10위권 상주)는 대부분 "매체가 먼저"입니다.

search_web 원칙:
- 워치리스트 "변동 없음" 항목 검색 금지
- 메이저 아티스트(BTS, Justin Bieber 등) 검색 금지
- NEW 진입 + 교차 소스 2개 이상인 것만 검색
- 한 아티스트 1회

작업 순서
1. 워치리스트 변화 체크 결과 확인
2. 교차 신호에서 NEW 진입 아티스트 탐색
3. 필요한 것만 search_web 검증
4. flag_signal + add_to_watchlist
5. save_result 호출 후 종료 (필수 — 이걸 안 하면 다음 단계가 결과를 못 받습니다)

주의: flag_signal/add_to_watchlist 호출 후에도 반드시 save_result를 호출해야 합니다.
"신호 부족" 같은 결론도 save_result에 담아 호출하세요. 이걸 안 하면 Analysis Agent가
오늘의 신호를 받지 못합니다.

확신도: 🔴 확실 / 🟡 주시 / 🟢 초기신호
한국어 작성, 고유명사/장르명 영어 유지."""


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

def run():
    print("📡 Cross-Platform Agent 시작")

    snapshot = load_latest_snapshot()
    if not snapshot:
        print("  ❌ 스냅샷 없음")
        return

    watchlist = load_watchlist()
    signals = []

    cross_signals = compute_cross_signals(snapshot)
    watchlist_changes = check_watchlist_changes(snapshot, watchlist)

    # NEW 진입이 하나라도 있는 아티스트만 — Cross-Platform의 핵심 관심사
    new_signals = [s for s in cross_signals if s["has_new"]]
    steady_signals = [s for s in cross_signals if not s["has_new"] and s["source_count"] >= 3]

    cross_text = f"## 교차 신호 집계 (NEW 진입 {len(new_signals)}명 + 3개 이상 소스 안정 {len(steady_signals)}명):\n"
    cross_text += "\n### ⭐ NEW 진입 (오늘 처음 등장) — 우선 분석:\n"
    for s in new_signals[:15]:
        cross_text += f"  {s['artist']} ({s['source_count']}개): {' | '.join(s['sources'])}\n"
    cross_text += "\n### 안정적 교차 (참고용):\n"
    for s in steady_signals[:10]:
        cross_text += f"  {s['artist']} ({s['source_count']}개)\n"

    # 워치리스트는 최근 7일만 — 오래된 건 History Agent 영역
    from datetime import timedelta
    seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()[:10]
    recent_watchlist = [
        a for a in watchlist.get("artists", [])
        if isinstance(a, dict) and a.get("added_at", "")[:10] >= seven_days_ago
    ]
    watchlist_context = f"## 최근 7일 워치리스트 ({len(recent_watchlist)}명):\n"
    for a in recent_watchlist:
        watchlist_context += f"  [{a['confidence']}] {a['value']}\n"
        
    user_message = f"""교차 플랫폼 신호에서 오늘 처음 등장한 아티스트를 찾아내세요.

워치리스트 "변동 없음" 항목과 메이저 아티스트는 search_web 금지.

{cross_text}
{watchlist_context}
{watchlist_changes}"""

    messages = [{"role": "user", "content": user_message}]
    max_iter = 12
    iteration = 0
    done = False

    while iteration < max_iter and not done:
        iteration += 1

        # 마지막 1턴이 남으면 save_result 강제 호출
        turns_left = max_iter - iteration
        extra_kwargs = {}
        if turns_left <= 1 and not done:
            extra_kwargs["tool_choice"] = {"type": "tool", "name": "save_result"}
            print(f"  🎯 남은 턴 {turns_left}회 — save_result 강제 호출")

        response = create_message_with_retry(
            client,
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            **extra_kwargs,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break
        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = execute_tool(block.name, block.input, watchlist, signals)
                print(f"  🔧 {block.name}: {result[:80]}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
                if block.name == "save_result":
                    done = True

        messages.append({"role": "user", "content": tool_results})
        time.sleep(0.3)

    print(f"  ✅ Cross-Platform 완료 | {iteration}턴 | {len(signals)}개 신호")
    return {"agent": "cross_platform", "signals": signals}


if __name__ == "__main__":
    run()
