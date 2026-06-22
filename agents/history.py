"""
Music Trend Agent — History Tracker Agent
역할:
- 워치리스트 추가일 vs 메이저 매체 최초 언급일 비교 → "우리가 먼저" 검증
- 리포트 중복 감지 — 같은 신호가 며칠째 반복되는지
- 데이터 품질 검증 — 워치리스트 추가 근거가 실제로 맞았는지
결과: discovery_results/history.json + history.json 누적 저장
"""

import json
import os
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
from tavily import TavilyClient
from utils.watchlist import get_latest_signal

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
WATCHLIST_PATH = ROOT / "data" / "watchlist.json"
HISTORY_PATH = ROOT / "data" / "verification_history.json"
RESULT_PATH = ROOT / "discovery_results" / "history.json"

# 시스템 최초 가동일 — 이 날짜 이전의 "우리 발굴" 주장은 구조적으로 불가능
SYSTEM_GENESIS_DATE = "2026-04-05"  # 첫 snapshot 생성일

client = anthropic.Anthropic()
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))


# ──────────────────────────────────────────────
# 히스토리 관리
# ──────────────────────────────────────────────

def load_history() -> dict:
    if HISTORY_PATH.exists():
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"artists": {}, "genres": {}, "verified": []}


def save_history(history: dict):
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)  # fresh clone: data/ 없을 수 있음
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def load_watchlist() -> dict:
    if WATCHLIST_PATH.exists():
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_recent_reports(n=7) -> list:
    """최근 N일 리포트 로드."""
    files = sorted(REPORTS_DIR.glob("report_*.md"), reverse=True)
    reports = []
    for f in list(files)[:n]:
        with open(f, "r", encoding="utf-8") as fp:
            reports.append({"date": f.stem.replace("report_", ""), "content": fp.read()})
    return reports

def load_recent_snapshots(days: int = 3) -> list:
    """최근 N일의 snapshot.json 파일을 로드하여 시간순 리스트로 반환."""
    from datetime import timedelta
    
    snapshots = []
    today = datetime.now().date()
    base_dir = ROOT / "snapshots"
        
    for i in range(days):
        target_date = today - timedelta(days=i)
        snap_path = base_dir / f"snapshot_{target_date.isoformat()}.json"
        if snap_path.exists():
            try:
                with open(snap_path, encoding="utf-8") as f:
                    snapshots.append(json.load(f))
            except Exception:
                pass
    
    # 오래된 것이 먼저, 최신이 마지막 (snapshots[-3:] 슬라이스에 맞춤)
    return list(reversed(snapshots))


# ──────────────────────────────────────────────
# 중복 감지
# ──────────────────────────────────────────────

def detect_duplicates(reports: list, watchlist: dict, snapshots: list = None) -> dict:
    """
    최근 리포트에서 반복 등장 + 변화 없는 항목만 중복으로 감지.
    스냅샷 비교로 차트 위치/플랫폼이 변하면 활발한 라이징으로 간주, 중복 처리 안 함.
    
    반환: {artist: {"count": int, "is_static": bool}}
    """
    from collections import Counter
    mention_counts = Counter()

    for report in reports:
        content = report["content"].lower()
        for a in watchlist.get("artists", []):
            if isinstance(a, dict) and a["value"].lower() in content:
                mention_counts[a["value"]] += 1
        for g in watchlist.get("genres", []):
            if isinstance(g, dict) and g["value"].lower() in content:
                mention_counts[g["value"]] += 1

    # snapshots 있으면 변화 감지로 보강
    result = {}
    for artist, count in mention_counts.items():
        if not snapshots or len(snapshots) < 2:
            # snapshot 데이터 없으면 단순 카운트만
            result[artist] = {"count": count, "is_static": count >= 5}
            continue

        # 최근 2-3일 차트 진입 패턴 비교
        chart_signatures = []
        for snap in snapshots[-3:]:
            sig = []
            for src_key, tracks in snap.get("sources", {}).items():
                if not isinstance(tracks, list):
                    continue
                for t in tracks:
                    track_artist = (t.get("artist") or t.get("channel") or "").lower()
                    if artist.lower() in track_artist or track_artist in artist.lower():
                        sig.append(f"{src_key}#{t.get('rank')}")
            chart_signatures.append(set(sig))

        # 모든 날의 signature가 같으면 정적, 다르면 활발
        if len(chart_signatures) >= 2:
            is_static = all(s == chart_signatures[0] for s in chart_signatures)
        else:
            is_static = False

        result[artist] = {"count": count, "is_static": is_static}

    return result


# ──────────────────────────────────────────────
# 워치리스트 → 히스토리 동기화
# ──────────────────────────────────────----------------------------------------------------------------

def sync_watchlist_to_history(watchlist: dict, history: dict):
    """워치리스트 신규 항목을 히스토리에 추가."""
    for a in watchlist.get("artists", []):
        if isinstance(a, dict) and a["value"] not in history["artists"]:
            history["artists"][a["value"]] = {
                "added_at": a["added_at"],
                "confidence": a["confidence"],
                "reason": get_latest_signal(a),
                "media_first_mention": None,  # 나중에 채워짐
                "days_ahead": None,
                "verified": False,
            }

    for g in watchlist.get("genres", []):
        if isinstance(g, dict) and g["value"] not in history["genres"]:
            history["genres"][g["value"]] = {
                "added_at": g["added_at"],
                "confidence": g["confidence"],
                "reason": get_latest_signal(g),
                "media_first_mention": None,
                "days_ahead": None,
                "verified": False,
            }

    save_history(history)


# ──────────────────────────────────────────────
# Tools
# ──────────────────────────────────────────────

TOOLS = [
    {
        "name": "search_web",
        "description": (
            "메이저 매체의 아티스트 최초 언급일 확인. "
            "예: '{artist} site:billboard.com OR site:pitchfork.com 2026'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 3},
            },
            "required": ["query"],
        },
    },
    {
        "name": "record_media_mention",
        "description": "메이저 매체 최초 언급 확인 시 히스토리에 기록.",
        "input_schema": {
            "type": "object",
            "properties": {
                "artist_or_genre": {"type": "string"},
                "media_first_mention_date": {"type": "string", "description": "YYYY-MM-DD 형식"},
                "media_source": {"type": "string", "description": "어떤 매체인지"},
            },
            "required": ["artist_or_genre", "media_first_mention_date", "media_source"],
        },
    },
    {
        "name": "save_result",
        "description": "히스토리 추적 완료 후 결과 저장. 마지막에 1회만 호출.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "duplicates_to_suppress": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Analysis Agent에서 반복 언급 줄여야 할 항목 목록",
                },
            },
            "required": ["summary"],
        },
    },
]


def execute_tool(tool_name, tool_input, history, signals):
    if tool_name == "search_web":
        try:
            result = tavily.search(
                query=tool_input["query"],
                search_depth="basic",
                max_results=tool_input.get("max_results", 3),
                include_answer=True,
            )
            return f"{result.get('answer', '')}\n" + "\n".join(
                r.get("content", "")[:150] for r in result.get("results", [])
            )
        except Exception as e:
            return f"검색 실패: {e}"

    elif tool_name == "record_media_mention":
        artist = tool_input["artist_or_genre"]
        media_date = tool_input["media_first_mention_date"]

        # our_added_date는 LLM 입력 금지 — 히스토리의 실제 added_at만 사용
        our_date = None
        if artist in history.get("artists", {}):
            our_date = history["artists"][artist].get("added_at", "")[:10]
        elif artist in history.get("genres", {}):
            our_date = history["genres"][artist].get("added_at", "")[:10]
        if not our_date:
            return f"⛔ {artist}: 워치리스트에 added_at 없음 — 검증 불가, 스킵"

        # media_first_mention 미래 날짜 거부 (LLM 환각 차단 — 핵심)
        today_str = datetime.now().strftime("%Y-%m-%d")
        if media_date > today_str:
            return (
                f"⛔ {artist}: 매체 보도일({media_date})이 미래 — 환각으로 거부. "
                f"검색결과에 실제 2026년 과거 보도일 없으면 record 말고 스킵하세요."
            )

        try:
            our_dt = datetime.strptime(our_date, "%Y-%m-%d")
            media_dt = datetime.strptime(media_date, "%Y-%m-%d")
            days_ahead = (media_dt - our_dt).days
        except Exception:
            days_ahead = None

        # 시스템 수명 초과 선행은 불가능 — 이중 방어
        if days_ahead is not None and days_ahead > 0:
            max_possible = (datetime.now() - datetime.strptime(SYSTEM_GENESIS_DATE, "%Y-%m-%d")).days
            if days_ahead > max_possible:
                return f"⛔ {artist}: 선행 {days_ahead}일이 시스템 수명({max_possible}일) 초과 — 거부."

        # 시스템 수명 초과 선행은 불가능 — days_ahead 상한
        if days_ahead is not None and days_ahead > 0:
            max_possible = (datetime.now() - datetime.strptime(SYSTEM_GENESIS_DATE, "%Y-%m-%d")).days
            if days_ahead > max_possible:
                return (
                    f"⛔ {artist}: 선행 주장 {days_ahead}일이 시스템 수명({max_possible}일) 초과 — "
                    f"데이터 오류로 거부."
                )

        # 1년 초과 과거 언급은 A&R 관점에서 무의미 — 기록 거부
        if days_ahead is not None and days_ahead < -365:
            return (
                f"⛔ {artist}: 매체 언급이 {abs(days_ahead)}일 전 — 1년 초과. "
                f"이미 유명한 아티스트로 간주하여 검증 건너뜀"
            )
            
        record = {
            "media_first_mention": media_date,
            "media_source": tool_input["media_source"],
            "days_ahead": days_ahead,
            "verified": True,
            "verified_at": datetime.now().isoformat(),
        }

        if artist in history.get("artists", {}):
            history["artists"][artist].update(record)
        elif artist in history.get("genres", {}):
            history["genres"][artist].update(record)

        save_history(history)

        if days_ahead is not None and days_ahead > 0:
            return f"✅ {artist}: 우리가 {days_ahead}일 먼저 잡았음 (매체: {tool_input['media_source']})"
        elif days_ahead is not None and days_ahead <= 0:
            return f"⚠️ {artist}: 매체가 {abs(days_ahead)}일 먼저 ({tool_input['media_source']})"
        return f"✅ {artist}: 기록 완료"
    

    elif tool_name == "save_result":
        result = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "agent": "history",
            "summary": tool_input["summary"],
            "duplicates_to_suppress": tool_input.get("duplicates_to_suppress", []),
            "signals": signals,
            "saved_at": datetime.now().isoformat(),
        }
        RESULT_PATH.parent.mkdir(exist_ok=True)
        with open(RESULT_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return f"✅ 히스토리 결과 저장"

    return f"알 수 없는 tool: {tool_name}"


SYSTEM_PROMPT = """당신은 History Tracker Agent입니다.
두 가지를 합니다:

1. "우리가 먼저" 검증
입력된 "미검증 항목"은 이미 최근 14일 내 추가된 것만 필터링되어 있습니다.
입력에 있는 항목만 검증하세요. 입력에 없는 항목은 절대 검증하지 마세요.

search_web 쿼리 형식:
- "<artist> billboard OR pitchfork OR rollingstone 2026"
- 결과에서 2026년 이내 매체 보도만 유효한 신호입니다
- 2년 이상 전 보도가 나오면 해당 아티스트는 "오래 전부터 유명"이므로
  record_media_mention을 호출하지 말고 스킵하세요 (검증 대상 부적절)

**search 효율 룰 (절대 위반 금지)**:
- 입력 미검증 목록에 없는 아티스트는 search_web 호출 절대 금지.
  (예: 입력에 The Neighbourhood, Kehlani가 없으면 검색 자체를 하지 마라)
- 같은 아티스트에 대해 search_web 두 번째 호출 금지. 한 번의 검색으로 결정.
- search_web 결과를 받으면 **반드시 다음 턴**에 둘 중 하나:
  (a) record_media_mention 호출 (매체 보도가 보이면 가장 빠른 2026년 날짜로)
  (b) 스킵 (매체 보도 0건 또는 2년+ 과거뿐일 때)
- 정보 부족하다고 추가 search 금지. 이번 턴 verify 보류, 다음 아티스트로.

**턴 예산 룰**:
- max_iter는 9턴. 마지막 1턴은 save_result 강제 호출용.
- 한 아티스트당 평균 1.5턴 (search 1 + record 1 또는 스킵).
- 입력 6명까지만 처리. 7번째부터는 다음 실행으로 미룸.
- 우선순위: 입력 목록 위에서부터 순서대로 (이미 confidence 정렬돼 있음).

**media_first_mention_date 절대 규칙**:
- 검색 결과에 명시된 실제 2026년 과거 보도일(기사 게재일)만 입력.
- 보도일이 명확히 안 보이면 record_media_mention 호출 금지 → 스킵.
- 날짜를 추측·생성 금지. 미래 날짜는 자동 거부됨.
- 앨범 발매일·페스티벌 날짜는 매체 보도일이 아님 (혼동 금지).
- 우리 발굴일은 시스템이 자동 조회하므로 입력하지 않습니다.

미검증 항목이 비어있으면:
- search_web을 호출하지 마세요
- 바로 save_result(summary="검증 대상 없음")로 종료하세요

2. 중복 감지 (엄격 기준)
suppress는 다음 두 조건 모두 만족하는 경우에만:
   a) 5일 이상 반복 등장
   b) 차트 위치·플랫폼이 5일 동안 변화 없음 (정적)
   
다음은 suppress하지 마세요 (귀중한 신호):
   - 매체와 1-3일 차이로 발견된 신예 (활발한 라이징)
   - 매일 차트 위치나 진입 플랫폼이 바뀌는 항목
   - YouTube Rising에서 조회수가 계속 증가하는 항목
   
원칙: 메이저 아티스트(이미 잘 알려진)와 신예를 구분하세요.
매체 차이 일수만 보고 suppress하면 진짜 라이징을 놓칩니다.
Analysis Agent가 이걸 보고 반복 언급을 줄임.

search_web: 매체 최초 언급일 확인에만 사용. 아티스트당 1회.
텍스트 출력 턴 최소화.

작업:
1. 워치리스트 미검증 항목 확인
2. search_web으로 매체 언급일 조회
3. record_media_mention으로 기록
4. 중복 항목 파악
5. save_result (1회만)

한국어 작성, 고유명사 영어 유지."""


def run():
    print("📚 History Tracker Agent 시작")

    watchlist = load_watchlist()
    history = load_history()
    reports = load_recent_reports(7)
    signals = []

    # 워치리스트 → 히스토리 동기화
    sync_watchlist_to_history(watchlist, history)

    # 중복 감지 (변화 없는 정적 항목만)
    snapshots = load_recent_snapshots(days=3)  # 이미 위에서 로드되어 있을 듯
    duplicate_data = detect_duplicates(reports, watchlist, snapshots)
    
    truly_duplicate = {
        k: v["count"] for k, v in duplicate_data.items()
        if v["count"] >= 5 and v["is_static"]
    }
    duplicates_text = "\n".join(
        f"  {k}: {v}일 연속 등장 + 차트 변화 없음" for k, v in truly_duplicate.items()
    ) or "중복 없음 (활발한 라이징은 suppress 안 함)"

    # 미검증 항목 — 최근 14일 이내 추가된 것 + confidence 우선순위 정렬
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=14)).isoformat()[:10]

    unverified_pairs = [
        (k, v) for k, v in history.get("artists", {}).items()
        if not v.get("verified")
        and v.get("added_at")
        and v["added_at"][:10] >= cutoff
    ]

    # confidence 정렬: 🔴(2) → 🟡(1) → 🟢(0) → 그 외(-1), 동률은 added_at 최신순
    _conf_order = {"🔴": 2, "🟡": 1, "🟢": 0}
    unverified_pairs.sort(
        key=lambda kv: (
            _conf_order.get(kv[1].get("confidence", ""), -1),
            kv[1].get("added_at", ""),
        ),
        reverse=True,
    )

    # 9턴 예산 → 6명 cap
    unverified = dict(unverified_pairs[:6])
    unverified_text = "\n".join(
        f"  {k}: 추가일 {v['added_at'][:10]} (confidence: {v.get('confidence', '?')})"
        for k, v in unverified.items()
    ) or "미검증 항목 없음 (최근 14일 내 추가된 워치리스트 항목 중)"

    user_message = f"""히스토리 추적과 중복 감지를 수행하세요.

## 워치리스트 미검증 항목 (매체 언급일 확인 필요):
{unverified_text}

## 최근 7일 리포트 반복 등장 항목:
{duplicates_text}

미검증 항목 중 최근 2주 내 추가된 것 위주로 search_web으로 매체 언급일 확인하세요.
3일 이상 반복 등장 항목은 duplicates_to_suppress에 포함하세요."""

    messages = [{"role": "user", "content": user_message}]
    max_iter = 10
    iteration = 0
    done = False

    while iteration < max_iter and not done:
        iteration += 1
        response = create_message_with_retry(
            client,
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break
        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = execute_tool(block.name, block.input, history, signals)
                print(f"  🔧 {block.name}: {result[:80]}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
                if block.name == "save_result":
                    done = True

        messages.append({"role": "user", "content": tool_results})
        time.sleep(0.3)

    print(f"  ✅ History 완료 | {iteration}턴")
    return {"agent": "history", "signals": signals}


if __name__ == "__main__":
    run()
