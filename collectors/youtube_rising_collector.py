import os
import re
from datetime import datetime, timedelta, timezone
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# 빅 아티스트 블랙리스트
# 목적: 이미 메이저인 아티스트 영상을 라이징 감지에서 제외
# ──────────────────────────────────────────────

# 아티스트 이름 (채널명 또는 영상 제목에 포함되면 제외)
BIG_ARTIST_NAMES = {
    # HYBE
    'bts', 'bangtan', '방탄소년단',
    'tomorrow x together', 'txt',
    'enhypen',
    'le sserafim', '르세라핌',
    'newjeans', '뉴진스',
    'seventeen', '세븐틴',
    'fromis_9',
    'btob',
    # SM
    'aespa', '에스파',
    'nct', 'nct 127', 'nct dream', 'wayv', 'nct wish',
    'exo',
    'shinee', '샤이니',
    'super junior',
    'red velvet', '레드벨벳',
    'girls generation', 'snsd',
    'riize',
    # YG
    'blackpink', '블랙핑크',
    'bigbang', '빅뱅',
    'winner',
    'ikon',
    '2ne1',
    'treasure',
    # JYP
    'twice', '트와이스',
    'stray kids', '스트레이키즈',
    'itzy', '있지',
    'nmixx',
    'day6',
    'got7',
    '2pm',
    # 글로벌 메이저
    'taylor swift',
    'drake',
    'kendrick lamar',
    'billie eilish',
    'the weeknd',
    'bad bunny',
    'ed sheeran',
    'ariana grande',
    'post malone',
    'travis scott',
    'olivia rodrigo',
    'doja cat',
    'sza',
}

# ──────────────────────────────────────────────
# 지역 언어/장르 노이즈 블랙리스트
# 목적: SoundTag taxonomy Tier 1+2에 없는 지역 장르 제외
# ──────────────────────────────────────────────
REGIONAL_NOISE_KEYWORDS = {
    # 인도 지역 언어/장르
    'haryanvi', 'bhojpuri', 'punjabi song', 'hindi song',
    'kokborok', 'assamese', 'odia', 'marathi', 'tamil song',
    'telugu song', 'kannada song', 'malayalam song', 'gujarati',
    'rajasthani', 'nagpuri', 'maithili', 'chhattisgarhi',
    'bodo music', 'kokborok', 'manipuri', 'mizo',  # 인도 북동부
    'urdu song', 'maaf', 'latest hindi',            # 힌디/우르두
    'punjabi', 'janaabe', 'panjabi',                # 펀자브
    'bodo song', 'new bodo',
    # 기타 지역 장르
    'dangdut', 'lagu', 'lirik',        # 인도네시아
    'nasyid', 'nusantara',             # 말레이시아
    'thai song', 'เพลง',               # 태국
    'myanmar song', 'khmer song',      # 동남아
    'arabic song', 'nasheed',          # 아랍
    # 인도 힙합/유튜버 콜라보 노이즈
    'fukra insaan', 'carry minati', 'technical guruji',  # 인도 유명 유튜버
    'hindi rap', 'desi hip hop', 'indian rapper',
}

# 채널 구독자 수 상한선 (이 이상이면 이미 메이저로 간주)
SUBSCRIBER_THRESHOLD = 3_000_000  # 300만

# 라이징 감지용 검색 키워드
def _build_search_queries():
    year = datetime.now().year
    return [
        f'new kpop mv {year}',
        'kpop debut mv',
        f'new artist official mv {year}',
        f'underground hiphop korea {year}',
        'indie korean music',
        f'afrobeats new artist {year}',
        'pluggnb new artist',
        f'amapiano new artist {year}',
        f'hyperpop new artist {year}',
        'uk garage new artist',
        f'phonk new artist {year}',
        'brazilian funk new artist',
        'jersey club new artist',
        # 미국/UK 신예 중심으로 재편
        f'new artist debut us {year}',
        f'indie artist breakthrough {year}',
        f'underground rapper emerging us {year}',
        f'new rb artist {year}',
        f'bedroom pop new artist {year}'
        f"uk drill new artist {year}",
        f"uk underground new artist {year}",
        f"drum and bass new artist {year}",
    ]

SEARCH_QUERIES = _build_search_queries()


# ──────────────────────────────────────────────
# 유틸 함수
# ──────────────────────────────────────────────

def _normalize_title(title: str) -> str:
    """
    중복 감지용 제목 정규화.
    'Official MV', '(M/V)', '4K', 한국어 괄호 등 불필요한 수식어 제거 후 소문자화.
    예: "Supernova (Official MV) 4K" → "supernova"
    """
    title = title.lower()
    # 괄호 안 내용 제거: (official mv), [lyrics], 【4k】 등
    title = re.sub(r'[\(\[\【][^\)\]\】]*[\)\]\】]', '', title)
    # 흔한 수식어 제거
    noise_words = [
        'official', 'mv', 'm/v', 'music video', 'lyric', 'lyrics',
        'audio', '4k', 'hd', 'performance', 'dance', 'ver', 'version',
        'feat', 'ft', 'prod', 'teaser', 'visualizer',
    ]
    for w in noise_words:
        title = re.sub(rf'\b{re.escape(w)}\b', '', title)
    # 특수문자·연속 공백 제거
    title = re.sub(r'[^\w\s가-힣]', ' ', title)
    title = re.sub(r'\s+', ' ', title).strip()
    return title


def _is_big_artist(title: str, channel: str) -> bool:
    """제목 또는 채널명에 빅 아티스트 이름이 포함되면 True"""
    combined = (title + ' ' + channel).lower()
    for name in BIG_ARTIST_NAMES:
        # 단어 경계 매칭 (예: 'nct'가 'contact' 안에서 매칭되는 것 방지)
        if re.search(rf'\b{re.escape(name)}\b', combined):
            return True
    return False

def _is_regional_noise(title: str) -> bool:
    """제목에 지역 언어/장르 노이즈 키워드가 포함되면 True"""
    title_lower = title.lower()
    for keyword in REGIONAL_NOISE_KEYWORDS:
        if keyword in title_lower:
            return True
    return False


def _is_big_channel(subscriber_count: int) -> bool:
    """구독자 수가 임계값 이상이면 메이저 채널로 간주"""
    return subscriber_count >= SUBSCRIBER_THRESHOLD


# ──────────────────────────────────────────────
# 메인 수집 함수
# ──────────────────────────────────────────────

def collect_youtube_rising(queries=None, days_back=7, max_per_query=5):  # 10 → 5
    """
    최근 N일 내 업로드된 영상 중 views_per_day가 높은 라이징 곡 감지.

    필터링:
    - 빅 아티스트 블랙리스트 (제목·채널명 기준)
    - 채널 구독자 수 SUBSCRIBER_THRESHOLD 이상 제외
    - 같은 곡의 중복 영상 병합 (정규화된 제목 기준, views_per_day 최고 영상 유지)
    """
    if queries is None:
        queries = SEARCH_QUERIES

    youtube = build('youtube', 'v3', developerKey=os.getenv('YOUTUBE_API_KEY'))
    published_after = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()

    raw_videos = []
    seen_video_ids = set()

    # ── 1단계: 검색 및 기본 데이터 수집 ──
    for query in queries:
        try:
            search_resp = youtube.search().list(
                part='snippet',
                q=query,
                type='video',
                videoCategoryId='10',  # Music
                publishedAfter=published_after,
                order='viewCount',
                maxResults=max_per_query,
            ).execute()

            video_ids = [
                item['id']['videoId']
                for item in search_resp.get('items', [])
                if item['id']['videoId'] not in seen_video_ids
            ]

            if not video_ids:
                continue

            # 조회수 + 채널 ID 가져오기
            stats_resp = youtube.videos().list(
                part='snippet,statistics',
                id=','.join(video_ids),
            ).execute()

            # 채널 구독자 수 일괄 조회 (API quota 절약: 채널 ID 묶어서 1번 호출)
            channel_ids = list({
                v['snippet']['channelId']
                for v in stats_resp['items']
            })
            channel_resp = youtube.channels().list(
                part='statistics',
                id=','.join(channel_ids),
            ).execute()
            subscriber_map = {
                ch['id']: int(ch['statistics'].get('subscriberCount', 0))
                for ch in channel_resp.get('items', [])
            }

            for video in stats_resp['items']:
                vid = video['id']
                if vid in seen_video_ids:
                    continue
                seen_video_ids.add(vid)

                stats = video['statistics']
                snippet = video['snippet']
                channel_id = snippet['channelId']
                subscriber_count = subscriber_map.get(channel_id, 0)

                published = datetime.fromisoformat(
                    snippet['publishedAt'].replace('Z', '+00:00')
                )
                days_since = max((datetime.now(timezone.utc) - published).days, 1)
                views = int(stats.get('viewCount', 0))
                likes = int(stats.get('likeCount', 0))
                title = snippet['title']
                channel = snippet['channelTitle']

                # ── 필터링 ──
                if _is_big_artist(title, channel):
                    print(f"  ⛔ [빅아티스트 제외] {title} — {channel}")
                    continue
                if _is_big_channel(subscriber_count):
                    print(f"  ⛔ [메이저채널 제외] {channel} ({subscriber_count:,}명)")
                    continue
                if _is_regional_noise(title):
                    print(f"  ⛔ [지역노이즈 제외] {title}")
                    continue

                raw_videos.append({
                    'title': title,
                    'channel': channel,
                    'video_id': vid,
                    'views': views,
                    'likes': likes,
                    'published_at': snippet['publishedAt'],
                    'days_since_upload': days_since,
                    'views_per_day': round(views / days_since),
                    'subscriber_count': subscriber_count,
                    'query': query,
                    '_normalized_title': _normalize_title(title),
                })

            print(f"  🔍 '{query}': {len(video_ids)} videos found")

        except Exception as e:
            print(f"  ❌ '{query}': {e}")

    # ── 2단계: 중복 제거 ──
    # 같은 정규화 제목 = 같은 곡으로 간주 → views_per_day 가장 높은 영상 1개만 유지
    deduped: dict[str, dict] = {}
    for v in raw_videos:
        key = v['_normalized_title']
        if key not in deduped or v['views_per_day'] > deduped[key]['views_per_day']:
            deduped[key] = v

    result = list(deduped.values())
    
    # ── 3단계: 장르별 캡 (같은 쿼리에서 최대 3개) ──
    query_count: dict[str, int] = {}
    capped = []
    for v in result:
        q = v['query']
        query_count[q] = query_count.get(q, 0) + 1
        if query_count[q] <= 3:
            capped.append(v)
    result = capped

    # 내부용 키 제거
    for v in result:
        v.pop('_normalized_title', None)

    # views_per_day 상한선 — 50만 이상이면 이미 바이럴 완료, 라이징 아님
    result = [v for v in result if v['views_per_day'] <= 500_000]

    # 노이즈 컷 — 셀프 프로모션, 봇 트래픽, 비트메이커 type beat 등 제거
    # (절대 조회수 5천 미만, 구독자 100명 미만, 좋아요 비율 0.5% 미만 = 노이즈)
    def _is_noise(v):
        views = v.get('views', 0)
        if views < 5_000:
            return True
        if v.get('subscriber_count', 0) < 100:
            return True
        like_pct = v.get('likes', 0) / views if views > 0 else 0
        if like_pct < 0.005:  # 0.5%
            return True
        return False
    
    result = [v for v in result if not _is_noise(v)]

    # views_per_day 기준 정렬
    result.sort(key=lambda x: x['views_per_day'], reverse=True)

    print(f"\n  ✅ 필터링 후 {len(result)}개 (원본 {len(raw_videos)}개)")
    return result


# ──────────────────────────────────────────────
# 바이럴 신호 측정
# ──────────────────────────────────────────────

def _measure_viral_signal(youtube, title, artist, video_id):
    """리액션/커버/댄스 영상 수로 바이럴 신호 측정"""
    signals = {}
    for query_type in ["reaction", "cover", "dance challenge"]:
        result = youtube.search().list(
            part="snippet",
            q=f"{title} {artist} {query_type}",
            type="video",
            maxResults=5,
            order="relevance",
        ).execute()
        signals[query_type] = result["pageInfo"]["totalResults"]
    return signals

# ──────────────────────────────────────────────
# 단독 실행 테스트
# ──────────────────────────────────────────────

if __name__ == '__main__':
    print("🚀 YouTube Rising Detection (필터링 버전)\n")
    videos = collect_youtube_rising()

    print(f"\n📈 Top 15 Rising (by views/day):\n")
    for i, v in enumerate(videos[:15], 1):
        print(f"{i}. {v['title']} — {v['channel']}")
        print(f"   구독자: {v['subscriber_count']:,} | Views: {v['views']:,} | {v['views_per_day']:,}/day | {v['days_since_upload']}일 전")
        print(f"   Source: '{v['query']}'")
        print()