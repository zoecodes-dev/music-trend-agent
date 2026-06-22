"""스냅샷·Discovery·히스토리·워치리스트 데이터를 LLM 프롬프트용 텍스트로 변환."""


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
