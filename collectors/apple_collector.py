import requests


def collect_apple_music_chart(country="kr", limit=100):
    """Apple Music Top Songs 차트 수집 (인증 불필요)"""
    url = f"https://rss.applemarketingtools.com/api/v2/{country}/music/most-played/{limit}/songs.json"
    response = requests.get(url)
    data = response.json()

    tracks = []
    for rank, song in enumerate(data["feed"]["results"], 1):
        tracks.append(
            {
                "rank": rank,
                "title": song["name"],
                "artist": song["artistName"],
                "album": song.get("collectionName", ""),
                "apple_id": song["id"],
                "url": song["url"],
                "release_date": song.get("releaseDate", ""),
                "genre": song.get("genres", [{}])[0].get("name", "") if song.get("genres") else "",
            }
        )

    return tracks


def collect_apple_music_multi(countries=None, limit=100):
    """여러 국가 차트 한번에 수집"""
    if countries is None:
        countries = ["kr", "us", "gb", "jp"]

    results = {}
    for country in countries:
        try:
            results[country] = collect_apple_music_chart(country, limit)
            print(f"  Apple Music {country.upper()}: {len(results[country])} tracks")
        except Exception as e:
            print(f"  Apple Music {country.upper()}: FAILED - {e}")
            results[country] = []

    return results


if __name__ == "__main__":
    tracks = collect_apple_music_chart("kr")
    for t in tracks[:5]:
        print(f"{t['rank']}. {t['title']} - {t['artist']}")
