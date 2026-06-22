"""뉴스레터 심화 — 일일 리포트 ##6에서 픽한 항목을 A&R 3축 깊이로 확장.

LLM 마킹분: 로컬(기존 3단 + 과거 14일 누적)만으로 심화.
--pick 1개: 위 + Tavily 1회(캐싱) 외부 리서치 보강.

실행:
  python scripts/newsletter_deepen.py                      # 오늘 리포트, 마킹분만
  python scripts/newsletter_deepen.py --pick "Djo"         # + Djo를 Tavily 심화
  python scripts/newsletter_deepen.py --date 2026-05-24 --pick "Djo"
"""
import os
import re
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta

import anthropic
from tavily import TavilyClient

BASE = Path(__file__).parent.parent
REPORTS_DIR = BASE / "reports"
NEWSLETTER_DIR = BASE / "newsletter"
CACHE_PATH = BASE / "data" / "newsletter_cache.json"

GREP_DAYS = 14  # 과거 리포트 권역 누적 조회 범위

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))


# ── 캐시 ────────────────────────────────────────────────
def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


# ── ##6 파싱 ────────────────────────────────────────────
def parse_section6(report_text: str) -> list[dict]:
    """리포트 ##6에서 [📰...] 태그된 항목 추출. 제목 + 태그 + 본문 3단."""
    # ## 6 ~ 다음 ## 헤더 직전까지
    m = re.search(r"##\s*6[.\s].*?(?=\n##\s*7[.\s]|\Z)", report_text, re.S)
    if not m:
        return []
    sec = m.group(0)

    items = []
    # 항목 = "**①/②/③ 제목 ... [📰 ...]**" 부터 다음 항목 제목 직전까지
    # 제목 줄에 [📰 ...] 태그가 있는 블록만 채택
    blocks = re.split(r"\n(?=\*\*[①②③④⑤])", sec)
    for b in blocks:
        title_m = re.match(r"\*\*([①②③④⑤].*?)\*\*", b.strip())
        if not title_m:
            continue
        title = title_m.group(1).strip()
        tag_m = re.search(r"\[📰\s*([^\]]+)\]", title)
        if not tag_m:
            continue  # 태그 없으면 후보 아님
        items.append({
            "title": re.sub(r"\s*\[📰[^\]]+\]", "", title).strip(),
            "tags": [t.strip() for t in re.split(r"[|,]", tag_m.group(1))],
            "body": b.strip(),
        })
    return items


# ── 과거 14일 권역 누적 grep (로컬, Tavily 미사용) ──────────
def grep_history(keyword: str, end_date: datetime) -> list[str]:
    """최근 GREP_DAYS일 리포트에서 keyword 등장 줄 수집."""
    hits = []
    for i in range(1, GREP_DAYS + 1):
        d = (end_date - timedelta(days=i)).strftime("%Y-%m-%d")
        f = REPORTS_DIR / f"report_{d}.md"
        if not f.exists():
            continue
        for ln in f.read_text(encoding="utf-8").splitlines():
            if keyword.lower() in ln.lower() and len(ln.strip()) > 10:
                hits.append(f"[{d}] {ln.strip()[:120]}")
    return hits[:8]  # 노이즈 컷


# ── Tavily 리서치 (픽 항목만, 캐싱) ──────────────────────
def tavily_research(artist: str, cache: dict) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    if artist in cache:
        return cache[artist]["research"]
    try:
        result = tavily.search(
            query=f"{artist} music sound style genre recent release",
            search_depth="basic",
            max_results=3,
            include_answer=True,
        )
        research = f"{result.get('answer', '')}\n" + "\n".join(
            r.get("content", "")[:150] for r in result.get("results", [])
        )
    except Exception as e:
        research = f"(리서치 실패: {e})"
    cache[artist] = {"research": research, "researched_at": today}
    save_cache(cache)
    return research


# ── A&R 3축 심화 (LLM) ──────────────────────────────────
DEEPEN_PROMPT = """너는 K-pop A&R 전문가의 뉴스레터 작성자다.
아래 일일 리포트 ##6 인사이트 항목을 뉴스레터 깊이로 확장한다.

[원본 ##6 항목]
{body}

[과거 14일 같은 권역/아티스트 누적 신호]
{history}

{research_block}

작성 규칙 — A&R 3축 골격에 기존 3단(관찰/왜 먹혔는가/K-pop 번역)을 녹여라:

## 왜 지금인가
- 타이밍의 구체적 근거. 과거 누적 신호가 있으면 "N일간 어떻게 쌓였는지" 궤적으로.
- 원본의 [관찰]을 시점·맥락으로 확장.

## 사운드의 본질
- 무엇이 이 사운드를 작동하게 하는가. BPM/구조/레퍼런스/장르 계보 구체적으로.
- 원본의 [왜 먹혔는가]를 사운드 레이어까지 파고들어 확장. 리서치 정보 있으면 디테일 보강.

## 어디에 적용하는가
- K-pop 컴백/앨범 기획에 바로 쓸 실행 가설. 그룹 타입(보이/걸/솔로)별 적용 차이.
- 원본의 [K-pop 번역]을 실행 단계까지 구체화. 전제 조건·리스크도 명시.

금지: 추상적 결론, "~이 중요함" 원론, 오늘 신호 아닌 일반론.
독자(A&R 실무자)가 "이번 컴백 기획에 바로 넣겠다" 수준의 구체성."""


def deepen(item: dict, history: list[str], research: str | None) -> str:
    research_block = ""
    if research:
        research_block = f"[외부 리서치 보강]\n{research}\n"
    prompt = DEEPEN_PROMPT.format(
        body=item["body"],
        history="\n".join(history) if history else "(누적 신호 없음 — 신규 권역)",
        research_block=research_block,
    )
    resp = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


# ── 메인 ────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--pick", default=None, help="Tavily 심화할 아티스트/키워드 1개")
    args = ap.parse_args()

    report_path = REPORTS_DIR / f"report_{args.date}.md"
    if not report_path.exists():
        print(f"리포트 없음: {report_path}")
        return
    report_text = report_path.read_text(encoding="utf-8")

    items = parse_section6(report_text)
    if not items:
        print("##6에 [📰] 태그된 후보 없음.")
        return

    cache = load_cache()
    end_date = datetime.strptime(args.date, "%Y-%m-%d")
    out = [f"# 뉴스레터 초안 — {args.date}\n"]

    pick_done = False
    for item in items:
        kw = item["title"].split("—")[0].split("(")[0].strip()[:30]
        history = grep_history(kw, end_date)

        research = None
        if args.pick and args.pick.lower() in item["title"].lower():
            research = tavily_research(args.pick, cache)
            pick_done = True

        body = deepen(item, history, research)
        tag_str = " / ".join(item["tags"])
        src = "🔬 Tavily 심화" if research else "📂 로컬 심화"
        out.append(f"\n---\n\n## {item['title']}\n_[{tag_str}] · {src}_\n\n{body}\n")

    if args.pick and not pick_done:
        print(f"⚠️ --pick '{args.pick}' 가 ##6 태그 항목에 없음 — Tavily 미실행.")

    NEWSLETTER_DIR.mkdir(parents=True, exist_ok=True)
    out_path = NEWSLETTER_DIR / f"newsletter_draft_{args.date}.md"
    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"✅ 생성: {out_path} ({len(items)}개 항목)")


if __name__ == "__main__":
    main()
