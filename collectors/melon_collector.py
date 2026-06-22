import re
import html as html_lib

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

CHART_URLS = {
    "top100": "https://www.melon.com/chart/index.htm",      # 일간(24Hits 기준 상위)
    "hot100": "https://www.melon.com/chart/hot100/index.htm",  # 실시간 Hot100
}


def _clean(text):
    """HTML 엔티티 디코드 + 공백 정규화."""
    text = html_lib.unescape(text)
    text = text.replace("\xa0", " ")  # &nbsp;
    return re.sub(r"\s+", " ", text).strip()


def _parse_melon_html(html):
    """
    멜론 차트 HTML 파싱. BeautifulSoup 없이 정규식만 사용.
    각 곡 행은 data-song-no를 가진 <tr> 단위.
    반환: [{"rank", "title", "artist", "song_id", "trend"}]
    """
    tracks = []

    # 각 곡 행을 song_no 기준으로 분리. rank01(곡명) / rank02(아티스트)는
    # 같은 행 안에 순서대로 등장하므로 행 단위로 끊어서 처리.
    # 행 구분자: data-song-no="..."가 곡마다 반복됨.
    row_pattern = re.compile(r'data-song-no="(\d+)"')
    song_ids = row_pattern.findall(html)

    # rank01: <div class="ellipsis rank01">...<a ...>곡명</a>
    title_pattern = re.compile(
        r'class="ellipsis rank01">.*?<a[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    # rank02: <div class="ellipsis rank02"><a ...>아티스트</a>
    # 첫 번째 a만 (checkEllipsis span 안 중복 a는 제외)
    artist_pattern = re.compile(
        r'class="ellipsis rank02">\s*<a[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    # 등락: rank_up / rank_down / rank_static
    trend_pattern = re.compile(r'class="bullet_icons (rank_up|rank_down|rank_static)"')

    titles = [_clean(t) for t in title_pattern.findall(html)]
    artists = [_clean(a) for a in artist_pattern.findall(html)]
    trends_raw = trend_pattern.findall(html)
    trends = [t.replace("rank_", "") for t in trends_raw]  # up/down/static

    # 곡 식별자는 두 번 등장(앨범재생/곡정보)할 수 있어 dedup하며 순서 유지
    seen = set()
    unique_song_ids = []
    for sid in song_ids:
        if sid not in seen:
            seen.add(sid)
            unique_song_ids.append(sid)

    n = min(len(titles), len(artists))
    for i in range(n):
        track = {
            "rank": i + 1,
            "title": titles[i],
            "artist": artists[i],
            "song_id": unique_song_ids[i] if i < len(unique_song_ids) else None,
            "trend": trends[i] if i < len(trends) else None,
        }
        tracks.append(track)

    return tracks


def collect_melon_chart(chart="top100", limit=100):
    """
    멜론 차트 수집 (인증 불필요, 직접 스크래이핑).
    chart: 'top100' (일간) 또는 'hot100' (실시간)
    """
    url = CHART_URLS.get(chart)
    if not url:
        raise ValueError(f"Unknown chart: {chart}. Use 'top100' or 'hot100'.")

    response = requests.get(url, headers=HEADERS, timeout=15)
    response.raise_for_status()
    response.encoding = "utf-8"

    tracks = _parse_melon_html(response.text)

    if not tracks:
        raise RuntimeError(
            f"멜론 {chart} 파싱 결과 0건 — HTML 구조 변경 가능성. "
            f"셀렉터(rank01/rank02/data-song-no) 확인 필요."
        )

    return tracks[:limit]


def collect_melon_multi(charts=None, limit=100):
    """멜론 여러 차트 한번에 수집 (top100 + hot100)."""
    if charts is None:
        charts = ["top100", "hot100"]

    results = {}
    for chart in charts:
        try:
            results[chart] = collect_melon_chart(chart, limit)
            print(f"  Melon {chart}: {len(results[chart])} tracks")
        except Exception as e:
            import traceback
            print(f"  Melon {chart}: FAILED - {e}")
            traceback.print_exc()
            results[chart] = []

    return results


def detect_new_entries(today_tracks, yesterday_song_ids):
    """
    어제 스냅샷의 song_id 집합과 비교해 신규 진입 추출.
    멜론 마크업은 'new' 클래스를 안 주므로 자체 계산.

    today_tracks: collect_melon_chart 결과
    yesterday_song_ids: 어제 차트의 song_id 리스트/집합
    반환: 신규 진입 트랙 리스트
    """
    yesterday = set(yesterday_song_ids or [])
    new_entries = []
    for track in today_tracks:
        sid = track.get("song_id")
        if sid and sid not in yesterday:
            new_entries.append(track)
    return new_entries


if __name__ == "__main__":
    print("=== 멜론 TOP100 (일간) ===")
    top = collect_melon_chart("top100")
    for t in top[:10]:
        print(f"{t['rank']:>3}. {t['title']} - {t['artist']} [{t['trend']}] (id={t['song_id']})")

    print("\n=== 멜론 Hot100 (실시간) ===")
    hot = collect_melon_chart("hot100")
    for t in hot[:10]:
        print(f"{t['rank']:>3}. {t['title']} - {t['artist']} [{t['trend']}] (id={t['song_id']})")
