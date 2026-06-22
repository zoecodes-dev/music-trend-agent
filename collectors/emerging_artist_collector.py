"""
Music Trend Agent - Emerging Artist Collector
Tavily API를 사용해서 신예 아티스트 인사이트 수집.

3가지 접근:
1. 프로듀서 워치리스트 — 유명 프로듀서들이 주목하는 신예
2. 피처링 기반 감지 — 메이저 아티스트의 feat. 파트너 추적
3. 미디어 Artist to Watch — 음악 미디어 신예 추천 기사

미국/UK 동향 중심, 아시아 보조.
"""

import os
from datetime import datetime
from tavily import TavilyClient
from dotenv import load_dotenv

load_dotenv()

client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

# ──────────────────────────────────────────────
# 프로듀서 워치리스트
# 이 프로듀서들이 주목하는 신예 아티스트를 추적
# ──────────────────────────────────────────────
PRODUCER_WATCHLIST = {
    # 미국 메이저
    "Metro Boomin": "metro boomin",
    "Pharrell Williams": "pharrell",
    "Max Martin": "max martin",
    "FINNEAS": "finneas",
    "Benny Blanco": "benny blanco",
    "Jack Antonoff": "jack antonoff",
    "Mustard": "mustard producer",
    "Southside": "southside producer",
    # UK
    "Tion Wayne": "tion wayne",
    "Steel Banglez": "steel banglez",
    # 한국
    "ZICO": "zico producer korea",
    "Crush": "crush producer korea",
    "Ryan Jhun": "ryan jhun",
    "Hukky Shibaseki": "hukky shibaseki",
}

# ──────────────────────────────────────────────
# 음악 미디어 소스
# ──────────────────────────────────────────────
MEDIA_SOURCES = [
    "Pitchfork",
    "The Fader",
    "NME",
    "Rolling Stone",
    "Billboard",
    "Complex",
    "Ones to Watch",
    "Bandwagon Asia",
]


def collect_producer_picks(max_producers: int = 5) -> list:
    """
    프로듀서 워치리스트 기반 신예 아티스트 수집.
    Tavily로 각 프로듀서가 최근 주목한 신예 검색.
    max_producers: 검색할 프로듀서 수 (Tavily 호출 절약)
    """
    now = datetime.now()
    year = now.year

    results = []
    all_producers = list(PRODUCER_WATCHLIST.items())
    day_index = datetime.now().weekday()  # 월=0 ~ 일=6
    producer_index = day_index % len(all_producers)
    producers = [all_producers[producer_index]]
    print(f"  🎛️ 오늘의 프로듀서: {producers[0][0]} ({day_index+1}일차/{len(all_producers)}명 순환)")

    for producer_name, search_term in producers:
        try:
            result = client.search(
                query=f"{search_term} new artist feature collab {year}",
                search_depth="basic",
                max_results=3,
                include_answer=True,
            )

            entry = {
                "producer": producer_name,
                "answer": result.get("answer", ""),
                "sources": [],
            }

            for r in result.get("results", []):
                entry["sources"].append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", "")[:200],
                    "published_date": r.get("published_date", ""),
                })

            results.append(entry)
            print(f"  🎛️ {producer_name}: 검색 완료")

        except Exception as e:
            print(f"  ❌ {producer_name}: {e}")

    return results


def collect_featuring_signals(chart_tracks: list, max_tracks: int = 5) -> list:
    """
    차트 트랙에서 feat. 아티스트 추출 후 독립 활동 검색.
    chart_tracks: Apple Music / Last.fm 차트 데이터
    """
    import re

    # feat. 패턴 추출
    feat_pattern = re.compile(
        r'feat\.?\s+([^,\(\)\[\]]+?)(?:\s*[,\(\)\[\]]|$)',
        re.IGNORECASE
    )

    featured_artists = []
    seen = set()

    for track in chart_tracks:
        title = track.get("title", "")
        match = feat_pattern.search(title)
        if match:
            artist = match.group(1).strip()
            if artist.lower() not in seen:
                seen.add(artist.lower())
                featured_artists.append({
                    "artist": artist,
                    "track": title,
                    "main_artist": track.get("artist", ""),
                })

    results = []
    for feat in featured_artists[:max_tracks]:
        try:
            result = client.search(
                query=f"{feat['artist']} artist music 2026 new song",
                search_depth="basic",
                max_results=2,
                include_answer=True,
            )

            results.append({
                "featured_artist": feat["artist"],
                "found_in": f"{feat['track']} (by {feat['main_artist']})",
                "answer": result.get("answer", ""),
                "sources": [r.get("url", "") for r in result.get("results", [])],
            })
            print(f"  🎤 feat. {feat['artist']}: 독립 활동 검색 완료")

        except Exception as e:
            print(f"  ❌ feat. {feat['artist']}: {e}")

    return results


def collect_artist_to_watch() -> list:
    """
    음악 미디어의 Artist to Watch / 신예 추천 기사 수집.
    미국/UK 미디어 중심.
    """
    now = datetime.now()
    year = now.year
    month = now.strftime("%B")

    queries = [
        f"pitchfork artist to watch emerging {month} {year}",
        f"billboard emerging artist rising {month} {year}",
        f"rolling stone new artist spotlight {year}",
        f"the fader new artist {month} {year}",
        f"ones to watch new music {month} {year}",
        f"NME new artist radar {month} {year}",
        f"new artist soundcloud viral {month} {year}",
        # Spotify/TikTok 직접 API는 구조적 차단(Spotify Audio Features 폐쇄·TikTok Research API 차단).
        # 아래는 Tavily 웹검색 프록시 — 해당 플랫폼 *관련 기사*를 검색할 뿐 직접 연동이 아니므로 유효.
        f"spotify editorial playlist new artist {month} {year}",
        f"tiktok viral new artist us {month} {year}",
        f"clash magazine new artist {month} {year}",
        f"DIY magazine emerging artist {year}",       
        f"uk underground music rising {month} {year}", 
    ]

    results = []

    for query in queries:
        try:
            result = client.search(
                query=query,
                search_depth="basic",
                max_results=2,
                include_answer=True,
            )

            if result.get("answer") or result.get("results"):
                results.append({
                    "query": query,
                    "answer": result.get("answer", ""),
                    "articles": [
                        {
                            "title": r.get("title", ""),
                            "url": r.get("url", ""),
                            "snippet": r.get("content", "")[:250],
                            "published_date": r.get("published_date", ""),
                        }
                        for r in result.get("results", [])
                    ],
                })
                print(f"  📰 {query[:50]}...: {len(result.get('results', []))}개 수집")

        except Exception as e:
            print(f"  ❌ {query[:50]}...: {e}")

    return results


def collect_emerging_artist_intel(
    chart_tracks: list = None,
    max_feat_tracks: int = 3,
) -> dict:
    """
    신예 아티스트 인텔 통합 수집.

    chart_tracks: 피처링 감지용 차트 데이터 (apple_kr + lastfm_global)
    max_producers: 프로듀서 워치리스트 검색 수 (기본 3명)
    max_feat_tracks: 피처링 분석 트랙 수 (기본 3개)

    Tavily 호출 수:
    - 프로듀서: max_producers회
    - 피처링: max_feat_tracks회
    - Artist to Watch: 6회
    총: max_producers + max_feat_tracks + 6회
    """
    if chart_tracks is None:
        chart_tracks = []

    print(f"  🎛️ 프로듀서 워치리스트 검색 (오늘 1명)...")
    producer_picks = collect_producer_picks()
    
    print(f"  🎤 피처링 기반 신예 감지...")
    featuring_signals = collect_featuring_signals(
        chart_tracks, max_tracks=max_feat_tracks
    )

    print(f"  📰 Artist to Watch 미디어 수집...")
    artist_to_watch = collect_artist_to_watch()

    total_calls = 1 + len(featuring_signals) + len(artist_to_watch)

    result = {
        "producer_picks": producer_picks,
        "featuring_signals": featuring_signals,
        "artist_to_watch": artist_to_watch,
        "collected_at": datetime.now().isoformat(),
        "tavily_calls_used": total_calls,
    }

    print(f"  ✅ Emerging Artist Intel 수집 완료 (Tavily {total_calls}회)")
    return result


if __name__ == "__main__":
    print("🌟 Emerging Artist Intel 수집 테스트\n")

    # 테스트용 차트 데이터 (feat. 감지용)
    test_tracks = [
        {"title": "KISS KISS KISS (feat. SUNWOO)", "artist": "나우아임영 & Royal 44"},
        {"title": "São Paulo (feat. Anitta)", "artist": "The Weeknd"},
        {"title": "FATHER (feat. Travis Scott)", "artist": "Kanye West"},
        {"title": "Stateside + Zara Larsson", "artist": "PinkPantheress"},
    ]

    data = collect_emerging_artist_intel(
        chart_tracks=test_tracks,
        max_producers=2,
        max_feat_tracks=2,
    )

    print("\n🎛️ 프로듀서 픽:")
    for p in data["producer_picks"]:
        print(f"\n  [{p['producer']}]")
        if p["answer"]:
            print(f"  {p['answer'][:150]}")

    print("\n🎤 피처링 신예:")
    for f in data["featuring_signals"]:
        print(f"\n  [{f['featured_artist']}] — {f['found_in']}")
        if f["answer"]:
            print(f"  {f['answer'][:150]}")

    print("\n📰 Artist to Watch:")
    for a in data["artist_to_watch"]:
        if a["answer"]:
            print(f"\n  [{a['query'][:40]}]")
            print(f"  {a['answer'][:150]}")

    print(f"\n📊 Tavily 총 {data['tavily_calls_used']}회 사용")
