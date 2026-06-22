"""
Music Trend Agent - Kworb Collector
kworb.net에서 Apple Music Worldwide 차트 스크래핑.

수집 데이터:
- Apple Music Worldwide Songs Top 50
- 순위, 아티스트, 곡명, Pts(포인트), Pts+(일간 변화), 국가별 순위(US/UK/JP)

인증 불필요, 무료.
"""

import re
import requests

BASE_URL = "https://kworb.net"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}


def _parse_chart_table(html: str, limit: int = 50) -> list:
    """
    Kworb 차트 HTML 테이블 파싱.
    | Pos | P+ | Artist and Title | Days | Pk | Pts | Pts+ | TPts | US | UK | JP |
    """
    tracks = []

    # 테이블 행 추출
    row_pattern = re.compile(
        r'<tr[^>]*>\s*<td[^>]*>(\d+)</td>\s*'   # Pos
        r'<td[^>]*>([^<]*)</td>\s*'              # P+ (순위 변화)
        r'<td[^>]*>(.*?)</td>\s*'                # Artist and Title
        r'<td[^>]*>(\d+)</td>\s*'                # Days
        r'<td[^>]*>.*?</td>\s*'                  # Pk
        r'(?:<td[^>]*>.*?</td>\s*)?'             # (x?) optional
        r'<td[^>]*>([\d,]+)</td>\s*'             # Pts
        r'<td[^>]*>([+-]?[\d,]+)</td>',          # Pts+
        re.DOTALL
    )

    for match in row_pattern.finditer(html):
        if len(tracks) >= limit:
            break

        pos = int(match.group(1))
        pos_change_raw = match.group(2).strip()
        artist_title_html = match.group(3)
        days = int(match.group(4))
        pts = int(match.group(5).replace(",", ""))
        pts_delta_raw = match.group(6).replace(",", "")

        # 순위 변화 파싱
        if pos_change_raw == "=" or pos_change_raw == "":
            pos_change = 0
        elif pos_change_raw == "NEW":
            pos_change = "NEW"
        else:
            try:
                pos_change = int(pos_change_raw)
            except ValueError:
                pos_change = 0

        # Pts+ 파싱
        try:
            pts_delta = int(pts_delta_raw)
        except ValueError:
            pts_delta = 0

        # 아티스트 & 곡명 파싱 (HTML 태그 제거)
        artist_title = re.sub(r'<[^>]+>', '', artist_title_html).strip()
        # "Artist - Title" 형태로 분리
        if ' - ' in artist_title:
            parts = artist_title.split(' - ', 1)
            artist = parts[0].strip()
            title = parts[1].strip()
        else:
            artist = artist_title
            title = ""

        tracks.append({
            "rank": pos,
            "artist": artist,
            "title": title,
            "days_on_chart": days,
            "pts": pts,
            "pts_delta": pts_delta,   # 일간 포인트 변화 (스트리밍 velocity 대리 지표)
            "velocity": pos_change,   # 순위 변화 (기존 velocity 필드와 통일)
        })

    return tracks


def collect_kworb_apple_worldwide(limit: int = 50) -> list:
    """
    Apple Music Worldwide Songs 차트 수집.
    Pts+ 값이 스트리밍 velocity 대리 지표로 활용됨.
    """
    url = f"{BASE_URL}/apple_songs/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        tracks = _parse_chart_table(resp.text, limit=limit)

        if not tracks:
            # fallback: 마크다운 테이블 파싱 시도
            tracks = _parse_markdown_table(resp.text, limit=limit)

        print(f"  Kworb Apple Worldwide: {len(tracks)} tracks")
        return tracks

    except Exception as e:
        print(f"  ❌ Kworb 수집 실패: {e}")
        return []


def _parse_markdown_table(html: str, limit: int = 50) -> list:
    """
    HTML 파싱 실패 시 텍스트 기반 파싱 fallback.
    """
    tracks = []
    lines = html.split('\n')

    for line in lines:
        if len(tracks) >= limit:
            break

        # "| 1 | = | BTS - SWIM | ..." 형태
        if not line.strip().startswith('|'):
            continue

        cols = [c.strip() for c in line.split('|')]
        cols = [c for c in cols if c]

        if len(cols) < 6:
            continue

        try:
            pos = int(cols[0])
        except ValueError:
            continue

        pos_change_raw = cols[1]
        artist_title = cols[2]

        if pos_change_raw == "=":
            pos_change = 0
        elif pos_change_raw == "NEW" or pos_change_raw == "**NEW**":
            pos_change = "NEW"
        else:
            try:
                pos_change = int(pos_change_raw.replace('+', ''))
            except ValueError:
                pos_change = 0

        # Pts, Pts+ 찾기
        pts = 0
        pts_delta = 0
        for i, col in enumerate(cols):
            col_clean = col.replace(',', '').replace('**', '')
            try:
                val = int(col_clean)
                if val > 1000 and pts == 0:
                    pts = val
                elif val > 1000 and pts > 0:
                    pts_delta = int(cols[i].replace(',', '').replace('+', '').replace('**', ''))
                    break
            except ValueError:
                if col_clean.startswith(('+', '-')) and len(col_clean) > 1:
                    try:
                        pts_delta = int(col_clean)
                        break
                    except ValueError:
                        pass

        if ' - ' in artist_title:
            parts = artist_title.split(' - ', 1)
            artist = parts[0].strip().replace('**', '')
            title = parts[1].strip().replace('**', '')
        else:
            artist = artist_title.replace('**', '')
            title = ""

        tracks.append({
            "rank": pos,
            "artist": artist,
            "title": title,
            "days_on_chart": 0,
            "pts": pts,
            "pts_delta": pts_delta,
            "velocity": pos_change,
        })

    return tracks


if __name__ == "__main__":
    print("📊 Kworb Apple Music Worldwide 차트 테스트\n")
    tracks = collect_kworb_apple_worldwide(limit=20)

    print(f"\nTop 20:")
    for t in tracks:
        vel = f" [{t['velocity']:+d}]" if isinstance(t['velocity'], int) and t['velocity'] != 0 else ""
        vel = " [NEW]" if t['velocity'] == "NEW" else vel
        pts_d = f" | Δpts: {t['pts_delta']:+,}" if t['pts_delta'] != 0 else ""
        print(f"  {t['rank']}. {t['artist']} - {t['title']}{vel}{pts_d}")
