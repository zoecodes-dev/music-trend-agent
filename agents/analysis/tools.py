"""tool 실행(캐시 저장 / 리포트 생성)과 ##7 '신규 추가' 2차 가드."""
import re
from datetime import datetime

from .config import REPORTS_DIR
from .loaders import load_watchlist, save_artist_cache


def _dedupe_section7_new(report: str) -> str:
    """## 7 '🔥 신규 추가'에서 history≥3 아티스트 줄 제거 (LLM이 신규/누적 교차 사용 시 2차 가드).
    신규 기준은 history 1~2회 — 3회+는 진행 중 신호 전용이므로 신규에 박히면 제거."""
    try:
        wl = load_watchlist()
        cumul = {a["value"].lower() for a in wl.get("artists", [])
                 if isinstance(a, dict) and len(a.get("history", [])) >= 3}
    except Exception:
        return report
    if not cumul:
        return report

    out, in_new = [], False
    for ln in report.splitlines():
        if re.match(r"\s*🔥\s*신규 추가", ln):
            in_new = True
            out.append(ln); continue
        if in_new and re.match(r"\s*🔁", ln):  # 진행 중 신호 시작 = 신규 섹션 끝
            in_new = False
        if in_new and ln.strip().startswith("-"):
            if any(name in ln.lower() for name in cumul):
                print(f"  🧹 ##7 신규에서 누적(3회+) 항목 제거: {ln.strip()[:50]}")
                continue
        out.append(ln)
    return "\n".join(out)


def execute_tool(tool_name, tool_input, cache):
    if tool_name == "cache_artist_analysis":
        artist = tool_input["artist"]
        cache[artist] = {
            "analyzed_at": datetime.now().strftime("%Y-%m-%d"),
            "last_seen_on_chart": datetime.now().strftime("%Y-%m-%d"),
            "sound_analysis": tool_input["sound_analysis"],
            "positioning": tool_input["positioning"],
            "ar_insight": tool_input["ar_insight"],
        }
        save_artist_cache(cache)
        return f"✅ 캐시 저장: {artist}"

    elif tool_name == "generate_report":
        report = tool_input["report"]
        report = _dedupe_section7_new(report)
        exposed = tool_input.get("comeback_exposed_artists", [])
        date_str = datetime.now().strftime("%Y-%m-%d")

        if exposed:
            try:
                from scripts.comeback_queue_manager import load_queue, mark_exposed
                queue_artists = set(load_queue()["items"].keys())
                valid = [a for a in exposed if a in queue_artists]
                invalid = [a for a in exposed if a not in queue_artists]
                if invalid:
                    print(f"  ⚠️ 큐 외 아티스트 노출 보고됨 (무시): {invalid}")
                if valid:
                    mark_exposed(valid, date_str)
                    print(f"  📌 comeback 큐 노출 기록: {len(valid)}명 — {valid}")
            except Exception as e:
                print(f"  ⚠️ comeback 큐 노출 기록 실패: {e}")

        REPORTS_DIR.mkdir(exist_ok=True)
        path = REPORTS_DIR / f"report_{date_str}.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        return f"✅ 리포트 저장: {path}"

    return f"알 수 없는 tool: {tool_name}"
