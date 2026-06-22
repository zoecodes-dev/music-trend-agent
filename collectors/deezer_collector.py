import requests


def collect_deezer_chart(limit=50):
    """Deezer 글로벌 차트 수집 (인증 불필요)"""
    url = f"https://api.deezer.com/chart/0/tracks?limit={limit}"
    response = requests.get(url)
    data = response.json()

    tracks = []
    for rank, track in enumerate(data["data"], 1):
        tracks.append(
            {
                "rank": rank,
                "title": track["title"],
                "artist": track["artist"]["name"],
                "album": track.get("album", {}).get("title", ""),
                "deezer_id": track["id"],
                "duration": track["duration"],
                "position": track.get("position", rank),
            }
        )

    return tracks


if __name__ == "__main__":
    tracks = collect_deezer_chart()
    for t in tracks[:5]:
        print(f"{t['rank']}. {t['title']} - {t['artist']}")
