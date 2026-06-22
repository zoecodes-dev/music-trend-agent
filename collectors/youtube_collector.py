import os
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()


def collect_youtube_trending(region_code="KR", max_results=50):
    """YouTube 음악 카테고리 트렌딩 영상 수집"""
    youtube = build("youtube", "v3", developerKey=os.getenv("YOUTUBE_API_KEY"))

    request = youtube.videos().list(
        part="snippet,statistics",
        chart="mostPopular",
        regionCode=region_code,
        videoCategoryId="10",  # Music
        maxResults=max_results,
    )
    response = request.execute()

    tracks = []
    for rank, video in enumerate(response["items"], 1):
        stats = video["statistics"]
        tracks.append(
            {
                "rank": rank,
                "title": video["snippet"]["title"],
                "channel": video["snippet"]["channelTitle"],
                "video_id": video["id"],
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "published_at": video["snippet"]["publishedAt"],
            }
        )

    return tracks


if __name__ == "__main__":
    tracks = collect_youtube_trending()
    for t in tracks[:5]:
        print(f"{t['rank']}. {t['title']} - {t['channel']}")
        print(f"   Views: {t['views']:,} | Likes: {t['likes']:,}")
