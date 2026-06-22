"""
Music Trend Agent - Credit Collector (Tavily 버전)
Tavily API를 사용해서 곡의 프로듀서/작곡가 크레딧 수집.
Rising 감지된 곡들 + 차트 상위권 기준으로 크레딧 조회.

MusicBrainz 대신 Tavily를 쓰는 이유:
- K-pop 신예 아티스트의 MusicBrainz 커버리지 낮음
- Tavily는 Melon, Bugs, NamuWiki, 팬 위키 등 한국 소스까지 커버
- 미국 신예도 AllMusic, Genius, Discogs 등에서 크레딧 수집 가능
"""

import os
import re
from datetime import datetime
from tavily import TavilyClient
from dotenv import load_dotenv

load_dotenv()

client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))


def _extract_names_from_text(text: str) -> dict:
    """
    텍스트에서 프로듀서/작곡가 이름 패턴 추출.
    "Produced by X", "작곡: X", "Prod. X" 등 패턴 감지.
    """
    credits = {
        "producers": [],
        "composers": [],
        "lyricists": [],
    }

    # 직함 키워드(Lyricist, Composer 등) 앞에서 끊기
    prod_patterns = [
        r'[Pp]roduced?\s+by\s+((?:[A-Z][a-z]+(?:\s[A-Z][a-z]+)*(?:\s&\s)?)+?)(?:\s+(?:Lyricist|Composer|Writer|Engineer|Mixer|feat|and\s+[a-z])|\.|,|$)',
        r'[Pp]rod\.?\s+(?:by\s+)?((?:[A-Z][a-z]+(?:\s[A-Z][a-z]+)*)+?)(?:\s+(?:Lyricist|Composer|Writer)|\.|,|$)',
    ]
    comp_patterns = [
        r'[Ww]ritten\s+by\s+((?:[A-Z][a-z]+(?:\s[A-Z][a-z]+)*(?:\s&\s)?)+?)(?:\s+(?:Producer|Lyricist|Engineer)|\.|,|$)',
        r'[Cc]omposed?\s+by\s+((?:[A-Z][a-z]+(?:\s[A-Z][a-z]+)*)+?)(?:\s+(?:Producer|Lyricist)|\.|,|$)',
        r'[Cc]omposer[:/\s]+((?:[A-Za-z]+(?:\s[A-Za-z]+)*,?\s*)+?)(?:\n|Arranger|Lyricist|$)',
    ]

    # 한국어 패턴
    kr_prod_patterns = [
        r'작곡[가]?[:\s]+([가-힣A-Za-z\s,]+?)(?:\n|,|\||작사|편곡|$)',
        r'프로듀서[:\s]+([가-힣A-Za-z\s,]+?)(?:\n|,|\||작사|작곡|$)',
        r'편곡[:\s]+([가-힣A-Za-z\s,]+?)(?:\n|,|\||작사|작곡|$)',
    ]
    kr_lyric_patterns = [
        r'작사[가]?[:\s]+([가-힣A-Za-z\s,]+?)(?:\n|,|\||작곡|편곡|$)',
    ]
    
    for pattern in prod_patterns:
        matches = re.findall(pattern, text)
        for m in matches:
            name = m.strip().rstrip('.,')
            if name and len(name) < 50:
                credits["producers"].append(name)

    for pattern in comp_patterns:
        matches = re.findall(pattern, text)
        for m in matches:
            name = m.strip().rstrip('.,')
            if name and len(name) < 50:
                credits["composers"].append(name)

    for pattern in kr_prod_patterns:
        matches = re.findall(pattern, text)
        for m in matches:
            name = m.strip().rstrip('.,')
            if name and len(name) < 30:
                credits["producers"].append(name)

    for pattern in kr_lyric_patterns:
        matches = re.findall(pattern, text)
        for m in matches:
            name = m.strip().rstrip('.,')
            if name and len(name) < 30:
                credits["lyricists"].append(name)

    # 노이즈 단어로 시작하는 항목 제거
    noise_starts = ('is ', 'are ', 'and ', 'the ', 'a ')
    for key in credits:
        expanded = []
        for name in credits[key]:
            parts = [p.strip() for p in name.split(',') if p.strip()]
            expanded.extend(parts)
        credits[key] = list(dict.fromkeys(expanded))

    return credits


def fetch_track_credits(title: str, artist: str) -> dict:
    """
    단일 곡의 크레딧 수집.
    Tavily로 영어/한국어 쿼리 각 1회 검색.
    """
    credits = {"producers": [], "composers": [], "lyricists": [], "sources": []}

    # 변경 — 곡명을 따옴표로 감싸서 의문문 오인식 방지
    queries = [
        f'"{title}" {artist} producer credits songwriter site:genius.com OR site:kbizoom.com OR site:allmusic.com',
        f'"{title}" {artist} 작곡 작사 site:colorcodedlyrics.com OR site:namu.wiki',
        f'{artist} "{title}" credits composer lyricist',
    ]

    for query in queries:
        try:
            result = client.search(
                query=query,
                search_depth="basic",
                max_results=2,
                include_answer=True,
            )

            full_text = result.get("answer", "")
            for r in result.get("results", []):
                full_text += " " + r.get("content", "")[:300]
                credits["sources"].append(r.get("url", ""))

            extracted = _extract_names_from_text(full_text)

            for key in ["producers", "composers", "lyricists"]:
                for name in extracted[key]:
                    if name not in credits[key]:
                        credits[key].append(name)

        except Exception as e:
            print(f"  ❌ 크레딧 검색 실패 ({title}): {e}")

    return credits


def collect_credits_for_tracks(tracks: list, max_tracks: int = 5) -> dict:
    """
    트랙 리스트에서 크레딧 수집.

    tracks: [{"title": "...", "artist": "..."}, ...]
    max_tracks: Tavily 호출 절약을 위해 최대 N개 (기본 5개, 트랙당 2회 호출)

    반환 구조:
    {
        "track_credits": [...],
        "producer_frequency": {"Name": count},
        "collected_at": "...",
        "tavily_calls_used": N,
    }
    """
    print(f"  🎵 크레딧 수집 중... (최대 {max_tracks}개, Tavily {max_tracks * 2}회)")

    track_credits = []
    producer_freq: dict[str, int] = {}

    for track in tracks[:max_tracks]:
        title = track.get("title", "")
        artist = track.get("artist", track.get("channel", ""))

        if not title or not artist:
            continue

        print(f"  🔍 {title} — {artist}")
        credits = fetch_track_credits(title, artist)

        track_credits.append({
            "title": title,
            "artist": artist,
            "credits": credits,
        })

        for producer in credits.get("producers", []):
            producer_freq[producer] = producer_freq.get(producer, 0) + 1

    producer_freq_sorted = dict(
        sorted(producer_freq.items(), key=lambda x: x[1], reverse=True)
    )

    result = {
        "track_credits": track_credits,
        "producer_frequency": producer_freq_sorted,
        "collected_at": datetime.now().isoformat(),
        "tavily_calls_used": len(track_credits) * 2,
    }

    found = sum(
        1 for t in track_credits
        if any(t["credits"].get(k) for k in ["producers", "composers", "lyricists"])
    )
    print(f"  ✅ 크레딧 수집 완료: {found}/{len(track_credits)}개 매칭")
    if producer_freq_sorted:
        top = list(producer_freq_sorted.keys())[:5]
        print(f"  🎛️ 주목 프로듀서: {top}")

    return result


if __name__ == "__main__":
    print("🎛️ Credit Collector (Tavily) 테스트\n")

    test_tracks = [
        {"title": "Who is she", "artist": "KISS OF LIFE"},
        {"title": "RUDE!", "artist": "Hearts2Hearts"},
        {"title": "KISS KISS KISS", "artist": "나우아임영"},
        {"title": "Die With A Smile", "artist": "Lady Gaga"},
        {"title": "TV Off", "artist": "Kendrick Lamar"},
    ]

    data = collect_credits_for_tracks(test_tracks, max_tracks=3)

    print("\n📋 크레딧 결과:")
    for t in data["track_credits"]:
        print(f"\n  [{t['title']} - {t['artist']}]")
        c = t["credits"]
        if c["producers"]:
            print(f"  Producer: {c['producers']}")
        if c["composers"]:
            print(f"  Composer: {c['composers']}")
        if c["lyricists"]:
            print(f"  Lyricist: {c['lyricists']}")
        if not any([c["producers"], c["composers"], c["lyricists"]]):
            print(f"  (크레딧 없음)")

    if data["producer_frequency"]:
        print(f"\n🎛️ 프로듀서 등장 빈도: {data['producer_frequency']}")
    print(f"\n📊 Tavily 총 {data['tavily_calls_used']}회 사용")