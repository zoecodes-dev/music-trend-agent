"""
Music Trend Agent — Community Agent
역할: 커뮤니티 반응 탐색.
- 공연 규모 / 팔로워 성장률
- K-pop 컴백 팬덤 온도
- 차트에 없는데 커뮤니티에서 돌기 시작한 패턴
- SNS/스트리밍 초기 반응
결과: discovery_results/community.json
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

load_dotenv()

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

DATA_DIR = ROOT / "snapshots"
WATCHLIST_PATH = ROOT / "data" / "watchlist.json"
RESULT_PATH = ROOT / "discovery_results" / "community.json"

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

def prepare_community_data(snapshot: dict) -> str:
    """커뮤니티 바이럴 신호만 추출. 컴백/미디어 데이터는 제외 (다른 Agent 영역)."""
    lines = ["## 커뮤니티 바이럴 후보 (구독자 대비 조회수 이상값)"]

    rising = snapshot.get("sources", {}).get("youtube_rising", [])
    viral_signals = []

    for v in rising:
        subs = v.get("subscriber_count", 1)
        vpd = v.get("views_per_day", 0)
        ratio = vpd / max(subs, 1)

        # 2단계 분류
        # 극단 바이럴 (ratio >= 5x) — 작은 채널에서 조회수가 폭발
        # 주목 신호 (ratio >= 2x, 구독자 50K 이하) — 초기 바이럴
        if ratio >= 5 or (ratio >= 2 and subs <= 50000):
            viral_signals.append({
                "title": v["title"],
                "channel": v["channel"],
                "vpd": vpd,
                "subs": subs,
                "ratio": ratio,
            })

    # ratio 높은 순 정렬, 상위 15개만
    viral_signals.sort(key=lambda x: x["ratio"], reverse=True)

    if not viral_signals:
        lines.append("  (오늘 이상값 없음)")
    else:
        for s in viral_signals[:15]:
            tier = "🔥" if s["ratio"] >= 5 else "⚠️"
            lines.append(
                f"  {tier} {s['title']} — {s['channel']} | "
                f"{s['vpd']:,}/day | 구독자 {s['subs']:,} | {s['ratio']:.1f}x"
            )

    # Last.fm 장르 모멘텀 — 커뮤니티 단위 관심도 변화
    genre_tags = snapshot.get("sources", {}).get("lastfm_genre_tags", {})
    momentum = [
        (g, d.get("listeners_delta_pct"))
        for g, d in genre_tags.items()
        if d.get("listeners_delta_pct") is not None and d["listeners_delta_pct"] > 2
    ]
    if momentum:
        momentum.sort(key=lambda x: x[1], reverse=True)
        lines.append("\n## 장르 단위 커뮤니티 온도 (Last.fm 리스너 증가율)")
        for g, pct in momentum[:5]:
            lines.append(f"  #{g}: +{pct:.1f}%")

    return "\n".join(lines)

TOOLS = [
    {
        "name": "search_web",
        "description": (
            "커뮤니티 반응 심층 탐색. "
            "공연 규모, 팔로워 성장률, SNS 반응, 스트리밍 초기 지표 확인."
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
        "description": "커뮤니티 반응에서 발굴한 신호 추가.",
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
        "description": "커뮤니티 반응 신호 플래그.",
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
                "community_signal": {
                    "type": "string",
                    "description": "어떤 커뮤니티 신호인지 (팬덤반응/공연/팔로워성장/바이럴)",
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
                include_domains=[
                    "reddit.com", "soundcloud.com", "bandcamp.com",
                    "genius.com", "rateyourmusic.com", "tiktok.com",
                    "twitter.com", "x.com", "discord.com",
                    "hiphopheads.com", "popheads.com",
                ],
            )
            results_text = "\n".join(
                f"[{r.get('url', '')[:50]}] {r.get('content', '')[:150]}"
                for r in result.get("results", [])
            )
            return f"{result.get('answer', '')}\n{results_text}"
        except Exception as e:
            return f"검색 실패: {e}"

    elif tool_name in ("add_to_watchlist", "flag_signal", "save_result"):
        return handle_tool_call(
            tool_name, tool_input, watchlist, signals,
            source="community",
            result_path=RESULT_PATH,
            save_watchlist_fn=save_watchlist,
        )


SYSTEM_PROMPT = """당신은 Community Agent입니다.
역할은 **차트 이전의 커뮤니티 신호**를 포착하는 것입니다. Cross-Platform Agent가 이미 차트를, History Agent가 미디어를, Producer Agent가 프로듀서 네트워크를 보고 있습니다. 당신은 그 셋이 못 보는 것만 봐야 합니다.

✅ 플래그해야 하는 것:
- YouTube Rising 이상값 (구독자 대비 조회수 5x 이상) — 작은 채널인데 뷰가 폭발
- Reddit, SoundCloud, Bandcamp, TikTok에서 논의되기 시작한 아티스트/곡
- 공연 솔드아웃, 팔로워 급증 같은 '팬이 실제로 움직인 증거'
- 팬덤의 논란·실망도 신호 (A&R에게는 다 정보)
- Last.fm 장르 리스너 증가율 — 장르 단위 커뮤니티 온도

❌ 절대 플래그하지 말 것:
- 차트 순위 기반 신호 — Cross-Platform Agent 영역
- 컴백 일정 정보 — 이미 K-pop Comeback Intel에 있음
- Billboard/Pitchfork 매체 보도 — History Agent 영역
- 분석·리뷰 유튜브 채널 (예: 데이튠, K-pop explained 같은 채널) — 정보 소비자이지 신호 자체가 아님
- 차트 1위곡 자체 — 이미 차트에 있으면 커뮤니티 신호가 아닙니다

판단 기준:
- "이 아티스트 차트에 있나?" → 있으면 flag하지 말고 스킵
- "이 곡이 주요 매체에 보도됐나?" → 보도됐으면 History 영역이니 스킵
- "이건 진짜 팬/리스너의 행동인가, 아니면 제작자의 정보 제공인가?" → 후자면 스킵

search_web 사용:
- Reddit, SoundCloud, Bandcamp 같은 커뮤니티 플랫폼에서만 검색됩니다 (tool이 제약함).
- 쿼리는 구체적으로: "artist name reddit discussion", "genre soundcloud emerging", "fandom reaction" 등
- 일반 뉴스·차트 쿼리는 쓰지 마세요 — 결과가 안 나옵니다.

media_status 원칙:
- 기본값은 "확인 불가".
- 커뮤니티 신호는 대부분 "확인 불가" 또는 "우리가 먼저"입니다. 매체보다 먼저 잡는 게 Community의 존재 이유이기 때문입니다.

작업 순서 (반드시 준수):
1. 입력 데이터의 바이럴 후보 검토 — 🔥(5x 이상) 우선
2. 각 후보에 대해 커뮤니티 플랫폼에서 search_web으로 반응 확인
3. 실제 커뮤니티 신호가 있는 것만 flag_signal + add_to_watchlist
4. save_result 호출 후 종료 (필수 — 이걸 안 하면 다음 단계가 결과를 못 받습니다)

주의: flag_signal/add_to_watchlist 호출 후에도 반드시 save_result를 호출해야 합니다.
"신호가 부족합니다" 같은 결론도 save_result에 담아 호출하세요.

확신도: 🔴 확실 / 🟡 주시 / 🟢 초기신호
한국어 작성, 고유명사/장르명 영어 유지."""


def run():
    print("🌐 Community Agent 시작")

    snapshot = load_latest_snapshot()
    if not snapshot:
        print("  ❌ 스냅샷 없음")
        return

    watchlist = load_watchlist()
    signals = []
    community_data = prepare_community_data(snapshot)

    user_message = f"""커뮤니티 반응에서 아직 차트에 오르지 않은 신호를 찾아내세요.

## 커뮤니티/컴백 데이터:
{community_data}"""

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

    print(f"  ✅ Community 완료 | {iteration}턴 | {len(signals)}개 신호")
    return {"agent": "community", "signals": signals}


if __name__ == "__main__":
    run()
