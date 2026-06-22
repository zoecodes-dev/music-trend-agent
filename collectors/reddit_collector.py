"""
reddit_collector.py — Reddit 음악 서브레딧 신호 수집

소스:
  - r/popheads (240K 구독자)
  - r/kpop (1.5M 구독자)
  - r/indieheads (460K 구독자)

수집 방식:
  - JSON endpoint (무인증, User-Agent만 요구)
  - 각 서브레딧에서 rising + hot 두 정렬 수집
  - 신호 임계: score ≥ 5 OR num_comments ≥ 10

아티스트 추출:
  - Claude Haiku 4.5 일괄 추출 (LLM 우선)
  - 정규식 검증 (LLM 누락/오추출 보정)
  - 월 비용 약 $0.35 (매일 1회 기준)

출력: dict — Reddit 신호 리스트 (snapshot.json에 통합)
"""

import json
import os
import re
import time
from typing import Optional

import requests
from anthropic import Anthropic


# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────

SUBREDDITS = ["popheads", "kpop", "indieheads"]
SORTS = ["rising", "hot"]
LIMIT_PER_ENDPOINT = 25

# 신호 임계 — score 또는 comments 중 하나만 충족하면 통과
MIN_SCORE = 5
MIN_COMMENTS = 10

# Haiku 모델
HAIKU_MODEL = "claude-haiku-4-5-20251001"
BATCH_SIZE = 30  # 한 번에 LLM에 보낼 제목 수

# Reddit가 익명 UA 차단하므로 명시적 UA 필수
USER_AGENT = "MusicTrendAgent/0.1 (research; contact: zoe)"


# ─────────────────────────────────────────────
# 정규식 패턴 (LLM 결과 검증/보정용)
# ─────────────────────────────────────────────

# Reddit 음악 서브레딧 관습 패턴
# "ARTIST - SONG" / "ARTIST — SONG" / "ARTIST: SONG"
_PATTERN_DASH = re.compile(r"^([A-Za-z0-9가-힣\.\&\$ ]{2,40})\s*[-—:]\s*[\"']?[\w가-힣]")
# "FRESH [type] ARTIST - SONG" / "[FRESH ALBUM] ARTIST - ALBUM"
_PATTERN_BRACKET = re.compile(
    r"(?:\[FRESH[^\]]*\]|FRESH(?:\s+\w+)?)\s+([A-Za-z0-9가-힣\.\&\$ ]{2,40})\s*[-—:]"
)


def _regex_extract_artist(title: str) -> Optional[str]:
    """제목에서 정규식으로 아티스트명 추출 시도. 매칭 안 되면 None."""
    m = _PATTERN_BRACKET.search(title)
    if m:
        return m.group(1).strip()
    m = _PATTERN_DASH.search(title)
    if m:
        return m.group(1).strip()
    return None


# ─────────────────────────────────────────────
# Reddit JSON endpoint 페치
# ─────────────────────────────────────────────

def _fetch_subreddit(subreddit: str, sort: str, limit: int = LIMIT_PER_ENDPOINT) -> list[dict]:
    """단일 서브레딧 + 정렬 조합 페치. 실패 시 빈 리스트."""
    url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit={limit}"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        children = data.get("data", {}).get("children", [])
        return [c["data"] for c in children if c.get("kind") == "t3"]
    except Exception as e:
        print(f"  ❌ r/{subreddit}/{sort}: {e}")
        return []


def _passes_threshold(post: dict) -> bool:
    """score ≥ 5 OR num_comments ≥ 10."""
    return post.get("score", 0) >= MIN_SCORE or post.get("num_comments", 0) >= MIN_COMMENTS


# ─────────────────────────────────────────────
# Haiku 아티스트 추출
# ─────────────────────────────────────────────

_EXTRACTION_PROMPT = """다음 Reddit 음악 서브레딧 글 제목들에서 언급된 아티스트명을 추출하세요.

규칙:
- 한 제목에 여러 아티스트가 있으면 모두 추출 (콜라보, 비교 등)
- 아티스트가 없는 제목 (예: 일반 토론, 추천 요청)은 빈 리스트
- 그룹명/예명 그대로 사용 (예: "BTS", "Hearts2Hearts", "PinkPantheress")
- 한국 아티스트는 한글/영문 표기 그대로 (예: "AKMU", "검정치마")

JSON 형식으로만 응답. 다른 텍스트 추가 X.

입력 제목 (각 줄 = 1개):
{titles}

출력 형식:
[
  {{"index": 0, "artists": ["BTS"]}},
  {{"index": 1, "artists": ["Taylor Swift", "Ed Sheeran"]}},
  {{"index": 2, "artists": []}}
]
"""


def _extract_artists_batch(titles: list[str], client: Anthropic) -> list[list[str]]:
    """제목 리스트 → 각 제목별 아티스트 리스트."""
    if not titles:
        return []

    numbered = "\n".join(f"{i}: {t}" for i, t in enumerate(titles))
    prompt = _EXTRACTION_PROMPT.format(titles=numbered)

    try:
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # JSON 블록 추출 (```json ... ``` 또는 raw JSON)
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
        parsed = json.loads(text)

        # index 기준으로 정렬해 결과 매핑
        result = [[] for _ in titles]
        for item in parsed:
            idx = item.get("index")
            if isinstance(idx, int) and 0 <= idx < len(titles):
                result[idx] = item.get("artists", [])
        return result

    except Exception as e:
        print(f"  ⚠ LLM 추출 실패: {e} — 정규식 fallback")
        return [[] for _ in titles]


def _verify_with_regex(title: str, llm_artists: list[str]) -> list[str]:
    """LLM 결과를 정규식으로 검증. 정규식이 잡았는데 LLM이 놓친 경우 추가."""
    regex_artist = _regex_extract_artist(title)
    if regex_artist and not any(
        regex_artist.lower() in a.lower() or a.lower() in regex_artist.lower()
        for a in llm_artists
    ):
        # LLM이 놓친 케이스 — 후보 추가
        return llm_artists + [regex_artist]
    return llm_artists


# ─────────────────────────────────────────────
# 메인 수집
# ─────────────────────────────────────────────

def collect_reddit_signals(client: Optional[Anthropic] = None) -> list[dict]:
    """
    Reddit 음악 서브레딧에서 신호 수집 + 아티스트 추출.

    Returns: 신호 리스트. 각 항목 형식:
        {
            "subreddit": str,
            "sort": "rising" | "hot",
            "title": str,
            "score": int,
            "num_comments": int,
            "permalink": str,
            "url": str,
            "created_utc": float,
            "artists": list[str],
        }
    """
    if client is None:
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # ── 1단계: 모든 서브레딧 × 정렬 페치 ──
    all_posts = []
    for sub in SUBREDDITS:
        for sort in SORTS:
            print(f"  🔍 r/{sub}/{sort} 수집 중...")
            posts = _fetch_subreddit(sub, sort)
            for p in posts:
                if _passes_threshold(p):
                    all_posts.append({
                        "subreddit": sub,
                        "sort": sort,
                        "title": p.get("title", ""),
                        "score": p.get("score", 0),
                        "num_comments": p.get("num_comments", 0),
                        "permalink": f"https://reddit.com{p.get('permalink', '')}",
                        "url": p.get("url", ""),
                        "created_utc": p.get("created_utc", 0),
                    })
            time.sleep(1)  # rate limit 보호 (60/min)

    print(f"  📊 임계 통과: {len(all_posts)}개")

    if not all_posts:
        return []

    # ── 2단계: 중복 제거 (같은 제목 한 번만) ──
    seen = set()
    deduped = []
    for p in all_posts:
        key = p["title"].lower().strip()
        if key not in seen:
            seen.add(key)
            deduped.append(p)
    print(f"  📊 중복 제거 후: {len(deduped)}개")

    # ── 3단계: Haiku 일괄 추출 ──
    titles = [p["title"] for p in deduped]
    extracted = []
    for i in range(0, len(titles), BATCH_SIZE):
        batch = titles[i:i + BATCH_SIZE]
        print(f"  🧠 Haiku 배치 {i // BATCH_SIZE + 1}/{(len(titles) - 1) // BATCH_SIZE + 1}")
        extracted.extend(_extract_artists_batch(batch, client))

    # ── 4단계: 정규식 검증 + 결과 합치기 ──
    for post, llm_artists in zip(deduped, extracted):
        post["artists"] = _verify_with_regex(post["title"], llm_artists)

    # ── 5단계: 아티스트 없는 글 필터링 (일반 토론 등) ──
    signals = [p for p in deduped if p["artists"]]
    print(f"  ✅ 최종 신호: {len(signals)}개 (아티스트 추출 성공)")

    return signals


if __name__ == "__main__":
    """단독 실행 시 결과 출력 (테스트용)."""
    from dotenv import load_dotenv
    load_dotenv()

    signals = collect_reddit_signals()
    print(f"\n=== 수집 결과 ({len(signals)}개) ===")
    for s in signals[:10]:
        print(f"  [r/{s['subreddit']}/{s['sort']}] score={s['score']} | {s['artists']}")
        print(f"    {s['title'][:80]}")
