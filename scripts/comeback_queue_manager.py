"""K-pop 컴백 데이터 큐 관리. 매일 노출할 4개 자동 픽."""
import json
import re
from datetime import datetime
from pathlib import Path
import os
import anthropic

QUEUE_PATH = Path(__file__).parent.parent / "data" / "comeback_queue.json"

def load_queue() -> dict:
    if QUEUE_PATH.exists():
        return json.loads(QUEUE_PATH.read_text())
    return {"items": {}}


def save_queue(queue: dict):
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)  # fresh clone: data/ 없을 수 있음
    QUEUE_PATH.write_text(json.dumps(queue, ensure_ascii=False, indent=2))

# 아티스트명 정규화는 utils/normalize.py 단일 모듈로 추출됨 (큐/워치리스트/collector 공유).
from utils.normalize import canonicalize

_ANTHROPIC_CLIENT = None

def _get_client():
    global _ANTHROPIC_CLIENT
    if _ANTHROPIC_CLIENT is None:
        _ANTHROPIC_CLIENT = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _ANTHROPIC_CLIENT


def _haiku_extract_artists_batch(entries: list[dict]) -> dict[int, list[str]]:
    """
    여러 entry의 텍스트를 한 번에 Haiku에 보내 아티스트 추출.
    반환: {entry_index: [artist1, artist2, ...]}
    """
    if not entries:
        return {}

    # 텍스트 모음
    blocks = []
    for i, entry in enumerate(entries):
        text = entry.get("content") or (entry.get("title", "") + " " + entry.get("snippet", ""))
        text = text.strip()[:800]  # entry당 최대 800자
        if text:
            blocks.append(f"[{i}] {text}")

    if not blocks:
        return {}

    prompt = f"""다음 텍스트 블록들에서 K-pop 아티스트/그룹명만 추출하세요.

추출 규칙:
- 실제 K-pop 아티스트/그룹명만 (예: BTS, NewJeans, CORTIS, ILLIT, 한로로, NMIXX)
- 앨범명/곡명은 제외 (예: REVERXE, WYLD, LOOP, GAME OVER 제외)
- 일반 단어는 제외 (예: MAY, POP, EP, MV, NEW, RAIN as weather 등)
- 잘린 토큰은 제외 (예: "RAIN. Specific", "and RAIN", "POP COMEBACK SCHEDULE")
- 한국 인디 아티스트도 포함 (예: 한로로, wave to earth)
- 미디어/잡지/플랫폼명 제외 (예: Billboard, Pitchfork, Spotify, Reddit)
- 5세대 신예 아티스트 적극 포함 (NAZE, KIIRAS, Hearts2Hearts 등)

출력 형식 (JSON만, 다른 텍스트 금지):
{{"0": ["아티스트1", "아티스트2"], "1": ["아티스트3"], ...}}

추출할 텍스트:
{chr(10).join(blocks)}"""

    try:
        response = _get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # ```json 펜스 제거
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.MULTILINE).strip()
        parsed = json.loads(raw)
        return {int(k): v for k, v in parsed.items() if isinstance(v, list)}
    except Exception as e:
        print(f"  ⚠️ Haiku 아티스트 추출 실패: {e}")
        return {}


def _extract_artists_from_text(text: str) -> list[str]:
    """
    단일 텍스트용 호환 함수. enqueue_new_items가 배치 호출 쓰도록 변경됐으므로
    이 함수는 fallback 또는 외부 직접 호출에서만 쓰임.
    """
    if not text:
        return []
    result = _haiku_extract_artists_batch([{"content": text}])
    return result.get(0, [])


def enqueue_new_items(collected: list, today: str):
    """컴백 collector 결과를 큐에 누적. Haiku 1회로 모든 entry 일괄 추출."""
    queue = load_queue()
    items = queue["items"]

    # 모든 entry에서 한 번에 아티스트 추출
    extracted = _haiku_extract_artists_batch(collected)
    print(f"  🤖 Haiku 아티스트 추출: {sum(len(v) for v in extracted.values())}명")

    for i, entry in enumerate(collected):
        text = entry.get("content", "") or (entry.get("title", "") + " " + entry.get("snippet", ""))
        artists = extracted.get(i, [])
        query = entry.get("query", "")

        for artist in artists:
            artist = canonicalize(artist)
            if artist in items:
                items[artist]["last_seen_in_collector"] = today
                src = set(items[artist].get("source_queries", []))
                src.add(query)
                items[artist]["source_queries"] = list(src)
            else:
                items[artist] = {
                    "first_collected": today,
                    "last_seen_in_collector": today,
                    "exposure_count": 0,
                    "last_exposed_at": None,
                    "source_queries": [query],
                    "sample_context": text[:200],
                }
    save_queue(queue)


def pick_today_exposures(today: str, n: int = 4) -> list[dict]:
    """오늘 노출할 항목 선택. 큐에서 우선순위 기반 픽."""
    queue = load_queue()
    items = queue["items"]
    today_dt = datetime.fromisoformat(today).date()

    candidates = []
    for artist, data in items.items():
        last_exp = data.get("last_exposed_at")
        exp_count = data.get("exposure_count", 0)
        first_collected_dt = datetime.fromisoformat(data["first_collected"]).date()
        last_seen_dt = datetime.fromisoformat(data["last_seen_in_collector"]).date()
        days_since_exposed = (today_dt - datetime.fromisoformat(last_exp).date()).days if last_exp else 999
        days_since_collected = (today_dt - first_collected_dt).days
        days_since_seen = (today_dt - last_seen_dt).days

        # 너무 오래된 항목 자동 제외 (14일 이상 collector에서 재발견 없으면)
        if days_since_seen > 14:
            continue

        # 우선순위 점수
        # - 노출 0회 + collector 재발견 = 최우선
        # - 노출 1회 + 5일 이상 = 갱신 후보
        # - 노출 2회+ = suppress
        if exp_count == 0:
            score = 100 + (10 - min(days_since_seen, 10))  # 최근에 본 항목 우선
        elif exp_count == 1 and days_since_exposed >= 5:
            score = 50 + (10 - min(days_since_seen, 10))
        elif exp_count >= 2:
            score = -1  # suppress
        else:
            score = 10  # 노출 1회 + 5일 미만 → 후순위

        if score > 0:
            candidates.append({
                "artist": artist,
                "exposure_count": exp_count,
                "days_since_collected": days_since_collected,
                "days_since_exposed": days_since_exposed if last_exp else None,
                "source_queries": data["source_queries"],
                "sample_context": data["sample_context"],
                "score": score,
            })

    # 점수 순 정렬, top n
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:n]


def mark_exposed(artist_names: list[str], today: str):
    """Analysis 후 노출 기록. 텍스트 파싱이 아닌 명시적 호출."""
    queue = load_queue()
    items = queue["items"]
    for name in artist_names:
        name = canonicalize(name)
        # 정확한 매칭 우선, 부분 매칭 fallback
        if name in items:
            items[name]["exposure_count"] += 1
            items[name]["last_exposed_at"] = today
        else:
            # 큐에 없는 이름 — 무시 (저장 안 함)
            pass
    save_queue(queue)