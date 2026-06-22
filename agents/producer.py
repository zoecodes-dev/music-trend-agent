"""
Music Trend Agent — Producer Network Agent
역할: 프로듀서/업계 네트워크에서 신예 탐색.
- 뜨고 있는 프로듀서가 주목하는 아티스트
- 피처링에 반복 등장하는 아티스트
- 아직 메이저가 아닌데 업계에서 먼저 주목받는 신예
결과: discovery_results/producer.json
"""

import json
import os
import time
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
from utils.tool_handlers import handle_tool_call

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

load_dotenv()

DATA_DIR = ROOT / "snapshots"
WATCHLIST_PATH = ROOT / "data" / "watchlist.json"
RESULT_PATH = ROOT / "discovery_results" / "producer.json"

client = anthropic.Anthropic()
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))


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


def prepare_producer_data(snapshot: dict) -> str:
    """emerging_artist 소스에서 프로듀서/피처링 데이터 추출."""
    emerging = snapshot.get("sources", {}).get("emerging_artist", {})
    lines = []

    for p in emerging.get("producer_picks", []):
        if p.get("answer"):
            lines.append(f"[Producer: {p['producer']}]\n{p['answer'][:400]}")
        for s in p.get("sources", [])[:2]:
            if s.get("snippet"):
                lines.append(f"  출처: {s['snippet'][:150]}")

    for f in emerging.get("featuring_signals", []):
        if f.get("answer"):
            lines.append(f"[Featuring: {f['featured_artist']} in '{f['found_in']}']\n{f['answer'][:300]}")

    for a in emerging.get("artist_to_watch", [])[:5]:
        if a.get("answer"):
            lines.append(f"[Media Pick: {a['query'][:40]}]\n{a['answer'][:300]}")

    return "\n\n".join(lines) if lines else "프로듀서 데이터 없음"


TOOLS = [
    {
        "name": "search_web",
        "description": (
            "프로듀서 네트워크 심층 탐색. "
            "특정 프로듀서가 최근 작업한 아티스트, "
            "피처링 아티스트의 독립 활동 확인 등."
        ),
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
        "description": "프로듀서 네트워크에서 발굴한 신예 추가.",
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
        "description": "프로듀서 네트워크 신호 플래그.",
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
                "producer_connection": {"type": "string", "description": "어떤 프로듀서와 연결됐는지"},
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
            source="producer",
            result_path=RESULT_PATH,
            save_watchlist_fn=save_watchlist,
        )


SYSTEM_PROMPT = """당신은 Producer Network Agent입니다.
프로듀서/업계 네트워크에서 아직 알려지지 않은 신예를 탐색합니다.

탐색 대상:
- 뜨고 있는 프로듀서가 최근 작업한 아티스트
- 메이저 곡 피처링에 반복 등장하는 언사인드 아티스트
- 미디어 Artist to Watch 목록 중 아직 차트에 없는 것
- 레이블/매니지먼트 없이 프로듀서 네트워크로만 떠오르는 신예

판단 기준:
- 언사인드 또는 인디인지 확인 — 이미 메이저 레이블이면 관심 낮춤
- 프로듀서 연결고리가 명확할수록 강한 신호
- 피처링 1회보다 여러 아티스트와 반복 연결되는 게 더 강한 신호

search_web: 프로듀서 연결 아티스트 심층 탐색에만 사용. 메이저 아티스트 검색 금지.
텍스트 출력 턴 최소화. tool 호출로 바로 넘어가라.

작업 순서 (반드시 준수):
1. 프로듀서/피처링/미디어 데이터에서 주목할 신예 탐색
2. 필요하면 search_web으로 검증
3. flag_signal + add_to_watchlist
4. save_result 호출 후 종료 (필수 — 이걸 안 하면 다음 단계가 결과를 못 받습니다)

주의: flag_signal/add_to_watchlist 호출 후에도 반드시 save_result를 호출해야 합니다.
"신호가 부족합니다" 같은 결론도 save_result에 담아 호출하세요.

확신도: 🔴 확실 / 🟡 주시 / 🟢 초기신호
한국어 작성, 고유명사/장르명 영어 유지."""


def run():
    print("🎛️ Producer Network Agent 시작")

    snapshot = load_latest_snapshot()
    if not snapshot:
        print("  ❌ 스냅샷 없음")
        return

    watchlist = load_watchlist()
    signals = []

    producer_data = prepare_producer_data(snapshot)

    user_message = f"""프로듀서/업계 네트워크에서 아직 알려지지 않은 신예를 찾아내세요.
메이저 아티스트 검색 금지. 언사인드/인디 신예 중심.

## 프로듀서/피처링/미디어 데이터:
{producer_data}"""

    messages = [{"role": "user", "content": user_message}]
    max_iter = 10
    iteration = 0
    done = False

    while iteration < max_iter and not done:
        iteration += 1

        # 마지막 2턴이 남으면 save_result 강제 호출
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

    print(f"  ✅ Producer 완료 | {iteration}턴 | {len(signals)}개 신호")
    return {"agent": "producer", "signals": signals}


if __name__ == "__main__":
    run()
