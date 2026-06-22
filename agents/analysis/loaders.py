"""스냅샷·캐시·워치리스트·히스토리·리포트 파일 I/O 및 차트 아티스트 집합 추출."""
import json
from datetime import datetime

from .config import (
    DATA_DIR, REPORTS_DIR, DISCOVERY_DIR,
    WATCHLIST_PATH, ARTIST_CACHE_PATH, HISTORY_PATH,
)


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
