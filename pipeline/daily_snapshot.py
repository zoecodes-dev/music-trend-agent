"""
Music Trend Agent - Daily Snapshot
매일 실행해서 차트 데이터를 날짜별 JSON으로 저장.
어제 스냅샷이 있으면 velocity (순위 변화) 자동 계산.
"""

import json
import os
from datetime import datetime, timedelta
import sys
from pathlib import Path

# 패키지 하위에서 직접 실행해도 루트 기준 import·데이터 경로가 동작하도록
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.youtube_collector import collect_youtube_trending
from collectors.youtube_rising_collector import collect_youtube_rising
from collectors.apple_collector import collect_apple_music_multi
from collectors.melon_collector import collect_melon_multi, detect_new_entries
from collectors.deezer_collector import collect_deezer_chart
from collectors.lastfm_collector import collect_lastfm_global, collect_lastfm_country, collect_lastfm_genre_tags
from collectors.kpop_comeback_collector import collect_kpop_comeback_intel
from collections import Counter
from collectors.media_coverage_collector import collect_media_coverage
from collectors.emerging_artist_collector import collect_emerging_artist_intel
from collectors.kworb_collector import collect_kworb_apple_worldwide
from collectors.reddit_collector import collect_reddit_signals
from anthropic import Anthropic
from scripts.comeback_queue_manager import enqueue_new_items, pick_today_exposures, load_queue

DATA_DIR = ROOT / "snapshots"

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def get_snapshot_path(date_str):
    return os.path.join(DATA_DIR, f"snapshot_{date_str}.json")


def load_snapshot(date_str):
    """특정 날짜의 스냅샷 로드"""
    path = get_snapshot_path(date_str)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def calculate_velocity(today_tracks, yesterday_tracks, key_field):
    """
    어제 대비 순위 변화 계산.
    key_field: 곡을 식별하는 필드 (title+artist 조합)
    """
    if not yesterday_tracks:
        return today_tracks

    yesterday_map = {}
    for t in yesterday_tracks:
        key = f"{t.get('title', t.get('name', ''))}|{t.get('artist', t.get('channel', ''))}"
        yesterday_map[key] = t["rank"]

    for t in today_tracks:
        key = f"{t.get('title', t.get('name', ''))}|{t.get('artist', t.get('channel', ''))}"
        if key in yesterday_map:
            t["yesterday_rank"] = yesterday_map[key]
            t["velocity"] = yesterday_map[key] - t["rank"]  # 양수 = 상승
        else:
            t["yesterday_rank"] = None
            t["velocity"] = "NEW"

    return today_tracks


def calculate_rising_velocity(today_rising, yesterday_rising):
    """
    rising 데이터의 views_per_day 변화량 계산.
    차트 순위가 없으므로 video_id 기준으로 views_per_day 증감만 추적.
    """
    if not yesterday_rising:
        return today_rising

    yesterday_map = {v["video_id"]: v["views_per_day"] for v in yesterday_rising}

    for v in today_rising:
        vid = v["video_id"]
        if vid in yesterday_map:
            v["vpd_delta"] = v["views_per_day"] - yesterday_map[vid]  # 양수 = 가속
        else:
            v["vpd_delta"] = None  # 어제 없었던 신규 감지

    return today_rising

def calculate_playcount_velocity(today_tracks, yesterday_tracks):
    """
    Last.fm playcount 증감 계산.
    어제 대비 playcount 변화량 → 스트리밍 모멘텀 추적.
    """
    if not yesterday_tracks:
        return today_tracks

    yesterday_map = {
        f"{t['title']}|{t['artist']}": t.get('playcount', 0)
        for t in yesterday_tracks
    }

    for t in today_tracks:
        key = f"{t['title']}|{t['artist']}"
        if key in yesterday_map:
            t['playcount_delta'] = t.get('playcount', 0) - yesterday_map[key]
        else:
            t['playcount_delta'] = None  # 신규 진입

    return today_tracks

def collect_all():
    """모든 소스에서 데이터 수집"""
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"=== Music Trend Snapshot: {today} ===\n")

    yesterday_data = load_snapshot(yesterday)

    snapshot = {
        "date": today,
        "collected_at": datetime.now().isoformat(),
        "sources": {},
    }

    # 1. YouTube Trending (한국)
    print("[1/12] YouTube Trending KR...")
    try:
        yt_tracks = collect_youtube_trending("KR", 50)
        yt_yesterday = (
            yesterday_data["sources"].get("youtube_kr", []) if yesterday_data else []
        )
        yt_tracks = calculate_velocity(yt_tracks, yt_yesterday, "title")
        snapshot["sources"]["youtube_kr"] = yt_tracks
        print(f"  ✅ {len(yt_tracks)} tracks collected")
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        snapshot["sources"]["youtube_kr"] = []

    # 2. YouTube Rising (글로벌 라이징 감지)
    print("[2/12] YouTube Rising (global)...")
    try:
        rising_tracks = collect_youtube_rising()
        rising_yesterday = (
            yesterday_data["sources"].get("youtube_rising", []) if yesterday_data else []
        )
        rising_tracks = calculate_rising_velocity(rising_tracks, rising_yesterday)
        snapshot["sources"]["youtube_rising"] = rising_tracks
        print(f"  ✅ {len(rising_tracks)} rising tracks detected")
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        snapshot["sources"]["youtube_rising"] = []

    # 3. Apple Music (한국, 미국, 일본, 영국, 글로벌)
    print("[3/12] Apple Music Chart KR/US/JP...")
    try:
        apple_results = collect_apple_music_multi(countries=["kr", "us", "jp", "gb"], limit=50)
        for country in ["kr", "us", "jp", "gb"]:
            tracks = apple_results.get(country, [])
            yesterday_apple = (
                yesterday_data["sources"].get(f"apple_{country}", []) if yesterday_data else []
            )
            tracks = calculate_velocity(tracks, yesterday_apple, "title")
            snapshot["sources"][f"apple_{country}"] = tracks
        print(f"  ✅ KR {len(apple_results.get('kr', []))} / US {len(apple_results.get('us', []))} / JP {len(apple_results.get('jp', []))} / GB {len(apple_results.get('gb', []))} tracks collected")
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        for country in ["kr", "us", "jp", "gb"]:
            if f"apple_{country}" not in snapshot["sources"]:
                snapshot["sources"][f"apple_{country}"] = []

    # 3.5 Melon (한국 로컬 차트 — Apple KR이 놓치는 국민/인디/발라드 신호)
    print("[3.5/12] Melon Chart KR (top100 + hot100)...")
    try:
        melon_results = collect_melon_multi(charts=["top100", "hot100"], limit=100)

        # 어제 song_id와 비교해 신규 진입 자체 계산 (멜론 마크업엔 new 없음)
        yesterday_melon = (
            yesterday_data["sources"].get("melon", {}) if yesterday_data else {}
        )
        for chart_name in ["top100", "hot100"]:
            today_tracks = melon_results.get(chart_name, [])
            y_ids = [t.get("song_id") for t in yesterday_melon.get(chart_name, [])]
            new_entries = detect_new_entries(today_tracks, y_ids)
            for t in today_tracks:
                t["is_new"] = t.get("song_id") in set(
                    te.get("song_id") for te in new_entries
                )

        snapshot["sources"]["melon"] = {
            "top100": melon_results.get("top100", []),
            "hot100": melon_results.get("hot100", []),
        }
        new_t100 = sum(1 for t in melon_results.get("top100", []) if t.get("is_new"))
        new_h100 = sum(1 for t in melon_results.get("hot100", []) if t.get("is_new"))
        print(f"  ✅ TOP100 {len(melon_results.get('top100', []))} (신규 {new_t100}) / HOT100 {len(melon_results.get('hot100', []))} (신규 {new_h100})")
    except Exception as e:
        import traceback
        print(f"  ❌ Failed: {e}")
        traceback.print_exc()
        snapshot["sources"]["melon"] = {"top100": [], "hot100": []}

    # 4. Deezer (글로벌)
    print("[4/12] Deezer Global Chart...")
    try:
        deezer_tracks = collect_deezer_chart(50)
        deezer_yesterday = (
            yesterday_data["sources"].get("deezer_global", []) if yesterday_data else []
        )
        deezer_tracks = calculate_velocity(deezer_tracks, deezer_yesterday, "title")
        snapshot["sources"]["deezer_global"] = deezer_tracks

        artist_counts = Counter(t['artist'] for t in deezer_tracks[:10])
        dominant = [(a, c) for a, c in artist_counts.items() if c >= 5]
        if dominant:
            print(f"  ⚠️ Deezer 이상 감지: {dominant} — 차트 신뢰도 낮음")
            snapshot["sources"]["deezer_anomaly"] = True
        else:
            snapshot["sources"]["deezer_anomaly"] = False

        print(f"  ✅ {len(deezer_tracks)} tracks collected")
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        snapshot["sources"]["deezer_global"] = []
        snapshot["sources"]["deezer_anomaly"] = False

    # 5. Last.fm Global
    print("[5/12] Last.fm Global Chart...")
    try:
        lastfm_global = collect_lastfm_global(50)
        lastfm_yesterday = (
            yesterday_data["sources"].get("lastfm_global", []) if yesterday_data else []
        )
        lastfm_global = calculate_playcount_velocity(lastfm_global, lastfm_yesterday)
        snapshot["sources"]["lastfm_global"] = lastfm_global
        print(f"  ✅ {len(lastfm_global)} tracks collected")
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        snapshot["sources"]["lastfm_global"] = []

    # 6. last.fm UK 차트 강화
    print("[6/12] Last.fm US/UK Chart...")
    try:
        lastfm_us = collect_lastfm_country(country='United States', limit=50)
        lastfm_uk = collect_lastfm_country(country='United Kingdom', limit=50)
        
        for country_key, tracks in [("lastfm_us", lastfm_us), ("lastfm_uk", lastfm_uk)]:
            yesterday_tracks = yesterday_data["sources"].get(country_key, []) if yesterday_data else []
            tracks = calculate_velocity(tracks, yesterday_tracks, "title")
            snapshot["sources"][country_key] = tracks
        
        print(f"  ✅ US {len(lastfm_us)} / UK {len(lastfm_uk)} tracks collected")
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        snapshot["sources"]["lastfm_us"] = []
        snapshot["sources"]["lastfm_uk"] = []
    

    # 7. Last.fm Genre Tags (라이징 장르 감지)
# 7. Last.fm Genre Tags (라이징 장르 모멘텀 추적)
    print("[7/12] Last.fm Genre Tags...")
    try:
        genre_tags = collect_lastfm_genre_tags(limit=10)
        yesterday_genre_tags = (
            yesterday_data["sources"].get("lastfm_genre_tags", {}) if yesterday_data else {}
        )
        for tag, data in genre_tags.items():
            yesterday_listeners = yesterday_genre_tags.get(tag, {}).get("total_listeners", None)
            if yesterday_listeners and yesterday_listeners > 0:
                delta = data["total_listeners"] - yesterday_listeners
                data["listeners_delta"] = delta
                data["listeners_delta_pct"] = round(delta / yesterday_listeners * 100, 1)
            else:
                data["listeners_delta"] = None
                data["listeners_delta_pct"] = None
        snapshot["sources"]["lastfm_genre_tags"] = genre_tags
        print(f"  ✅ {len(genre_tags)} genres tracked")
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        snapshot["sources"]["lastfm_genre_tags"] = {}

     # 8. k word 스크래핑    
    print("[8/12] Kworb Apple Music Worldwide...")
    try:
        kworb_tracks = collect_kworb_apple_worldwide(limit=50)
        kworb_yesterday = (
            yesterday_data["sources"].get("kworb_apple_ww", []) if yesterday_data else []
        )
        kworb_tracks = calculate_velocity(kworb_tracks, kworb_yesterday, "title")
        snapshot["sources"]["kworb_apple_ww"] = kworb_tracks
        print(f"  ✅ {len(kworb_tracks)} tracks collected")
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        snapshot["sources"]["kworb_apple_ww"] = []
            
    # 9. K-pop 컴백 소식
    print("[9/12] K-pop Comeback Intel...")
    try:
        comeback_data = collect_kpop_comeback_intel()
        today_str = snapshot["date"]
        enqueue_new_items(comeback_data["schedule"], today_str)
        today_exposures = pick_today_exposures(today_str, n=4)

        snapshot["sources"]["kpop_comeback"] = {
            "today_exposures": today_exposures,
            "queue_size": len(load_queue()["items"]),
            "raw_schedule": comeback_data["schedule"],
            "tavily_calls_used": comeback_data["tavily_calls_used"],
        }
        print(f"  ✅ 완료 (Tavily {comeback_data['tavily_calls_used']}회, 큐: {len(load_queue()['items'])}개, 오늘 노출: {len(today_exposures)}개)")
    except Exception as e:
        import traceback
        print(f"  ❌ Failed: {e}")
        traceback.print_exc()
        snapshot["sources"]["kpop_comeback"] = {}
    
    # 10. 미디어 데이터와 교차 검증
    print("[10/12] Media Coverage (Billboard/Pitchfork/Rolling Stone)...")
    try:
        # 오늘 수집된 아티스트 목록 추출 (교차 검증용)
        watch = set()
        for v in snapshot["sources"].get("youtube_rising", []):
            watch.add(v["channel"])
        for t in snapshot["sources"].get("apple_kr", [])[:20]:
            watch.add(t["artist"])
        for t in snapshot["sources"].get("lastfm_global", [])[:20]:
            watch.add(t["artist"])

        media_data = collect_media_coverage(watch_artists=list(watch))
        snapshot["sources"]["media_coverage"] = media_data
        print(f"  ✅ 완료 (Tavily {media_data['tavily_calls_used']}회)")
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        snapshot["sources"]["media_coverage"] = {}

    # 11. 신예 감지
    print("[11/12] Emerging Artist Intel...")
    try:
        # 피처링 감지용 차트 데이터 준비
        chart_for_feat = (
            snapshot["sources"].get("apple_us", [])[:30] +
            snapshot["sources"].get("lastfm_global", [])[:20]
        )
        emerging_data = collect_emerging_artist_intel(
            chart_tracks=chart_for_feat,
            max_feat_tracks=3,
        )
        snapshot["sources"]["emerging_artist"] = emerging_data
        print(f"  ✅ 완료 (Tavily {emerging_data['tavily_calls_used']}회)")
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        snapshot["sources"]["emerging_artist"] = {}
        
    # 12. Reddit Signals (popheads/kpop/indieheads × rising/hot)
    print("[12/12] Reddit Signals...")
    try:
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        reddit_signals = collect_reddit_signals(client=client)
        snapshot["sources"]["reddit_signals"] = reddit_signals
        print(f"  ✅ {len(reddit_signals)} signals collected")
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        snapshot["sources"]["reddit_signals"] = []


    # 저장
    ensure_data_dir()
    path = get_snapshot_path(today)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Snapshot saved: {path}")

    print_summary(snapshot)
    return snapshot


def print_summary(snapshot):
    """수집 결과 요약 출력"""
    print("\n" + "=" * 50)
    print("📊 TODAY'S HIGHLIGHTS")
    print("=" * 50)

    # Apple Music 한국 Top 5
    apple = snapshot["sources"].get("apple_kr", [])
    if apple:
        print("\n🍎 Apple Music KR Top 5:")
        for t in apple[:5]:
            vel = ""
            if t.get("velocity") == "NEW":
                vel = " 🆕"
            elif isinstance(t.get("velocity"), int) and t["velocity"] > 0:
                vel = f" ⬆️+{t['velocity']}"
            elif isinstance(t.get("velocity"), int) and t["velocity"] < 0:
                vel = f" ⬇️{t['velocity']}"
            print(f"  {t['rank']}. {t['title']} - {t['artist']}{vel}")

    # Melon 한국 — Apple KR과 다른 신호 강조
    melon = snapshot["sources"].get("melon", {})
    melon_top = melon.get("top100", []) if isinstance(melon, dict) else []
    if melon_top:
        apple_artists = {t["artist"] for t in apple[:50]} if apple else set()
        print("\n🍈 Melon TOP100 — Apple KR에 없는 항목 (한국 로컬 신호):")
        local_only = 0
        for t in melon_top[:30]:
            # 아티스트명 정규화 비교 (멜론은 '아티스트 (한글)' 형식)
            base_artist = t["artist"].split("(")[0].strip()
            in_apple = any(base_artist in a or a in t["artist"] for a in apple_artists)
            if not in_apple:
                new_mark = " 🆕" if t.get("is_new") else ""
                print(f"  {t['rank']}. {t['title']} - {t['artist']}{new_mark}")
                local_only += 1
                if local_only >= 8:
                    break
        if local_only == 0:
            print("  (Apple KR과 완전 일치 — 차별 신호 없음)")

    # YouTube 한국 Top 5
    yt = snapshot["sources"].get("youtube_kr", [])
    if yt:
        print("\n📺 YouTube Trending KR Top 5:")
        for t in yt[:5]:
            vel = ""
            if t.get("velocity") == "NEW":
                vel = " 🆕"
            elif isinstance(t.get("velocity"), int) and t["velocity"] > 0:
                vel = f" ⬆️+{t['velocity']}"
            elif isinstance(t.get("velocity"), int) and t["velocity"] < 0:
                vel = f" ⬇️{t['velocity']}"
            print(f"  {t['rank']}. {t['title']} - {t['channel']}{vel}")
            print(f"         Views: {t['views']:,}")

    # YouTube Rising Top 5
    rising = snapshot["sources"].get("youtube_rising", [])
    if rising:
        print("\n🚀 YouTube Rising Top 5 (views/day 기준):")
        for v in rising[:5]:
            accel = ""
            if v.get("vpd_delta") is not None:
                accel = f" (어제 대비 {'⬆️+' if v['vpd_delta'] >= 0 else '⬇️'}{v['vpd_delta']:,}/day)"
            elif v.get("vpd_delta") is None:
                accel = " 🆕"
            print(f"  {v['title']} — {v['channel']}")
            print(f"         {v['views_per_day']:,}/day | 구독자 {v['subscriber_count']:,}{accel}")

    # Last.fm 장르 태그 모멘텀
    genre_tags = snapshot["sources"].get("lastfm_genre_tags", {})
    if genre_tags:
        print("\n🎵 Last.fm 장르 모멘텀 (delta 기준):")
        sortable = [
            (tag, data) for tag, data in genre_tags.items()
            if data.get("listeners_delta_pct") is not None
        ]
        no_delta = [
            (tag, data) for tag, data in genre_tags.items()
            if data.get("listeners_delta_pct") is None
        ]
        sorted_tags = sorted(sortable, key=lambda x: x[1]["listeners_delta_pct"], reverse=True)
        for genre, data in (sorted_tags + no_delta)[:5]:
            listeners = data.get("total_listeners", 0)
            pct = data.get("listeners_delta_pct")
            pct_str = f" ({'+' if pct >= 0 else ''}{pct}%)" if pct is not None else " (첫 수집)"
            top_track = data.get("top_tracks", [{}])[0] if data.get("top_tracks") else {}
            print(f"  #{genre}: {listeners:,} total{pct_str} — {top_track.get('name', '')} / {top_track.get('artist', '')}")           
 
    # NEW entries 감지 (차트 소스)
    print("\n🔥 NEW ENTRIES (어제 없다가 오늘 등장):")
    new_count = 0
    for source_name in ["apple_kr", "youtube_kr", "deezer_global", "lastfm_global"]:
        tracks = snapshot["sources"].get(source_name, [])
        for t in tracks:
            if t.get("velocity") == "NEW" and t["rank"] <= 20:
                name = t.get("title", "")
                artist = t.get("artist", t.get("channel", ""))
                print(f"  [{source_name}] #{t['rank']} {name} - {artist}")
                new_count += 1
    if new_count == 0:
        print("  (신규 진입 없음)")


if __name__ == "__main__":
    collect_all()