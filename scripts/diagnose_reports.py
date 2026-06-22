"""
diagnose_reports.py — 리포트 누적 메트릭 진단

10일치 이상 리포트를 스캔하여 인계 문서의 4가지 패턴을 자동 카운트:
  1. ## 9 "우리가 먼저" 부재 빈도
  2. ## 9 자기비판 표현 (Discovery 타이밍/시스템 개선 필요 등)
  3. ## 3 빈 섹션 빈도 (Discovery 신예 0건)
  4. ## 2 도망 표현 (데이터 부족/판단 불가)

사용법:
  python diagnose_reports.py                    # 모든 report_*.md 스캔
  python diagnose_reports.py --since 2026-04-20 # 특정 날짜 이후만
  python diagnose_reports.py --json             # JSON 출력 (스크립트 연동용)
"""

import argparse
import json
import re
from pathlib import Path
from datetime import datetime


# ─────────────────────────────────────────────
# 패턴 정의
# ─────────────────────────────────────────────

# ## 9에서 "우리가 먼저" 케이스가 있다고 인정하는 표현
WE_FIRST_PRESENT_PATTERNS = [
    r"우리가 먼저 (잡은|포착|발견|확인)",
    r"메이저 매체 대비 \d+",   # "2-3일 선행" 등 명시적 선행 주장
    r"선행 (신호|발견|포착)",
]

# ## 9에서 "우리가 먼저" 케이스 부재를 인정하는 표현
WE_FIRST_ABSENT_PATTERNS = [
    r"검증된.*없음",
    r"교차 확인 없음",
    r"우리가 먼저.*부재",
    r"케이스 (없음|부재)",
    r"모두 매체.*먼저",
]

# ## 9 자기비판 표현 (3일 누적되면 트리거)
SELF_CRITICISM_PATTERNS = [
    r"Discovery (타이밍|시스템).*개선 필요",
    r"Discovery.*개선",
    r"독자적 발굴.*필요",
    r"발굴 (능력|체계).*부족",
]

# ## 2/## 3에서 강한 도망 표현 (분석 자체를 거부)
DODO_STRONG_PATTERNS = [
    r"데이터 부족",
    r"정보 부족",
    r"확인 (불가|어려움)",
    r"판단 (어려움|불가)",
    r"단정 (어려움|불가)",
    r"분석 (어려움|불가)",
    r"제한적",
]

# ## 2/## 3에서 약한 회피 표현 (분석은 하되 자신 없음)
DODO_WEAK_PATTERNS = [
    r"(으)?로 추정",
    r"추정되는",
    r"것으로 보임",
    r"(으)?로 보임",
    r"가능성 (있음|시사)",
    r"시사함?",
]


# ─────────────────────────────────────────────
# 섹션 추출
# ─────────────────────────────────────────────

def extract_section(text: str, section_num: int) -> str:
    """## N. 섹션부터 다음 ## 또는 --- 까지 추출"""
    pattern = rf"^## {section_num}\.[^\n]*\n(.*?)(?=^## \d+\.|^---|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def count_bold_items(section_text: str) -> int:
    """**로 시작하는 라인 수 (각 신예/사운드 항목의 헤드)"""
    return len(re.findall(r"^\*\*[^*]+\*\*", section_text, re.MULTILINE))


def any_pattern_match(text: str, patterns: list) -> bool:
    return any(re.search(p, text) for p in patterns)


def count_pattern_matches(text: str, patterns: list) -> int:
    return sum(len(re.findall(p, text)) for p in patterns)


# ─────────────────────────────────────────────
# 단일 리포트 진단
# ─────────────────────────────────────────────

def diagnose_report(filepath: Path) -> dict:
    text = filepath.read_text(encoding="utf-8")
    date_str = filepath.stem.replace("report_", "")

    s2 = extract_section(text, 2)
    s3 = extract_section(text, 3)
    s9 = extract_section(text, 9)

    # ## 9 분석
    we_first_present = any_pattern_match(s9, WE_FIRST_PRESENT_PATTERNS)
    we_first_absent = any_pattern_match(s9, WE_FIRST_ABSENT_PATTERNS)
    self_criticism = any_pattern_match(s9, SELF_CRITICISM_PATTERNS)

    # ## 2 분석
    s2_items = count_bold_items(s2)
    s2_dodo_strong = count_pattern_matches(s2, DODO_STRONG_PATTERNS)
    s2_dodo_weak = count_pattern_matches(s2, DODO_WEAK_PATTERNS)

    # ## 3 분석
    s3_items = count_bold_items(s3)
    s3_empty = s3_items == 0

    # ## 8 정의 추출 (정의 안정성 추적용)
    s8 = extract_section(text, 8)
    s8_first_line = s8.split("\n")[0][:80] if s8 else ""

    return {
        "date": date_str,
        "we_first_present": we_first_present,
        "we_first_absent": we_first_absent,
        "self_criticism_in_s9": self_criticism,
        "s2_items": s2_items,
        "s2_dodo_strong": s2_dodo_strong,
        "s2_dodo_weak": s2_dodo_weak,
        "s3_items": s3_items,
        "s3_empty": s3_empty,
        "s8_first_line": s8_first_line,
    }


# ─────────────────────────────────────────────
# 누적 분석
# ─────────────────────────────────────────────

def aggregate(diagnoses: list) -> dict:
    n = len(diagnoses)
    if n == 0:
        return {}

    we_first_present_days = [d["date"] for d in diagnoses if d["we_first_present"]]
    we_first_absent_days = [d["date"] for d in diagnoses if d["we_first_absent"]]
    self_crit_days = [d["date"] for d in diagnoses if d["self_criticism_in_s9"]]
    s3_empty_days = [d["date"] for d in diagnoses if d["s3_empty"]]
    s2_dodo_strong_days = [d["date"] for d in diagnoses if d["s2_dodo_strong"] > 0]
    s2_dodo_weak_days = [d["date"] for d in diagnoses if d["s2_dodo_weak"] > 0]

    # 최근 3일 자기비판 누적 (트리거 조건)
    recent_3 = sorted(diagnoses, key=lambda d: d["date"])[-3:]
    recent_3_self_crit = sum(1 for d in recent_3 if d["self_criticism_in_s9"])

    return {
        "total_days": n,
        "we_first_present": {
            "count": len(we_first_present_days),
            "days": we_first_present_days,
            "ratio": f"{len(we_first_present_days)}/{n}",
        },
        "we_first_absent": {
            "count": len(we_first_absent_days),
            "days": we_first_absent_days,
            "ratio": f"{len(we_first_absent_days)}/{n}",
        },
        "self_criticism": {
            "count": len(self_crit_days),
            "days": self_crit_days,
            "recent_3_count": recent_3_self_crit,
            "trigger_warning": recent_3_self_crit >= 3,
        },
        "s3_empty": {
            "count": len(s3_empty_days),
            "days": s3_empty_days,
            "ratio": f"{len(s3_empty_days)}/{n}",
            "pct": round(100 * len(s3_empty_days) / n, 1),
        },
        "s2_dodo_strong": {
            "count": len(s2_dodo_strong_days),
            "days": s2_dodo_strong_days,
            "ratio": f"{len(s2_dodo_strong_days)}/{n}",
            "pct": round(100 * len(s2_dodo_strong_days) / n, 1),
        },
        "s2_dodo_weak": {
            "count": len(s2_dodo_weak_days),
            "days": s2_dodo_weak_days,
            "ratio": f"{len(s2_dodo_weak_days)}/{n}",
            "pct": round(100 * len(s2_dodo_weak_days) / n, 1),
        },
    }


# ─────────────────────────────────────────────
# 출력
# ─────────────────────────────────────────────

def print_human(diagnoses: list, summary: dict):
    print("=" * 60)
    print(f"  리포트 누적 진단 ({summary['total_days']}일치)")
    print("=" * 60)

    # 일별 요약 테이블
    print("\n[일별 요약]")
    print(f"{'날짜':<12} {'##9 우리먼저':<12} {'##9 자기비판':<12} {'##3 빈':<8} {'##2 항목':<8} {'##2 강도망':<10} {'##2 약회피':<10}")
    for d in sorted(diagnoses, key=lambda x: x["date"]):
        we = "✓ 있음" if d["we_first_present"] else ("✗ 부재" if d["we_first_absent"] else "—")
        sc = "⚠ 있음" if d["self_criticism_in_s9"] else "—"
        s3 = "🔴 빈" if d["s3_empty"] else f"{d['s3_items']}건"
        print(f"{d['date']:<12} {we:<14} {sc:<14} {s3:<10} {d['s2_items']:<10} {d['s2_dodo_strong']:<12} {d['s2_dodo_weak']:<12}")

    # 누적 메트릭
    print("\n[누적 메트릭]")
    wf = summary["we_first_present"]
    wa = summary["we_first_absent"]
    sc = summary["self_criticism"]
    s3 = summary["s3_empty"]
    s2s = summary["s2_dodo_strong"]
    s2w = summary["s2_dodo_weak"]

    print(f"  ## 9 '우리가 먼저' 있음:  {wf['ratio']}일  → {wf['days']}")
    print(f"  ## 9 '우리가 먼저' 부재:  {wa['ratio']}일  → {wa['days']}")
    print(f"  ## 9 자기비판 표현:       {sc['count']}일  → {sc['days']}")
    print(f"  ## 3 빈 섹션:             {s3['ratio']}일 ({s3['pct']}%)  → {s3['days']}")
    print(f"  ## 2 강한 도망 (분석거부):{s2s['ratio']}일 ({s2s['pct']}%)  → {s2s['days']}")
    print(f"  ## 2 약한 회피 (추정/시사):{s2w['ratio']}일 ({s2w['pct']}%)  → {s2w['days']}")

    # 트리거 경고
    print("\n[트리거 체크]")
    if sc["trigger_warning"]:
        print(f"  🔴 ## 9 자기비판 최근 3일 모두 등장 — 프롬프트 수정 트리거 발동")
    else:
        print(f"  🟢 ## 9 자기비판 최근 3일 중 {sc['recent_3_count']}일 — 트리거 미발동")


def print_json(diagnoses: list, summary: dict):
    print(json.dumps({
        "summary": summary,
        "daily": diagnoses,
    }, ensure_ascii=False, indent=2))


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="/mnt/project", help="리포트 디렉토리")
    parser.add_argument("--since", help="YYYY-MM-DD 이후만")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    args = parser.parse_args()

    report_dir = Path(args.dir)
    files = sorted(report_dir.glob("report_*.md"))

    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").date()
        files = [
            f for f in files
            if datetime.strptime(f.stem.replace("report_", ""), "%Y-%m-%d").date() >= since
        ]

    if not files:
        print("리포트 없음")
        return

    diagnoses = [diagnose_report(f) for f in files]
    summary = aggregate(diagnoses)

    if args.json:
        print_json(diagnoses, summary)
    else:
        print_human(diagnoses, summary)


if __name__ == "__main__":
    main()
