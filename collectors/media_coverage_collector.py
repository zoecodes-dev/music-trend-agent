import os
from datetime import datetime
from tavily import TavilyClient
from dotenv import load_dotenv

load_dotenv()

client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))


def _build_media_queries():
    year = datetime.now().year
    month = datetime.now().strftime("%B")
    return [
        f"billboard hot 100 trending {month} {year}",
        f"pitchfork best new music {month} {year}",
        f"rolling stone rising artist {year}",
        f"billboard kpop chart {month} {year}",
    ]


def _extract_artist_mentions(text: str, watch_artists: list) -> list:
    """
    텍스트에서 watch_artists 중 언급된 아티스트 추출.
    대소문자 무관 매칭.
    """
    text_lower = text.lower()
    mentioned = []
    for artist in watch_artists:
        if artist.lower() in text_lower:
            mentioned.append(artist)
    return mentioned


def collect_media_coverage(watch_artists: list = None):
    """
    Billboard, Pitchfork, Rolling Stone 등 공신력 있는 미디어의
    트렌드 커버리지 수집 + 우리 수집 데이터와 교차 검증.

    watch_artists: 교차 검증할 아티스트 목록
                   (Rising + 차트 상위권 아티스트를 넘겨받아 비교)

    반환 구조:
    {
        "articles": [...],
        "crossover_artists": [...],  # 미디어 + 우리 데이터 동시 등장
        "collected_at": "...",
        "tavily_calls_used": N,
    }
    """
    if watch_artists is None:
        watch_artists = []

    queries = _build_media_queries()
    articles = []
    all_mentioned = set()

    for query in queries:
        try:
            result = client.search(
                query=query,
                search_depth="basic",
                max_results=3,
                include_answer=True,
            )

            # answer 요약
            if result.get("answer"):
                mentioned = _extract_artist_mentions(result["answer"], watch_artists)
                all_mentioned.update(mentioned)
                articles.append({
                    "query": query,
                    "type": "summary",
                    "content": result["answer"],
                    "artists_mentioned": mentioned,
                    "sources": [r["url"] for r in result.get("results", [])],
                })

            # 개별 기사
            for r in result.get("results", []):
                snippet = r.get("content", "")[:300]
                mentioned = _extract_artist_mentions(
                    r.get("title", "") + " " + snippet, watch_artists
                )
                all_mentioned.update(mentioned)
                articles.append({
                    "query": query,
                    "type": "article",
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": snippet,
                    "published_date": r.get("published_date", ""),
                    "artists_mentioned": mentioned,
                })

            print(f"  📰 '{query}': {len(result.get('results', []))}개 수집")

        except Exception as e:
            print(f"  ❌ '{query}': {e}")

    crossover_artists = list(all_mentioned)

    result = {
        "articles": articles,
        "crossover_artists": crossover_artists,
        "collected_at": datetime.now().isoformat(),
        "tavily_calls_used": len(queries),
    }

    print(f"  ✅ 미디어 커버리지 수집 완료 | 교차 아티스트: {crossover_artists}")
    return result


if __name__ == "__main__":
    print("📰 Media Coverage 수집 테스트\n")

    test_artists = ["KISS OF LIFE", "aespa", "ILLIT", "Hearts2Hearts", "Kendrick Lamar"]
    data = collect_media_coverage(watch_artists=test_artists)

    print(f"\n🔴 미디어 교차 검증 아티스트: {data['crossover_artists']}")
    print(f"\n📄 수집된 기사 ({len(data['articles'])}개):")
    for a in data["articles"][:3]:
        if a["type"] == "summary":
            print(f"  [요약] {a['query']}")
            print(f"  {a['content'][:150]}")
        else:
            print(f"  [{a.get('title', '')}]")
            print(f"  {a['snippet'][:100]}")
        if a["artists_mentioned"]:
            print(f"  → 언급 아티스트: {a['artists_mentioned']}")
        print()
