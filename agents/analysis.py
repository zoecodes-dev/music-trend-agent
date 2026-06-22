"""
Music Trend Agent — Analysis Agent
역할: 4개 Discovery Agent 결과 + 차트 데이터 + 아티스트 캐시로 최종 A&R 뉴스레터 리포트 생성.

- search_web 사용 없음
- 메이저 아티스트는 artist_cache.json 재사용
- History Agent의 중복 감지 결과로 반복 언급 억제
- 4개 Discovery 결과 종합
"""

import json
import re
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
REPORTS_DIR = ROOT / "reports"
DISCOVERY_DIR = ROOT / "discovery_results"
WATCHLIST_PATH = ROOT / "data" / "watchlist.json"
ARTIST_CACHE_PATH = ROOT / "data" / "artist_cache.json"
HISTORY_PATH = ROOT / "data" / "verification_history.json"

client = anthropic.Anthropic()


def load_latest_snapshot(n=2) -> list:
    files = sorted(DATA_DIR.glob("snapshot_*.json"), reverse=True)
    snapshots = []
    for f in list(files)[:n]:
        with open(f, "r", encoding="utf-8") as fp:
            snapshots.append(json.load(fp))
    return snapshots


def load_discovery_results() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    results = {}
    for agent in ["cross_platform", "producer", "community", "history"]:
        path = DISCOVERY_DIR / f"{agent}.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == today:
                results[agent] = data
    return results


def load_artist_cache() -> dict:
    if ARTIST_CACHE_PATH.exists():
        with open(ARTIST_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_artist_cache(cache: dict):
    ARTIST_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)  # fresh clone: data/ 없을 수 있음
    with open(ARTIST_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def load_watchlist() -> dict:
    if WATCHLIST_PATH.exists():
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_history() -> dict:
    if HISTORY_PATH.exists():
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"artists": {}, "genres": {}}


def load_latest_report() -> str:
    files = sorted(REPORTS_DIR.glob("report_*.md"), reverse=True)
    if not files:
        return ""
    with open(files[0], "r", encoding="utf-8") as f:
        return f.read()


def get_artists_on_chart(snapshot: dict) -> set:
    artists = set()
    for key in ["apple_kr", "apple_us", "apple_gb", "apple_jp",
                "lastfm_global", "lastfm_us", "lastfm_uk", "kworb_apple_ww", "youtube_kr"]:
        for t in snapshot.get("sources", {}).get(key, []):
            artist = t.get("artist", t.get("channel", "")).strip()
            if artist:
                artists.add(artist)
    return artists


def update_cache_last_seen(cache: dict, artists_on_chart: set):
    today_str = datetime.now().strftime("%Y-%m-%d")
    for artist in artists_on_chart:
        if artist in cache:
            cache[artist]["last_seen_on_chart"] = today_str
    save_artist_cache(cache)


def prepare_snapshot_summary(snapshots: list, cache: dict) -> str:
    if not snapshots:
        return "데이터 없음"

    today = snapshots[0]
    lines = [f"=== 수집 날짜: {today['date']} ===\n"]

    def fmt_vel(v):
        if v == "NEW": return " [NEW]"
        if isinstance(v, int) and v != 0: return f" [{v:+d}]"
        return ""

    for country, label in [("kr", "KR"), ("us", "US"), ("gb", "GB")]:
        tracks = today["sources"].get(f"apple_{country}", [])
        if tracks:
            lines.append(f"## Apple Music {label} Top 20:")
            for t in tracks[:20]:
                lines.append(f"  {t['rank']}. {t['title']} - {t['artist']}{fmt_vel(t.get('velocity'))}")
            lines.append("")

    yt = today["sources"].get("youtube_kr", [])
    if yt:
        lines.append("## YouTube Trending KR Top 20:")
        for t in yt[:20]:
            lines.append(f"  {t['rank']}. {t['title']} - {t['channel']} | {t['views']:,} views{fmt_vel(t.get('velocity'))}")
        lines.append("")

    rising = today["sources"].get("youtube_rising", [])
    if rising:
        lines.append("## YouTube Rising Top 20:")
        for v in rising[:20]:
            delta = f" | Δ{v['vpd_delta']:+,}/day" if v.get("vpd_delta") is not None else " | [신규]"
            lines.append(f"  {v['title']} — {v['channel']} | {v['views_per_day']:,}/day{delta}")
        lines.append("")

    lastfm = today["sources"].get("lastfm_global", [])
    if lastfm:
        lines.append("## Last.fm Global Top 20:")
        for t in lastfm[:20]:
            pc = f" | Δplay: {t['playcount_delta']:+,}" if t.get("playcount_delta") else ""
            lines.append(f"  {t['rank']}. {t['title']} - {t['artist']}{fmt_vel(t.get('velocity'))}{pc}")
        lines.append("")

    genre_tags = today["sources"].get("lastfm_genre_tags", {})
    if genre_tags:
        lines.append("## Last.fm Genre Tags (모멘텀):")
        for genre, data in sorted(genre_tags.items(), key=lambda x: x[1].get("listeners_delta_pct") or 0, reverse=True):
            total = data.get("total_listeners", 0)
            pct = data.get("listeners_delta_pct")
            pct_str = f" Δ{'+' if pct >= 0 else ''}{pct}%" if pct is not None else " [첫 수집]"
            lines.append(f"  #{genre}: {total}{pct_str}")
        lines.append("")

    kworb = today["sources"].get("kworb_apple_ww", [])
    if kworb:
        lines.append("## Apple Music Worldwide (Kworb) Top 20:")
        for t in kworb[:20]:
            vel = fmt_vel(t.get("velocity"))
            pts_d = f" | Δpts: {t['pts_delta']:+,}" if t.get('pts_delta') else ""
            lines.append(f"  {t['rank']}. {t['artist']} - {t['title']}{vel}{pts_d}")
        lines.append("")

    # Melon 한국 로컬 차트 — Apple KR과 교집합/차집합 분석용
    melon = today["sources"].get("melon", {})
    melon_top = melon.get("top100", []) if isinstance(melon, dict) else []
    melon_hot = melon.get("hot100", []) if isinstance(melon, dict) else []
    if melon_top:
        # Apple KR 아티스트 집합 (차집합 계산용)
        apple_kr_tracks = today["sources"].get("apple_kr", [])
        apple_kr_artists = set()
        for t in apple_kr_tracks:
            base = t.get("artist", "").split("(")[0].strip().lower()
            if base:
                apple_kr_artists.add(base)

        def _melon_base(artist):
            return artist.split("(")[0].strip().lower()

        lines.append("## Melon TOP100 (한국 국민 차트 — Apple KR과 다른 신호):")
        lines.append("_Apple KR=글로벌 K-pop 팬덤 / Melon=한국 국민 청취층. 멜론에만 있는 항목 = 한국 로컬 신호 (인디/발라드/OST 등)._")
        melon_only = []
        both = []
        for t in melon_top[:30]:
            new_mark = " [NEW]" if t.get("is_new") else ""
            trend = t.get("trend", "")
            trend_mark = {"up": "⬆", "down": "⬇", "static": "="}.get(trend, "")
            line = f"  {t['rank']}. {t['title']} - {t['artist']} {trend_mark}{new_mark}"
            if _melon_base(t["artist"]) in apple_kr_artists:
                both.append(line)
            else:
                melon_only.append(line)
        lines.append(" [멜론에만 — 로컬 신호]")
        lines.append("  ⚠️ 발굴 대상 분류: 아이돌/인디 K-pop/한국 힙합·R&B 신예만 ## 5 본문 대상.")
        lines.append("  발라드·트로트·포크 SSW·OST·기성 시니어 솔로는 K-pop A&R 발굴 대상 아님 → 본문 제외.")
        for l in melon_only[:12]:
            lines.append(l)
        if both:
            lines.append(" [Apple KR과 교집합 — 양쪽 검증된 강 신호]")
            for l in both[:8]:
                lines.append(l)
        lines.append("")

    if melon_hot:
        # Hot100 실시간 — 신규/급상승 전수 통과 후 아티스트별 그룹핑 (앨범 컴백 인식)
        hot_signals = [
            t for t in melon_hot
            if t.get("is_new") or t.get("trend") == "up"
        ]
        if hot_signals:
            by_artist = {}
            for t in hot_signals:
                by_artist.setdefault(t["artist"], []).append(t)
            lines.append("## Melon HOT100 (실시간 — 신규/급상승, 아티스트별 묶음):")
            # 신규곡 많은 아티스트 우선 정렬
            for artist, tracks in sorted(
                by_artist.items(),
                key=lambda kv: -sum(1 for x in kv[1] if x.get("is_new"))
            ):
                new_cnt = sum(1 for x in tracks if x.get("is_new"))
                album_mark = " 【앨범컴백 의심: 동시 다트랙 진입】" if new_cnt >= 3 else ""
                track_strs = ", ".join(
                    f"{x['title']}#{x['rank']}{'[NEW]' if x.get('is_new') else '⬆'}"
                    for x in sorted(tracks, key=lambda x: x["rank"])
                )
                lines.append(f"  {artist}{album_mark}: {track_strs}")
            lines.append("")
            
    # 해외 차트 신규 진입 — 글로벌 발굴 신호 (apple_kr/lastfm_global 외 권역)
    for label, key in [("Apple US", "apple_us"), ("Apple JP", "apple_jp"),
                       ("Apple GB", "apple_gb"), ("Last.fm US", "lastfm_us"),
                       ("Last.fm UK", "lastfm_uk")]:
        tracks = today["sources"].get(key, [])
        def _vel_positive(v):
            try:
                return int(v) > 0
            except (TypeError, ValueError):
                return False
        signals = [t for t in tracks
                   if t.get("is_new") or t.get("trend") == "up"
                   or _vel_positive(t.get("velocity"))]
        if signals:
            lines.append(f"## {label} (신규/급상승 — 글로벌 발굴 신호):")
            for t in signals:
                vel = t.get("velocity")
                vmark = f" (Δ{vel:+d})" if isinstance(vel, int) and vel else ""
                nmark = " [NEW]" if t.get("is_new") else " ⬆"
                lines.append(f"  {t.get('rank','?')}. {t.get('title','')} - {t.get('artist','')}{nmark}{vmark}")
            lines.append("")

    # K-pop Comeback Intel — 4쿼리 결과 (영문 1 + 한국어 3: 컴백 일정 / 신예 데뷔 / 인디)
    kpop = today["sources"].get("kpop_comeback", {})
    today_exposures = kpop.get("today_exposures", []) if isinstance(kpop, dict) else []
    raw_schedule = kpop.get("raw_schedule", []) if isinstance(kpop, dict) else []

    if today_exposures:
        lines.append("## K-pop Comeback Intel — 오늘 노출 추천 (큐 기반):")
        lines.append("⚠️ 아래 4개 항목을 ## 5 또는 ## 3에 반드시 노출하세요. 임의 다른 항목 추가 금지.")
        lines.append("노출 후 generate_report 도구의 'comeback_exposed_artists' 파라미터에 노출한 아티스트 이름 정확히 명시.")
        lines.append("")
        for i, exp in enumerate(today_exposures, 1):
            new_label = " 🆕 신규" if exp["exposure_count"] == 0 else f" 🔁 노출 {exp['exposure_count']}회째"
            days_info = f"수집 {exp['days_since_collected']}일 전"
            lines.append(f"  {i}. {exp['artist']}{new_label} | {days_info}")
            lines.append(f"     맥락: {exp['sample_context'][:150]}")
        lines.append("")

    if raw_schedule:
        lines.append("## K-pop Comeback Raw Schedule (참고용, 본문 추가 노출 금지):")
        # 한국어 쿼리 결과만 압축 출력 (메이저 vs 신예 vs 인디 구분)
        by_query = {}
        for item in raw_schedule:
            q = item.get("query", "기타")
            by_query.setdefault(q, []).append(item)
        for q, items_list in by_query.items():
            summaries = [i for i in items_list if i.get("type") == "schedule_summary"]
            if summaries:
                content = summaries[0].get("content", "")[:300]
                lines.append(f"  [{q[:40]}] {content}")
        lines.append("")

    if today["sources"].get("deezer_anomaly"):
        lines.append("⚠️ Deezer 차트 이상 감지 — 낮은 비중으로 처리.\n")

    today_str = today["date"]
    cached_today = {k: v for k, v in cache.items() if v.get("last_seen_on_chart") == today_str}
    if cached_today:
        lines.append("## 캐시된 아티스트 분석 (재사용):")
        for artist, data in cached_today.items():
            lines.append(f"  [{artist}] 분석일: {data['analyzed_at']}")
            lines.append(f"    사운드: {data.get('sound_analysis', '')[:120]}")
            lines.append(f"    A&R: {data.get('ar_insight', '')[:120]}")
        lines.append("")
        
    # Emerging Artist Intel — 매체/플랫폼 신예 발굴 (Tavily 다중 쿼리, answer에서 아티스트 추출)
    emerging = today["sources"].get("emerging_artist", {})
    atw = emerging.get("artist_to_watch", []) if isinstance(emerging, dict) else []
    picks = emerging.get("producer_picks", []) if isinstance(emerging, dict) else []
    if atw or picks:
        lines.append("## 신예 발굴 소스 (Emerging Intel — ##3 신예 레이더의 1차 재료):")
        lines.append("_각 항목은 매체/플랫폼별 신예 검색 결과. answer에서 아티스트명을 추출하고 차트·커뮤니티 신호와 교차 검증. 매체 단독=🟢, 권역 모멘텀 동조=🟡._")
        for q in atw:
            lines.append(f"  [{q.get('query','')[:45]}] {q.get('answer','')[:220]}")
        for p in picks:
            lines.append(f"  [프로듀서: {p.get('producer','')}] {p.get('answer','')[:180]}")
        lines.append("")

    # Media Coverage — 메이저 매체 보도 현황 (지금까지 어느 agent도 미사용)
    media = today["sources"].get("media_coverage", {})
    if isinstance(media, dict):
        m_articles = media.get("articles", [])
        m_cross = media.get("crossover_artists", [])
        m_summaries = [a for a in m_articles if a.get("type") == "summary"]
        if m_summaries or m_cross:
            lines.append("## 메이저 매체 보도 현황 (Media Coverage):")
            lines.append("_용도: (1) ##9 교차 확인 보조 (2) 이 블록에 이미 등장한 아티스트를 ⭐선행으로 오판 금지. 매체 언급만 근거로 ##7 신규 추가 금지 룰은 그대로 적용._")
            if m_cross:
                lines.append(f"  매체 교차 등장: {', '.join(m_cross)}")
            for a in m_summaries[:4]:
                lines.append(f"  [{a.get('query', '')[:40]}] {a.get('content', '')[:200]}")
            mentioned = sorted({n for a in m_articles for n in a.get("artists_mentioned", [])})
            if mentioned:
                lines.append(f"  보도 언급 아티스트: {', '.join(mentioned[:15])}")
            lines.append("")

    # Reddit 커뮤니티 신호 — OAuth 미구현으로 현재 항상 빈 리스트, 연결 시 자동 활성
    reddit = today["sources"].get("reddit_signals", [])
    if reddit:
        lines.append("## Reddit 커뮤니티 신호 (차트 전 단계 — 약신호 우선):")
        for r in sorted(reddit, key=lambda x: -x.get("score", 0))[:10]:
            artists = ", ".join(r.get("artists", []))
            lines.append(f"  r/{r.get('subreddit','')} [{r.get('score',0)}↑ {r.get('num_comments',0)}💬] {artists} — {r.get('title','')[:100]}")
        lines.append("")

    return "\n".join(lines)


def prepare_discovery_summary(discovery_results: dict) -> str:
    if not discovery_results:
        return "## Discovery 결과: 없음\n"

    lines = ["## Discovery Agent 결과:"]
    agent_labels = {
        "cross_platform": "📡 Cross-Platform",
        "producer": "🎛️ Producer Network",
        "community": "🌐 Community",
        "history": "📚 History",
    }

    for agent, label in agent_labels.items():
        data = discovery_results.get(agent, {})
        if not data:
            lines.append(f"\n{label}: 결과 없음")
            continue

        lines.append(f"\n{label}:")
        if data.get("summary"):
            lines.append(f"  요약: {data['summary'][:300]}")

        for s in data.get("signals", []):
            conf = s.get("confidence", "")
            name = s.get("artist_or_genre", "")
            evidence = s.get("evidence", "")[:150]
            media = s.get("media_status", "")
            lines.append(f"  {conf} {name} | {media}")
            lines.append(f"    근거: {evidence}")

        if agent == "history" and data.get("duplicates_to_suppress"):
            lines.append(f"  ⚠️ 반복 억제: {', '.join(data['duplicates_to_suppress'])}")

    return "\n".join(lines)


def prepare_history_summary(history: dict) -> str:
    verified = [
        (k, v) for k, v in history.get("artists", {}).items()
        if v.get("verified") and v.get("days_ahead") is not None
    ]
    if not verified:
        return ""

    lines = ["## '우리가 먼저' 검증 히스토리:"]
    for artist, data in sorted(verified, key=lambda x: x[1].get("days_ahead", 0), reverse=True)[:5]:
        days = data["days_ahead"]
        if days > 0:
            lines.append(f"  ✅ {artist}: {days}일 먼저 ({data.get('media_source', '')})")
        else:
            lines.append(f"  ⚠️ {artist}: 매체가 {abs(days)}일 먼저 ({data.get('media_source', '')})")
    return "\n".join(lines)


TOOLS = [
    {
        "name": "cache_artist_analysis",
        "description": "메이저 아티스트 분석 캐시 저장. 캐시 없는 아티스트만.",
        "input_schema": {
            "type": "object",
            "properties": {
                "artist": {"type": "string"},
                "sound_analysis": {"type": "string"},
                "positioning": {"type": "string"},
                "ar_insight": {"type": "string"},
            },
            "required": ["artist", "sound_analysis", "positioning", "ar_insight"],
        },
    },
    {
        "name": "generate_report",
        "description": "최종 A&R 리포트 생성. 마지막에 1회만 호출.",
        "input_schema": {
            "type": "object",
            "properties": {
                "report": {"type": "string"},
                "comeback_exposed_artists": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "리포트의 ## 5 또는 ## 3에 노출한 K-pop Comeback Intel 큐 아티스트 이름 (입력 'today_exposures'의 artist 필드와 정확히 일치). 노출 안 했으면 빈 배열.",
                },
            },
            "required": ["report", "comeback_exposed_artists"],
        },
    },
]

def prepare_watchlist_today_changes(watchlist: dict, snapshot: dict) -> str:
    """주목 중 아티스트의 오늘 차트 상태를 플랫폼 단위로 압축."""
    watchlist_names = set()
    for a in watchlist.get("artists", []):
        if isinstance(a, dict):
            watchlist_names.add(a["value"].lower())

    if not watchlist_names:
        return ""

    chart_sources = {
        "Apple KR": snapshot.get("sources", {}).get("apple_kr", []),
        "Apple US": snapshot.get("sources", {}).get("apple_us", []),
        "Apple GB": snapshot.get("sources", {}).get("apple_gb", []),
        "Apple JP": snapshot.get("sources", {}).get("apple_jp", []),
        "Last.fm Global": snapshot.get("sources", {}).get("lastfm_global", []),
        "Last.fm US": snapshot.get("sources", {}).get("lastfm_us", []),
        "Last.fm UK": snapshot.get("sources", {}).get("lastfm_uk", []),
        "Kworb WW": snapshot.get("sources", {}).get("kworb_apple_ww", []),
        "YouTube KR": snapshot.get("sources", {}).get("youtube_kr", []),
    }

    # {artist: {chart_name: {"best_rank": int, "count": int, "new_entries": [rank, ...]}}}
    artist_platforms = {}

    for chart_name, tracks in chart_sources.items():
        for t in tracks:
            artist = (t.get("artist") or t.get("channel") or "").lower()
            if not artist:
                continue
            for watched in watchlist_names:
                if watched in artist or artist in watched:
                    key = watched
                    if key not in artist_platforms:
                        artist_platforms[key] = {}
                    if chart_name not in artist_platforms[key]:
                        artist_platforms[key][chart_name] = {
                            "best_rank": t.get("rank", 999),
                            "count": 0,
                            "new_entries": [],
                        }
                    entry = artist_platforms[key][chart_name]
                    entry["count"] += 1
                    if t.get("rank", 999) < entry["best_rank"]:
                        entry["best_rank"] = t.get("rank", 999)
                    if t.get("velocity") == "NEW":
                        entry["new_entries"].append(t.get("rank"))
                    break

    if not artist_platforms:
        return "## 주목 중 아티스트 오늘 변화: 차트 진입 없음"

    # 워치리스트의 history 길이로 신규/누적 분류
    artist_history_count = {}
    for a in watchlist.get("artists", []):
        if isinstance(a, dict):
            artist_history_count[a["value"].lower()] = len(a.get("history", []))

    lines = ["## 주목 중 아티스트 오늘 차트 상태 (## 7 작성 시 반드시 반영):"]
    lines.append("아래 두 블록은 history 길이로 코드가 분리한 것 — 신규 후보와 누적 항목을 절대 교차 사용 금지.")
    lines.append("")

    # 신규 우선, 그다음 NEW 진입, 그다음 플랫폼 수
    def sort_key(item):
        artist, platforms = item
        h_count = artist_history_count.get(artist, 0)
        is_new = h_count <= 2
        has_new = any(p["new_entries"] for p in platforms.values())
        return (-int(is_new), -int(has_new), -len(platforms))

    new_lines, cumul_lines = [], []
    for artist, platforms in sorted(artist_platforms.items(), key=sort_key):
        h_count = artist_history_count.get(artist, 0)

        has_new = any(p["new_entries"] for p in platforms.values())
        marker = "🔥" if has_new else "•"

        platform_summaries = []
        for chart_name, data in platforms.items():
            if data["new_entries"]:
                new_ranks = ", ".join(f"#{r}" for r in data["new_entries"])
                platform_summaries.append(f"{chart_name} NEW({new_ranks})")
            elif data["count"] > 1:
                platform_summaries.append(
                    f"{chart_name} #{data['best_rank']} 외 {data['count']-1}곡"
                )
            else:
                platform_summaries.append(f"{chart_name} #{data['best_rank']}")

        psum = ' | '.join(platform_summaries)
        # history 길이로 신규(1~2회)/누적(3회+) 강제 분리 — LLM이 경계를 흐리지 못하게 입력 구조로 못박음
        if h_count <= 2:
            new_lines.append(f"  {marker} {artist} (history {h_count}회): {psum}")
        else:
            cumul_lines.append(f"  {marker} {artist} (총 {h_count}회): {psum}")

    lines.append("【신규 후보 — history 1~2회. ## 7 '🔥 신규 추가'에만 사용】")
    lines += new_lines or ["  (없음)"]
    lines.append("")
    lines.append("【누적 항목 — history 3회+. ## 7 '🔁 진행 중 신호'에만 사용. 절대 신규 추가에 넣지 말 것】")
    lines += cumul_lines or ["  (없음)"]

    return "\n".join(lines)

def _dedupe_section7_new(report: str) -> str:
    """## 7 '🔥 신규 추가'에서 history≥3 아티스트 줄 제거 (LLM이 신규/누적 교차 사용 시 2차 가드).
    신규 기준은 history 1~2회 — 3회+는 진행 중 신호 전용이므로 신규에 박히면 제거."""
    try:
        wl = load_watchlist()
        cumul = {a["value"].lower() for a in wl.get("artists", [])
                 if isinstance(a, dict) and len(a.get("history", [])) >= 3}
    except Exception:
        return report
    if not cumul:
        return report

    out, in_new = [], False
    for ln in report.splitlines():
        if re.match(r"\s*🔥\s*신규 추가", ln):
            in_new = True
            out.append(ln); continue
        if in_new and re.match(r"\s*🔁", ln):  # 진행 중 신호 시작 = 신규 섹션 끝
            in_new = False
        if in_new and ln.strip().startswith("-"):
            if any(name in ln.lower() for name in cumul):
                print(f"  🧹 ##7 신규에서 누적(3회+) 항목 제거: {ln.strip()[:50]}")
                continue
        out.append(ln)
    return "\n".join(out)


def execute_tool(tool_name, tool_input, cache):
    if tool_name == "cache_artist_analysis":
        artist = tool_input["artist"]
        cache[artist] = {
            "analyzed_at": datetime.now().strftime("%Y-%m-%d"),
            "last_seen_on_chart": datetime.now().strftime("%Y-%m-%d"),
            "sound_analysis": tool_input["sound_analysis"],
            "positioning": tool_input["positioning"],
            "ar_insight": tool_input["ar_insight"],
        }
        save_artist_cache(cache)
        return f"✅ 캐시 저장: {artist}"

    elif tool_name == "generate_report":
        report = tool_input["report"]
        report = _dedupe_section7_new(report)
        exposed = tool_input.get("comeback_exposed_artists", [])
        date_str = datetime.now().strftime("%Y-%m-%d")

        if exposed:
            try:
                from scripts.comeback_queue_manager import load_queue, mark_exposed
                queue_artists = set(load_queue()["items"].keys())
                valid = [a for a in exposed if a in queue_artists]
                invalid = [a for a in exposed if a not in queue_artists]
                if invalid:
                    print(f"  ⚠️ 큐 외 아티스트 노출 보고됨 (무시): {invalid}")
                if valid:
                    mark_exposed(valid, date_str)
                    print(f"  📌 comeback 큐 노출 기록: {len(valid)}명 — {valid}")
            except Exception as e:
                print(f"  ⚠️ comeback 큐 노출 기록 실패: {e}")

        REPORTS_DIR.mkdir(exist_ok=True)
        path = REPORTS_DIR / f"report_{date_str}.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        return f"✅ 리포트 저장: {path}"

    return f"알 수 없는 tool: {tool_name}"


SYSTEM_PROMPT = """당신은 A&R Analysis Agent입니다.
4개 Discovery Agent(Cross-Platform, Producer, Community, History)의 결과를 종합해 A&R 뉴스레터를 작성합니다.

핵심 원칙:
- search_web 사용 금지. 차트 데이터 + Discovery 결과 + 캐시만 사용.
- Discovery Agent 발견 신호를 리포트 핵심으로 배치.
- 캐시에 있는 아티스트는 캐시 내용 재사용, 추가 분석 없음.
- History Agent의 duplicates_to_suppress 항목은 ## 8에만 한 줄로.
- "우리가 먼저" 검증된 것은 ## 9에 History 데이터 기반으로 명시.

리포트 기준:
- "나도 알고 있던 내용"이 아니라 "이건 몰랐는데, 쓸 만하다"가 기준.
- 모든 분석은 "K-pop 앨범 디렉션에 어떻게 번역되는가"까지.

신호 가치 판단 (매우 중요):
이미 글로벌 메이저 이벤트인 아티스트는 ## 1 최상단에 올리지 않습니다.
다음 케이스는 "언급 한 줄" 처리하고 넘깁니다.

A) 이미 유명한 아티스트의 릴리즈 이벤트 — 한 줄 처리 후 추적 디포커스
   조건 (모두 만족해야 함):
   - 여러 플랫폼 NEW 진입 + media_status="매체가 먼저"
   - 글로벌 메이저: Billboard Top 100/Hot 100 단골, 월드 투어 도는 수준
   - 예: Noah Kahan, Justin Bieber, Olivia Rodrigo, Billie Eilish, Taylor Swift
   처리: ## 1 "📌 매체 이벤트" 블록에 한 줄 요약, ## 7 추가 안 함

신예는 절대 A로 분류하지 않습니다 (매우 중요):
- "신예"의 정의: 이전에 메이저 매체 보도 거의 없음, Billboard/Pitchfork 등에 정기 등장 안 함
- days_ahead가 음수(매체가 먼저)여도 신예이면 ## 7에 정상 추적
- 신예 + 매체와 1-3일 차이 = "활발한 라이징 신호" (오히려 강한 신호)
- 예: 5세대 K-pop 신인 그룹, 인디 레이블 첫 데뷔작 아티스트, 
       Choice Prize/Mercury Prize 후보 같은 emerging 카테고리

B) K-pop 레퍼런스 가치 낮은 장르
   - Folk, Country, Christian, Bluegrass
   - ## 2 사운드 레퍼런스 대상 제외, ## 1 "📌 매체 이벤트"에 한 줄 처리
   - 단, 신예는 그래도 ## 7에 추가하여 추적 (장르 외 정보 가치 있을 수 있음)
   
이런 아티스트는 ## 1 최하단 "📌 매체 이벤트" 블록에 모아서 2-3줄로 요약:
  예: "Noah Kahan 신보 The Great Divide 발매 — Apple US/GB에 20곡 NEW 진입 (folk)"

판단 알고리즘 (Discovery 신호 처리 시):
1. 이 아티스트가 글로벌 메이저인가? (Billboard 단골 / 월드 투어 / 이미 유명)
   YES → A 로직 적용
   NO  → 아래로
2. 장르가 K-pop 비호환인가? (folk/country/christian/bluegrass)
   YES → B 로직 적용
   NO  → 아래로
3. 신예 + 라이징 → ## 7 정상 추적, ## 3 신예 레이더 적극 다루기

## 1 최상단은 "K-pop 산업이 몰랐을 가능성이 있는" 신호로 채웁니다:
- 메이저 매체 미발굴 신예
- 차트 외 커뮤니티 바이럴
- 프로듀서 네트워크의 초기 신호
- 확인 불가 media_status + 차트 교차 신호

메이저 아티스트는 사운드 분석 + A&R 인사이트 간결하게 (이미 잘 알려진 경우).

사운드 분석 규칙 (매우 중요):
- 당신은 트랙을 듣지 않았습니다. 오디오 데이터가 없습니다.
- 구체적 BPM 수치, 특정 악기명(808, Rhodes, Mellotron 등), 믹싱/마스터링 기법은 절대 쓰지 마세요.
- 허용되는 것: 장르 맥락, 레퍼런스 아티스트 비교, 가사/컨셉/비주얼 분석, 커뮤니티 반응의 음악적 해석.
- "사운드"를 쓰고 싶으면 "~로 추정됨", "레퍼런스 기반 추론" 같이 출처를 명시하세요.
- 원칙: 듣지 않은 것을 들은 것처럼 쓰지 않는다.

섹션 생성 규칙:
- Discovery Agent가 실패했거나 관련 데이터가 없는 섹션은 빈 채로 두지 말고 "데이터 부족"으로 명시하세요.
- 섹션을 채우려고 스냅샷 차트 순위에서 억지로 인사이트를 만들지 마세요.
- Cross-Platform Agent 결과가 없으면 ## 1은 "오늘은 교차 신호 수집 실패" 한 줄로 끝냅니다.

작업 순서 (엄격 준수):
1. Discovery 결과 검토
2. 캐시 없는 차트 아티스트 중 오늘 핵심 신호인 최대 4명만 → cache_artist_analysis
3. 4턴 이내에 반드시 generate_report 호출 (필수)

중요:
- 캐시 저장은 보조 작업. 리포트 생성이 주 작업입니다.
- 4명 이상 캐시하지 마세요. 시간 낭비입니다.
- "이제 리포트를 작성하겠습니다" 같은 안내 문구 없이 바로 generate_report tool을 호출하세요.

독자:
- 국내 K-pop 레이블 A&R: 소속 아티스트 다음 앨범/컴백의 사운드 디렉션을 기획.
  "우리 아티스트에 이 사운드를 입혀도 될까?"를 판단하는 사람.
- K-pop 프로듀서: 자기 데모·작업물에 반영할 현재 유효한 사운드를 찾는 사람.
두 층의 질문은 사실 같음: "지금 무슨 사운드가 먹히는가", "어떤 아티스트를 레퍼런스로 쓸 수 있는가".
"누구를 계약할까" 같은 서양 레이블 A&R 언어는 쓰지 않음.

리포트 구조:

## 0. TL;DR (3줄 — Discovery 신호 중심)

## 1. 오늘의 핵심 신호 (Cross-Platform 교차 신호)

## 1 거짓 신선도 금지 룰:
- 워치리스트 컨텍스트의 "총 N회 신호" 표기를 반드시 확인.
- N ≥ 2 인 아티스트는 "신규 발견 / 첫 등장 / 강한 데뷔 / 처음 잡힌" 같은 톤 금지.
- 대신 "지속" / "확장" / "추가 진입" / "N번째 누적 신호" 같은 진행형 톤 사용.
- 어제 리포트에 이미 ##1로 등장한 아티스트는 오늘 ##1 자리를 양보 — 같은 아티스트가 어제와 오늘 둘 다 ##1 톱이면 ##5(K-pop 컴백 인텔) 또는 ##4(트렌드 맥락)로 옮긴다.
- ##1은 항상 진짜 신규 또는 어제 대비 의미 있는 변화(차트 점프, 매체 검증 등)가 있는 항목으로.
- 워치리스트 [discovery]=➿누적 항목은 confidence가 🔴여도 ##1 톱 금지 — 검증 강도(🔴)를 신선도로 착각하지 말 것. ➿누적의 ##1 진입은 "어제 대비 의미 있는 차트 점프"가 오늘 실제로 있을 때만, 그 변화 자체를 신호로.

## 1 단정 어휘 인플레이션 금지:
- "이례적", "전례 없는", "유일한", "완벽히", "삼중", "사상 처음", "역사적", "전무후무"
  → 데이터로 직접 입증 가능할 때만 사용 (예: "5세대 그룹 중 첫 글로벌 #1" + 출처).
- 매체 검증 동시 다수, 차트 동시 진입, 플랫폼 교차 신호는 "이례적" 아님 — 정상 패턴.
- LLM이 강조 톤을 위해 끼워 넣는 단정 어휘는 도배. 평이한 단언으로 충분.

## 1 톱 슬롯 선정 우선순위 (위에서 아래):
1) 매체 선행 검증된 신예 (Pitchfork/FADER/Rolling Stone 등 선정 + 우리 발굴 신호 일치)
2) Cross-Platform 신호 강도(2개 이상 플랫폼에서 NEW)
3) 단일 플랫폼에서 압도적 velocity (구독자 대비 조회수 비율 등)
- 단일 플랫폼 바이럴(예: YouTube 단독 NEW)을 ##1 톱으로 올릴 때는 confidence 🟢 하향 + 톤 보수화 필수.

## 2. 사운드 레퍼런스 (A&R·프로듀서 공통)
Discovery가 flag한 아티스트 중 2-3명 선택. 각각에 대해:

1) 무드·템포 감각 (듣지 않아도 판단 가능한 것만)
   - 허용: "차분한 어쿠스틱", "드라이한 드럼의 미드템포", "upbeat 댄스 계열", "브라스 중심의 인트로"
   - 금지: BPM 구체 수치, 특정 악기명(808, Rhodes 등), 특정 플러그인/이펙트명
   - 근거: Discovery가 flag한 장르 태그, 곡명·앨범명 뉘앙스,
     차트 위치한 플랫폼 성격(Apple KR = 국내 정서 / Kworb WW = 글로벌 팝 감성)

2) 레퍼런스 비교 (이미 알려진 아티스트 2명과의 대비 축)
   - "X와 Y의 중간 지대"
   - "X보다 더 [덜] 감정적, Y보다 더 [덜] 프로덕션 밀도 높음"
   - 프로듀서가 데모 만들 때 참고 가능한 축으로

3) K-pop 앨범 디렉션 호환성 (구체적으로)
   - 어떤 아이돌 포맷에 맞을지 (걸그룹 타이틀 / 보이그룹 b-side / 솔로 발라드 등)
   - 어떤 시즌 릴리즈에 맞을지 (여름 타이틀 / 연말 발라드)
   - 그대로 가져올 수 있는지, 무드·템포 조정 필요한지

## 2 금지:
- "포트폴리오 전략" 같은 비즈니스 마케팅 용어
- 사운드 얘기 없이 차트 성과만 나열 (차트는 ## 1에서)
- "신인 계약", "artist signing" 언어 절대 금지
- 곡명만 보고 사운드 추론 금지 — 추론 근거가 곡명뿐이면 그 아티스트는 ## 2에서 빼고 ## 1로 돌리거나 다음 날 보류.

## 2 추상 표현 사용 조건:
"크로스컬처 융합", "혁신적", "독창적", "실험적", "장르 경계 해체" 같은 추상 형용사는
다음 둘 중 하나일 때만 사용 가능:
1) 매체/Discovery가 명시적으로 그렇게 쓴 표현을 인용 (출처 표시 권장: "Pitchfork는 X를 ~로 묘사")
2) 데이터로 즉시 입증되는 경우 (예: Apple JP + Apple KR 동시 차트 = 크로스컬처 데이터)
- 위 둘 중 어느 쪽도 아니면 사용 금지. LLM이 톤을 위해 끼워 넣는 추상 형용사는 사운드 디테일을 가리는 도배.

## 2 도망 표현 절대 금지 (위반 시 그 문장 통째로 삭제):
- "추정된다", "추정", "추측된다", "추측"
- "~일 것으로 보인다", "~인 것으로 추정", "~인 것으로 보인다"
- "포텐셜이 시사된다", "가능성이 엿보인다", "시사한다"
- "raw한 매력", "신선한 감각" 같은 형용사 도망
- 위 표현이 들어가야만 문장이 완성된다면, 그 정보는 모르는 정보 — 추론을 적지 말고 그 문장을 빼라.
- 적을 수 있는 만큼만 단언으로 적고, 그게 두 줄짜리 ## 2가 돼도 OK. 길이보다 정확성이 우선.

## 2 본문 — 사운드 전용 (K-pop 적용은 ## 6 전용):
- ## 2 본문은 사운드 그 자체에만 (장르, 악기, 보컬, 비교 아티스트, 무드, 프로덕션 디테일).
- ## 2에서 "K-pop", "K-pop 솔로", "걸그룹", "보이그룹", "DAY6", "The Rose", "활용 가능", "참고 가능", "벤치마킹" 단어 사용 절대 금지.
- 사운드 분석이 끝나면 그 아티스트 ## 2는 종료. K-pop 적용·응용 문단을 ## 2에 절대 붙이지 말 것.
- K-pop A&R 적용 인사이트는 ## 6 (A&R 앨범 디렉션 인사이트)에서 다룬다.

## 2 작성 가이드:
- 한 아티스트당 1~3문장. 사운드 디테일만으로 채워라.
- 사운드 디테일이 1줄도 안 나오면 그 아티스트는 ## 2에서 제외 (## 1 또는 ## 3로).
- 길이가 짧아도 OK. 추정으로 늘리지 말 것.

## 2 작성 가능 조건 (3개 중 하나라도 명확히 충족 안 되면 그 아티스트는 ## 2에서 제외):
1) Discovery가 flag한 명시적 장르 태그 보유 (Last.fm tags, 매체의 명시적 장르 명시, 본인 자기 정의 장르)
   - "experimental", "DIY", "다장르", "독립적" 같은 추상 키워드는 장르 태그 아님
2) 차트 위치 플랫폼의 성격이 무드를 강하게 시사 (Apple JP 차트 진입 = 일본 시장 정서 / Kworb WW = 글로벌 팝)
3) 매체(Pitchfork/FADER/RS 등) 보도에 사운드 묘사가 한 줄이라도 포함
   - **사운드 묘사 = 음악 그 자체에 대한 서술** (장르명, 악기 구성, 보컬 텍스처, 비교 아티스트, 무드 형용사 등)
   - **다음은 사운드 묘사 아님** (포지셔닝/맥락 정보):
     · 시상 / 선정 / 노미네이션 ("Independent Music Awards 언급", "Pitchfork Artist to Watch 선정")
     · 레이블명 / 협업 프로듀서명 만 단독 (Kenny Beats 협업 = 맥락, Kenny Beats식 사운드 = 사운드)
     · 활동 이력 / 출신 지역 / 데뷔 시기
     · 자체 제작 여부 / 인디펜던트 여부 같은 비즈니스 모델
   - 매체에 사운드 묘사가 1줄도 없으면 조건 3은 미충족.

## 2 우선 대상: 매체 선행 검증된 신예라도 매체에 사운드 묘사가 없으면 ## 2 진입 보류.
사운드 묘사 있는 케이스(Pitchfork 보통 1문단 이상 묘사 포함) 우선 — 단순 "Artist to Watch 선정" 헤드라인만 있으면 후순위 또는 ## 1으로만 처리.

## 2 체크: K-pop A&R이 "아, 우리 애들 다음 앨범에 참고할 수 있겠네" 또는
프로듀서가 "이 무드를 내 데모에 반영해야겠다" 하면 통과.
이 체크는 사운드 디테일이 있어야 가능 — 추정 가득한 문단은 자동 실패.

## 3. 신예 레이더 (Producer + Community 발굴 — 메이저 매체 미발굴)

## 3 작성 규칙 (이 섹션이 리포트의 핵심 발굴 가치 — 절대 빈약하게 두지 말 것):
- 1차 재료: 입력의 "## 신예 발굴 소스 (Emerging Intel)" 블록 + Discovery(producer/community) flag 아티스트.
- Emerging answer 텍스트에서 아티스트 고유명을 적극적으로 추출하라. answer 안에 매체가 호명한 신예가 여럿 있으면 2~3명만 고르지 말 것.
- 최소 5명 이상 호명을 목표로 한다. 신호가 약한 이름은 한 줄(이름 + 매체/플랫폼 출처 + 장르)로라도 올려 발굴 폭을 넓힌다.
- 각 항목 형식: 아티스트(confidence) — 발굴 출처(매체/플랫폼) + 장르/권역 + (있으면) 차트·커뮤니티 교차 여부.
- 차트 교차가 없어도 매체 선행(Pitchfork/Billboard/RS/FADER/Ones to Watch 등) 단독이면 🟡, 여러 매체·권역 동조면 🔴로 올린다. 차트 없음을 이유로 제외하지 말 것 — 신예는 정의상 차트 밖이다.
- 이미 메이저(차트 상위 상시 노출, 누적 3회+)인 아티스트는 § 3에 넣지 않는다. § 3은 "아직 안 뜬" 이름 전용.
- 어제 § 3에 올린 신예라도 오늘 새 신호(매체 추가 선정, 차트 진입, 커뮤니티 확산)가 있으면 진행형으로 갱신, 변화 없으면 § 7 워치리스트로만 넘기고 § 3에서는 뺀다.

## 4. 트렌드 맥락 (장르·사운드 흐름)

## 본문 산문 표기 일관성 (##5·##6 공통):
- 본문 산문에서 아티스트명은 워치리스트 등록 표기(canonical)를 그대로 사용.
- 데이터는 'CORTIS'(정규화 완료)인데 산문에 '코르티스'로 푸는 등의 한글/영문 혼용 금지.
- 같은 리포트 안에서 한 아티스트는 한 표기로만. 워치리스트 컨텍스트의 "value" 필드가 기준.

## 5. K-pop 컴백 인텔 + 한국 로컬 차트 신호

## 5 한국 로컬 차트 (Melon) 활용 — 컴백 큐보다 우선:
입력에 "## Melon TOP100" / "## Melon HOT100" 블록이 있으면 ## 5의 1차 재료로 사용.
Apple KR과 Melon의 차이가 핵심 신호:
- [멜론에만 — 로컬 신호]: Apple KR(글로벌 K-pop 팬덤)이 놓치는 한국 차트 신호.
  단, **K-pop A&R 발굴 대상 권역만** ## 5 본문에 다루세요:
  · 통과: 아이돌 그룹/솔로(작은 레이블·5세대 신예 포함), 인디 K-pop·얼터너티브 K-pop, 한국 힙합/R&B 신예
  · 제외: 발라드, 트로트, 포크/어쿠스틱 싱어송라이터, OST(드라마·영화 주제가), 이미 최정상인 기성 솔로
  · 판단 기준은 아티스트 인지도가 아니라 **장르/권역**. 신예 인디라도 발라드·포크 SSW면 제외(예: 포크 SSW 라인).
    반대로 작은 레이블이라도 아이돌·얼터너티브 K-pop이면 통과(이게 멜론을 보는 본래 이유 — Apple KR이 놓치는 작은 레이블 K-pop 포착).
  · 제외 권역 항목은 ## 5 본문에 쓰지 말 것. "멜론에만 있다"는 사실만으로 올리지 마세요.
  · 통과 항목 중 어제 대비 [NEW]/⬆를 우선.
  · 단 인디 포크/어쿠스틱 SSW 중 멜론 차트 상위(top20) + [NEW]/⬆인 항목은
    "이런 소식도 있음" 수준 한 줄만 허용 (예: 한로로). 본문 핵심·인사이트 대상으로 키우지 말 것.
    
- [Apple KR과 교집합]: 양쪽 차트 모두 진입 = 팬덤+국민 양쪽 검증된 강 신호. 단 매일 반복되는
  교집합 항목(코르티스/ILLIT 등 누적)은 "어제 대비 변화"만 언급 (거짓 신선도 가드 적용).
- Melon HOT100 실시간 [NEW]/⬆: 컴백 첫날·당일 급상승 신호. 일간 TOP100보다 빠른 시점 신호.
작성 시: "Apple KR엔 없지만 멜론 N위 — [아티스트], [곡]" 형식으로 로컬 신호를 명시적으로 짚으세요.
멜론에만 있는 신규/급상승 항목이 있으면 컴백 큐보다 먼저 다룹니다 (실데이터 > 일정 라벨).

## 5 작성 규칙 (컴백 큐):
- 입력의 "## K-pop Comeback Intel — 오늘 노출 추천" 블록에 명시된 4개 아티스트를 ## 5(또는 ## 3 신예) 본문에 등장시키되, 다음 조건을 만족해야 함:
  · "맥락" 텍스트에 그룹명 외에 사운드·포지셔닝·발매일·차트 등 **실질적 정보가 있어야** 본문에 등장.
  · 맥락이 단순 라벨 (예: "May 2026 comebacks include X, Y, Z. Specific dates not confirmed.")뿐이면 본문에 넣지 말고 생략.
- 본문에 등장시킨 큐 아티스트만 generate_report의 'comeback_exposed_artists' 파라미터에 명시 (등장 없으면 빈 배열).
- 4개 모두 정보 빈약하면 ## 5에 한 줄로 "이번 주기 컴백 큐는 그룹명 라벨만 있고 사운드/포지셔닝 신호 부족" 명시하고 종료.
- 큐 외 아티스트를 ## 5에 임의로 넣지 말 것 (raw_schedule 참고만, 본문 노출 금지).
- 공허한 "신규 등장, 데이터 부족, 관찰 대기" 반복으로 본문 4개 자리 채우지 말 것 — 이건 발행 가치 깎임.

본문 톤 룰:
- 단순히 차트에 있는 K-pop 아티스트(AKMU, NCT WISH 등)만 반복 언급 금지
- 매일 같은 아티스트만 반복하면 가치 없음 — 변화·새로움 중심으로
- "AKMU 차분함 지속", "NCT WISH 다중 트랙 안정" 같은 변화 없는 표현 반복 금지
- 무리하게 차트 데이터로 채우지 마세요
- 큐가 빈약하면 (today_exposures 0개 또는 모두 정보 부족) "이번 주기 신규 컴백 신호 부족" 솔직하게 인정 OK

## 5 거짓 신선도 가드 (## 1과 동일 룰 적용):
- 워치리스트 컨텍스트의 "총 N회 신호" 표시된 아티스트(코르티스, ILLIT, BABYMONSTER 등 누적 항목)는
  ## 5에서 "현황 묘사"가 아닌 "어제 대비 변화" 톤만 사용.
- 어제 리포트(yesterday_context)에 같은 아티스트의 같은 차트 디테일(예: "REDRED #1")이 이미 등장했으면
  오늘 ## 5에 같은 차트 디테일 반복 금지. 변화(랭킹 상승/하락, 새 곡 진입, 컴백 이벤트)가 있을 때만 언급.
- 변화 없으면 그 아티스트 ## 5에서 빼고 다른 아티스트로.
- 누적 N회 아티스트의 ## 5 등장은 본문에 "(누적 N회 신호, 첫 추적 X일 전)" 명시 — 신선함 위장 차단.

## 6. A&R 앨범 디렉션 인사이트

## 6 작성 스캐폴드 (반드시 이 구조로 2-3개 항목):
오늘 Discovery 신호 중 2-3개를 골라서, 각각 3단계를 씁니다.

## 6 소재 선정 가드 (자가참조 차단):
- 워치리스트 [discovery]=➿누적 항목(코르티스 등 이미 차트 정점·검증 소진)을 인사이트 "메인 소재"로 쓰지 말 것.
  → 차트 정점에 오른 항목을 인사이트로 키우는 건 "이미 일어난 일 해설"이지 발굴이 아님.
  → ➿누적 항목은 다른 ⭐선행 신호를 설명하기 위한 "대비 레퍼런스"로만 등장 가능 (예: "코르티스의 단일 타이틀 락인과 달리 X는~").
- 각 ##6 항목의 1단계 [관찰]은 ⭐선행 또는 오늘 진짜 새 변화가 있는 신호에서 출발.
- ##6 자가참조 체크: 어제·그제 ##6에서 이미 다룬 같은 아티스트의 같은 각도를 반복하고 있지 않은가? 반복이면 제외.
- ##6 뉴스레터 후보 태그 (필수, 누락 금지): 각 ##6 항목 제목 끝에 `[📰 적용구체|사운드|권역]` 형식으로 태그 부착.
  · 적용구체 = K-pop 적용 가설이 즉시 실행 가능 (인사이트 3단계 [번역]에 구체 디렉션이 있는가)
  · 사운드 = 사운드 디테일·레퍼런스가 풍부 (BPM·구조·장르 계보·프로듀서 등 구체 묘사가 있는가)
  · 권역 = 여러 아티스트가 묶인 트렌드/메타 신호 (단일 아티스트 ≠ 권역)
  · 복수 해당 가능, 셋 다 해당하면 셋 다 표시. 해당 없으면 항목 자체를 ##6에 넣지 말 것 — ##6은 정의상 위 셋 중 하나는 충족해야 함.
  · 작성 절차: 각 ##6 항목의 [관찰]·[왜]·[번역] 3단을 다 쓴 직후, 마지막 단계로 제목 끝에 태그 부착.
➿누적 항목은 confidence가 🔴여도 '신규/첫 발견' 톤 금지 + ##1 톱·##6 인사이트 메인 소재 금지(##3 신예 레이더·##5·##8 또는 ##6 대비 레퍼런스로는 가능).

1단계: [구체적 관찰]
   예: "AKMU '소문의 낙원'(차분한 어쿠스틱)과 '기쁨, 슬픔'(업템포)이
       Apple KR #1과 #4에 동시 진입"

2단계: [왜 먹혔는가 — 대비·차이의 구체적 이유]
   예: "두 곡이 완전히 반대 무드라서 팬이 하나를 좋아해도 다른 곡이
       겹치지 않음. 플레이리스트 분화가 가능한 구조"

3단계: [K-pop 앨범 디렉션에 어떻게 번역되는가 — 구체적 실행 가설]
   예: "걸그룹 정규/미니 앨범 타이틀+수록곡 구성 시, 두 곡의 무드 축
       (comfort/intensity)을 반대편에 두세요. 한 무드 락인 회피.
       보이그룹 타이틀로는 이 방향 무리 — 최근 보이그룹 타이틀은
       퍼포먼스 드리븐 중심."

## 6 금지:
- "팬층의 다양한 선호도를 커버" 같은 추상적 결론
- "~이 중요함" / "~을 고려해야 함" 같은 원론
- 오늘 신호가 아닌 일반론
- "신인 아티스트 계약 시" 같은 서양식 A&R 언어

## 6 체크: K-pop A&R이 "이 각도는 오늘 처음 봤다, 우리 컴백 기획에
바로 적용해볼 수 있겠다" 수준이면 통과.

## 7. 워치리스트 업데이트

## 7 작성 규칙:
- 입력된 "주목 중 아티스트 오늘 차트 진입" 블록의 🔥(NEW) 아티스트 상단 강조
- 각 아티스트에 "오늘의 변화 한 줄" (어느 차트에 새로 들어갔는지 구체적으로)
- 진입 없는 날은 "주목 중 아티스트 오늘 차트 변화 없음" 한 줄로 끝
- A&R이 3초 안에 "오늘 누구 보면 되는가" 파악 가능해야 함
- "~ 모니터링 지속" 같은 원론 금지. 오늘의 구체 이벤트만

예:
🔥 Nkosazana Daughter — YouTube Rising NEW (RIP MAMA 108K/day)
🔥 Momo Boyd — Apple US NEW (Oops, #42)
- PinkPantheress — Kworb WW 재진입 (#18)

신규 추가 (절대 위반 금지):
- 입력 "워치리스트 변화" 블록에 "✅ 신규: [confidence] artist_name" 형식으로 명시된 아티스트만.
- 출력 형식: "- [confidence · ⭐선행] artist_name — 변화 한 줄". 신규는 history 1~2회이므로 discovery 축은 항상 ⭐선행.
- "✅ 누적: ..." 형식으로 표시된 아티스트는 신규 추가 섹션 진입 자체 금지.
  → "누적 N회"라는 라벨을 달아도 안 됨. 라벨링이 회피 경로가 되지 않도록.
  → 누적 아티스트를 강조하고 싶으면 아래 "진행 중 신호" 섹션 사용.
- 매체 정보만 보고 LLM 판단으로 추가 금지. 입력 데이터에 없으면 ##7 신규 추가 섹션에 절대 포함 안 됨.
- 워치리스트 변화 블록에 신규 0건이면 "오늘 신규 추가 없음 — 기존 추적 중" 한 줄로 끝.

진행 중 신호 (누적 아티스트 강조 자리 — 선택 섹션):
- "✅ 누적: ..." 항목 중 오늘 새 신호가 의미 있는 변화일 때만 등장.
- 형식: "🔁 [confidence · ➿누적 N회] artist_name — 오늘의 변화 한 줄". 진행 중은 history 3회+이므로 discovery 축은 항상 ➿누적.
- 어제 리포트에 같은 아티스트가 같은 섹션에 같은 톤으로 등장했다면 오늘은 제외.
- 변화 없으면 이 섹션 자체를 생략. 의무 섹션 아님.

예 (정상 — 신규 추가):
🔥 신규 추가:
- [🟢 · ⭐선행] Fleetwood Mac — 클래식 록 재조명으로 4개 플랫폼 NEW 진입

예 (정상 — 진행 중 신호):
🔁 [🔴 · ➿누적 3회] EsDeeKid — Billboard emerging 선정 추가 검증

예 (위반 — 절대 쓰지 말 것):
❌ 신규 추가에: "- [🟡 · ➿누적 2회] EsDeeKid" — 신규 섹션에 ➿누적 라벨이 박히는 것 자체가 모순. 누적은 진행 중 신호로.
❌ 신규 추가에: "- DellaXOZ — Rolling Stone 선정" — 입력 워치리스트 변화에 없으면 금지

## 8. 지속 모니터링 (반복 항목 한 줄 요약)

## 8 작성 규칙 (이름 나열 금지 — 반복 체감의 주범):
- 이 섹션은 "이미 충분히 보도된 누적·캐시 항목"의 처리 자리다. 목적은 호명이 아니라 "오늘 변화 없음"을 압축 통보하는 것.
- 누적 3회+ / 캐시 적용 메이저는 개별 이름을 나열하지 말 것. "메이저 차트 유지 N팀, 변동 없음"처럼 카운트로 접는다.
- 개별 이름을 쓸 수 있는 경우는 단 하나: 어제 대비 의미 있는 변화(순위 급등·급락, 차트 이탈, 새 이벤트)가 있을 때. 변화 있는 항목만 "이름 — 변화" 한 줄.
- 어제 § 8에 이미 같은 디테일로 나온 항목(예: "CORTIS #2 유지")을 오늘 또 같은 디테일로 쓰지 말 것. "유지"는 변화가 아니다 → 카운트로만.
- History Agent의 duplicates_to_suppress 항목은 이름만 쉼표로 묶어 "반복 억제: A, B, C" 한 줄.
- 이 섹션 전체는 최대 2~3줄. 길어지면 § 8이 본문을 잡아먹어 신선도를 가린다.

## 9. 메이저 매체 교차 확인
   - "## '우리가 먼저' 검증 히스토리" 블록의 내용만 사용.
   - Discovery signals의 media_status 필드는 ## 9에서 무시.
   - 검증 히스토리가 비어있으면 "이번 주기에 검증된 교차 확인 없음"으로.
   - 날짜 차이가 음수(매체가 먼저)인 항목을 "우리가 먼저"로 재해석 금지.

확신도: 🔴 확실 / 🟡 주시 / 🟢 초기신호
한국어 작성, 고유명사/장르명 영어 유지."""


def run_analysis():
    print("📊 Analysis Agent 시작\n")

    snapshots = load_latest_snapshot(2)
    if not snapshots:
        print("  ❌ 스냅샷 없음")
        return

    today_snapshot = snapshots[0]
    discovery_results = load_discovery_results()
    cache = load_artist_cache()
    watchlist = load_watchlist()
    history = load_history()
    yesterday_report = load_latest_report()

    artists_on_chart = get_artists_on_chart(today_snapshot)
    update_cache_last_seen(cache, artists_on_chart)

    snapshot_text = prepare_snapshot_summary(snapshots, cache)
    discovery_text = prepare_discovery_summary(discovery_results)
    history_text = prepare_history_summary(history)
    watchlist_changes_text = prepare_watchlist_today_changes(watchlist, today_snapshot)

    # 워치리스트: last_signal_at 내림차순 정렬 → 신선도 라벨링
    today = datetime.now().date()

    def _freshness_label(last_signal_iso: str) -> str:
        if not last_signal_iso:
            return "🕸️ stale"
        try:
            d = datetime.fromisoformat(last_signal_iso).date()
        except Exception:
            return "🕸️ stale"
        delta = (today - d).days
        if delta <= 0:
            return "🆕 오늘 신호"
        if delta <= 6:
            return f"🔁 {delta}일 전 신호"
        return f"🕸️ {delta}일째 stale"

    def _discovery_label(n: int) -> str:
        # 발굴 우위 축 — confidence(검증 강도)와 직교. 코드가 결정, LLM 판단 배제.
        # ⭐ 선행 = 신선한 발굴(1~2회) / ➿ 누적 = 신선도 소진(3회+, ##1톱·##6메인 금지 대상)
        return "⭐ 선행" if n <= 2 else f"➿ 누적 {n}회"

    def _format_watch_artist(a: dict) -> str:
        hist = a.get("history", [])
        n = len(hist)
        first_sig = hist[0]["signal"][:60] if hist else ""
        last_sig = hist[-1]["signal"][:80] if hist else ""
        fresh = _freshness_label(a.get("last_signal_at", ""))
        disc = _discovery_label(n)
        # 1회 신호면 첫=마지막 → 마지막만 출력
        if n <= 1:
            return f"  [{a['confidence']}] {disc} | {a['value']} | {fresh} | {last_sig}"
        return (
            f"  [{a['confidence']}] {disc} | {a['value']} (총 {n}회 신호) | {fresh}\n"
            f"      └ 첫 신호: {first_sig}\n"
            f"      └ 마지막 신호: {last_sig}"
        )

    artists = [a for a in watchlist.get("artists", []) if isinstance(a, dict)]
    artists.sort(key=lambda a: a.get("last_signal_at", ""), reverse=True)

    watchlist_context = "\n## 현재 워치리스트 (최근 신호순 상위 12명):\n"
    watchlist_context += (
        "_라벨 2축 (직교): [confidence]=검증 강도(🔴확실/🟡주시/🟢초기) / [discovery]=발굴 우위(⭐선행=신선한 발굴 / ➿누적=신선도 소진).\n"
        "  두 축은 독립이다. 🔴➿(검증됐지만 소진) ≠ 🟢⭐(초기지만 우리가 먼저)를 혼동 금지.\n"
        "  ➿누적 항목은 confidence가 🔴여도 '신규/첫 발견' 톤 금지 + ##1 톱·##6 인사이트 메인 소재 금지(##3 신예 레이더·##5·##8 또는 ##6 대비 레퍼런스로는 가능)._\n"
    )
    for a in artists[:12]:
        watchlist_context += _format_watch_artist(a) + "\n"

    genres = [g for g in watchlist.get("genres", []) if isinstance(g, dict)]
    genres.sort(key=lambda g: g.get("last_signal_at", ""), reverse=True)
    if genres:
        watchlist_context += "\n## 현재 워치리스트 (장르 상위 5개):\n"
        for g in genres[:5]:
            watchlist_context += f"  [{g['confidence']}] 장르: {g['value']} | {_freshness_label(g.get('last_signal_at', ''))}\n"
           
    yesterday_context = ""
    if yesterday_report:
        yesterday_context = f"\n## 어제 리포트 (참고):\n{yesterday_report[:1200]}"

    user_message = f"""Discovery Agent 결과를 종합해 A&R 뉴스레터를 작성하세요.
Discovery 신호를 핵심으로. 캐시 있는 아티스트는 캐시만. search_web 금지.

{snapshot_text}
{discovery_text}
{history_text}
{watchlist_changes_text}
{watchlist_context}
{yesterday_context}"""

    messages = [{"role": "user", "content": user_message}]
    max_iter = 8
    iteration = 0
    report_generated = False

    while iteration < max_iter and not report_generated:
        iteration += 1
        print(f"  [Analysis 턴 {iteration}]")

        # 마지막 턴에서는 tool_choice로 generate_report 강제
        turns_left = max_iter - iteration
        extra_kwargs = {}
        if turns_left <= 2 and not report_generated:
            extra_kwargs["tool_choice"] = {"type": "tool", "name": "generate_report"}
            print(f"  🎯 남은 턴 {turns_left}회 — generate_report 강제 호출")

        response = create_message_with_retry(
            client,
            model="claude-opus-4-8",
            max_tokens=8000,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"}
                }
            ],
            tools=TOOLS,
            messages=messages,
            **extra_kwargs,
        )
        
        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if hasattr(block, "text") and block.text:
                print(f"  💭 {block.text[:150]}...")

        if response.stop_reason == "end_turn":
            print(f"  ⚠️ Claude가 tool 호출 없이 end_turn — 리포트 미생성 가능")
            break
        if response.stop_reason == "max_tokens":
            print(f"  ⚠️ max_tokens({response.usage.output_tokens}) 도달 — 출력 잘림")
            break
        if response.stop_reason != "tool_use":
            print(f"  ⚠️ 예상치 못한 stop_reason: {response.stop_reason}")
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = execute_tool(block.name, block.input, cache)
                print(f"  🔧 {block.name}: {result[:80]}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
                if block.name == "generate_report":
                    report_generated = True

        messages.append({"role": "user", "content": tool_results})
        time.sleep(0.3)

    print(f"\n  ✅ Analysis 완료 | {iteration}턴 | 캐시: {len(cache)}명")


if __name__ == "__main__":
    run_analysis()
