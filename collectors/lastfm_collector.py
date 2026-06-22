import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('LASTFM_API_KEY')
BASE_URL = 'http://ws.audioscrobbler.com/2.0/'

# 라이징 감지용 — 추적할 장르 태그 목록
RISING_TAGS = [
    'afrobeats', 'amapiano', 'pluggnb', 'hyperpop',
    'jersey club', 'brazilian funk', 'uk garage',
    'drill', 'phonk', 'bedroom pop', 'alt r&b',
]


def collect_lastfm_global(limit=50):
    """Last.fm 글로벌 인기 트랙"""
    resp = requests.get(BASE_URL, params={
        'method': 'chart.getTopTracks',
        'api_key': API_KEY,
        'format': 'json',
        'limit': limit
    })
    data = resp.json()

    tracks = []
    for rank, track in enumerate(data['tracks']['track'], 1):
        tracks.append({
            'rank': rank,
            'title': track['name'],
            'artist': track['artist']['name'],
            'listeners': int(track['listeners']),
            'playcount': int(track['playcount']),
        })
    return tracks


def collect_lastfm_country(country='Korea, Republic of', limit=50):
    """Last.fm 국가별 인기 트랙"""
    resp = requests.get(BASE_URL, params={
        'method': 'geo.getTopTracks',
        'country': country,
        'api_key': API_KEY,
        'format': 'json',
        'limit': limit
    })
    data = resp.json()

    if 'tracks' not in data:
        return []

    tracks = []
    for rank, track in enumerate(data['tracks']['track'], 1):
        tracks.append({
            'rank': rank,
            'title': track['name'],
            'artist': track['artist']['name'],
            'listeners': int(track['listeners']),
        })
    return tracks


def collect_lastfm_genre_tags(tags=None, limit=10):
    """
    장르 태그별 인기 트랙 + 태그 총 청취자 수 — 라이징 장르 감지용.

    반환 구조:
    {
        'amapiano': {
            'total_listeners': 1234567,
            'top_tracks': [
                {'rank': 1, 'name': '...', 'artist': '...'},
                ...
            ]
        },
        ...
    }
    """
    if tags is None:
        tags = RISING_TAGS

    results = {}
    for tag in tags:
        try:
            # 1. 태그 인기 트랙
            tracks_resp = requests.get(BASE_URL, params={
                'method': 'tag.getTopTracks',
                'tag': tag,
                'api_key': API_KEY,
                'format': 'json',
                'limit': limit
            })
            tracks_data = tracks_resp.json()

            top_tracks = []
            if 'tracks' in tracks_data and 'track' in tracks_data['tracks']:
                for rank, track in enumerate(tracks_data['tracks']['track'], 1):
                    top_tracks.append({
                        'rank': rank,
                        'name': track['name'],
                        'artist': track['artist']['name'],
                    })

            # 2. 총 트랙수
            total_tracks = int(tracks_data.get('tracks', {}).get('@attr', {}).get('total', 0))

                
            results[tag] = {
                'total_listeners': total_tracks,  # 필드명 유지 (generate_report.py와 호환)
                'top_tracks': top_tracks,
            }
            print(f"  Last.fm tag '{tag}': {len(top_tracks)} tracks | {total_tracks:,} total")

        except Exception as e:
            print(f"  Last.fm tag '{tag}': FAILED - {e}")
            results[tag] = {'total_listeners': 0, 'top_tracks': []}

    return results

if __name__ == '__main__':
    print("Global Top 5:")
    for t in collect_lastfm_global(5):
        print(f"  {t['rank']}. {t['title']} - {t['artist']}")

    print("\nKorea Top 5:")
    for t in collect_lastfm_country(limit=5):
        print(f"  {t['rank']}. {t['title']} - {t['artist']}")

    print("\nGenre Tags:")
    tags = collect_lastfm_genre_tags(limit=3)
    for genre, data in tags.items():
        print(f"  #{genre}: {data['total_listeners']:,} reach")
        for tr in data['top_tracks']:
            print(f"    {tr['rank']}. {tr['name']} - {tr['artist']}")
            