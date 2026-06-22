"""
Music Trend Agent — Genre Taxonomy Agent (월 1회 실행)
역할:
- 새로 떠오르는 장르/사운드 탐색
- genre_taxonomy_agent.json 자동 업데이트
- Discovery Agent들의 검색 쿼리가 자동으로 최신 장르를 반영하게 함
- SoundTag taxonomy 기반이지만 여기서 먼저 구현
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
TAXONOMY_PATH = ROOT / "config" / "genre_taxonomy_agent.json"

client = anthropic.Anthropic()
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))


# ──────────────────────────────────────────────
# Taxonomy 관리
# ──────────────────────────────────────────────

def load_taxonomy() -> dict:
    if TAXONOMY_PATH.exists():
        with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    # 기본 taxonomy — SoundTag 기반 초기값
    return {
        "version": "1.0",
        "last_updated": None,
        "genres": {
            "afrobeats": {"status": "established", "search_query": "afrobeats new artist 2026", "added_at": "2026-04-01"},
            "amapiano": {"status": "established", "search_query": "amapiano new artist 2026", "added_at": "2026-04-01"},
            "pluggnb": {"status": "rising", "search_query": "pluggnb new artist", "added_at": "2026-04-01"},
            "hyperpop": {"status": "established", "search_query": "hyperpop new artist 2026", "added_at": "2026-04-01"},
            "jersey club": {"status": "rising", "search_query": "jersey club new artist", "added_at": "2026-04-01"},
            "brazilian funk": {"status": "rising", "search_query": "brazilian funk new artist", "added_at": "2026-04-01"},
            "uk garage": {"status": "established", "search_query": "uk garage new artist", "added_at": "2026-04-01"},
            "drill": {"status": "established", "search_query": "uk drill new artist 2026", "added_at": "2026-04-01"},
            "phonk": {"status": "established", "search_query": "phonk new artist 2026", "added_at": "2026-04-01"},
            "bedroom pop": {"status": "established", "search_query": "bedroom pop new artist 2026", "added_at": "2026-04-01"},
            "alt r&b": {"status": "established", "search_query": "new rb artist 2026", "added_at": "2026-04-01"},
        },
        "deprecated": [],
        "pending_review": [],
    }


def save_taxonomy(taxonomy: dict):
    taxonomy["last_updated"] = datetime.now().isoformat()
    TAXONOMY_PATH.parent.mkdir(parents=True, exist_ok=True)  # fresh clone: config/ 없을 수 있음
    with open(TAXONOMY_PATH, "w", encoding="utf-8") as f:
        json.dump(taxonomy, f, ensure_ascii=False, indent=2)


def get_active_search_queries(taxonomy: dict) -> list:
    """현재 taxonomy에서 YouTube Rising 검색 쿼리 목록 추출."""
    return [
        data["search_query"]
        for genre, data in taxonomy.get("genres", {}).items()
        if data.get("status") in ("established", "rising")
        and data.get("search_query")
    ]


# ──────────────────────────────────────────────
# Tools
# ──────────────────────────────────────────────

TOOLS = [
    {
        "name": "search_web",
        "description": "새로 떠오르는 장르/사운드 탐색.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "add_genre",
        "description": "새 장르를 taxonomy에 추가.",
        "input_schema": {
            "type": "object",
            "properties": {
                "genre_name": {"type": "string", "description": "장르명 (소문자, 영어)"},
                "status": {
                    "type": "string",
                    "enum": ["emerging", "rising", "established"],
                    "description": "emerging: 막 나타남 / rising: 성장 중 / established: 정착됨",
                },
                "search_query": {"type": "string", "description": "YouTube Rising 검색 쿼리"},
                "evidence": {"type": "string", "description": "왜 새 장르로 추가하는지 근거"},
                "parent_genre": {"type": "string", "description": "상위 장르 (있다면)"},
            },
            "required": ["genre_name", "status", "search_query", "evidence"],
        },
    },
    {
        "name": "update_genre_status",
        "description": "기존 장르 상태 업데이트 (emerging→rising→established 또는 deprecated).",
        "input_schema": {
            "type": "object",
            "properties": {
                "genre_name": {"type": "string"},
                "new_status": {
                    "type": "string",
                    "enum": ["emerging", "rising", "established", "deprecated"],
                },
                "reason": {"type": "string"},
            },
            "required": ["genre_name", "new_status", "reason"],
        },
    },
    {
        "name": "save_taxonomy",
        "description": "taxonomy 업데이트 완료 후 저장. 마지막에 1회만 호출.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "이번 업데이트 요약"},
            },
            "required": ["summary"],
        },
    },
]


def execute_tool(tool_name, tool_input, taxonomy):
    if tool_name == "search_web":
        try:
            result = tavily.search(
                query=tool_input["query"],
                search_depth="basic",
                max_results=tool_input.get("max_results", 5),
                include_answer=True,
            )
            return f"{result.get('answer', '')}\n" + "\n".join(
                r.get("content", "")[:200] for r in result.get("results", [])
            )
        except Exception as e:
            return f"검색 실패: {e}"

    elif tool_name == "add_genre":
        genre = tool_input["genre_name"].lower()
        if genre in taxonomy.get("genres", {}):
            return f"이미 있음: {genre}"
        if "genres" not in taxonomy:
            taxonomy["genres"] = {}
        taxonomy["genres"][genre] = {
            "status": tool_input["status"],
            "search_query": tool_input["search_query"],
            "evidence": tool_input["evidence"],
            "parent_genre": tool_input.get("parent_genre", None),
            "added_at": datetime.now().strftime("%Y-%m-%d"),
        }
        return f"✅ 추가: {genre} ({tool_input['status']})"

    elif tool_name == "update_genre_status":
        genre = tool_input["genre_name"].lower()
        if genre not in taxonomy.get("genres", {}):
            return f"없음: {genre}"
        old_status = taxonomy["genres"][genre]["status"]
        taxonomy["genres"][genre]["status"] = tool_input["new_status"]
        taxonomy["genres"][genre]["status_updated_at"] = datetime.now().strftime("%Y-%m-%d")
        taxonomy["genres"][genre]["status_reason"] = tool_input["reason"]
        if tool_input["new_status"] == "deprecated":
            taxonomy.setdefault("deprecated", []).append({
                "genre": genre,
                "deprecated_at": datetime.now().strftime("%Y-%m-%d"),
                "reason": tool_input["reason"],
            })
        return f"✅ {genre}: {old_status} → {tool_input['new_status']}"

    elif tool_name == "save_taxonomy":
        save_taxonomy(taxonomy)
        active_count = sum(
            1 for g in taxonomy.get("genres", {}).values()
            if g.get("status") in ("established", "rising", "emerging")
        )
        return f"✅ Taxonomy 저장: {active_count}개 활성 장르 | {tool_input['summary']}"

    return f"알 수 없는 tool: {tool_name}"


SYSTEM_PROMPT = """당신은 Genre Taxonomy Agent입니다. 월 1회 실행됩니다.

역할:
새로 떠오르는 장르/사운드를 탐색하고 taxonomy를 업데이트합니다.
이 taxonomy는 Discovery Agent들의 YouTube Rising 검색 쿼리에 직접 사용됩니다.

탐색 방향:
- 음악 커뮤니티에서 새로 명명된 장르/사운드
- 기존 장르의 변형 또는 퓨전 ("bedroom pop"에서 "laptop pop"이 파생되는 식)
- 소셜 미디어에서 해시태그로 쓰이기 시작한 새 장르명
- 아직 명확한 이름이 없지만 사운드가 뚜렷한 것 → 임시 명칭 부여

상태 기준:
- emerging: 이름은 있지만 아직 추적할 만한 규모가 아님
- rising: 성장 중, YouTube Rising 쿼리에 포함할 것
- established: 이미 충분히 정착, 기존 쿼리 유지
- deprecated: 더 이상 의미 있는 신호를 주지 않음

작업:
1. 최근 음악 트렌드 검색 (새 장르 탐색)
2. 기존 taxonomy 장르 상태 재검토
3. add_genre / update_genre_status
4. save_taxonomy (1회만)

한국어 작성, 장르명은 영어 유지."""


def run():
    print("🎵 Genre Taxonomy Agent 시작")

    taxonomy = load_taxonomy()

    current_genres_text = "\n".join(
        f"  {genre}: {data['status']} | 쿼리: {data['search_query']}"
        for genre, data in taxonomy.get("genres", {}).items()
    )

    user_message = f"""현재 장르 taxonomy를 검토하고 새로 떠오르는 장르를 추가하세요.

## 현재 taxonomy ({len(taxonomy.get('genres', {}))}개):
{current_genres_text}

## 마지막 업데이트: {taxonomy.get('last_updated', '없음')}

탐색 방향:
1. "emerging music genres 2026" 검색
2. "new music subgenre 2026 trend" 검색
3. 기존 장르 중 deprecated 후보 확인
4. 새 장르 추가 + 상태 업데이트
5. save_taxonomy"""

    messages = [{"role": "user", "content": user_message}]
    max_iter = 15
    iteration = 0
    done = False

    while iteration < max_iter and not done:
        iteration += 1
        response = create_message_with_retry(
            client,
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break
        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = execute_tool(block.name, block.input, taxonomy)
                print(f"  🔧 {block.name}: {result[:100]}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
                if block.name == "save_taxonomy":
                    done = True

        messages.append({"role": "user", "content": tool_results})
        time.sleep(0.3)

    print(f"  ✅ Taxonomy 완료 | {iteration}턴 | {len(taxonomy.get('genres', {}))}개 장르")
    return taxonomy


if __name__ == "__main__":
    run()
