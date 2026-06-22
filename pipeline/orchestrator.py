"""
Music Trend Agent — Orchestrator
Discovery agents를 병렬 실행하고 결과를 Analysis Agent에 전달.

실행 순서:
1. [병렬] cross_platform / producer / community / history
2. [순차] analysis (4개 결과 종합 → 최종 리포트)
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# 패키지 하위에서 직접 실행해도 루트 기준 import·데이터 경로가 동작하도록
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DISCOVERY_DIR = ROOT / "discovery_results"
DISCOVERY_DIR.mkdir(exist_ok=True)

# ──────────────────────────────────────────────
# 각 agent 임포트
# ──────────────────────────────────────────────

def run_cross_platform():
    from agents.cross_platform import run as run_agent
    return run_agent()

def run_producer():
    from agents.producer import run as run_agent
    return run_agent()

def run_community():
    from agents.community import run as run_agent
    return run_agent()

def run_history():
    from agents.history import run as run_agent
    return run_agent()

def run_analysis():
    from agents.analysis import run_analysis
    return run_analysis()


# ──────────────────────────────────────────────
# 병렬 실행
# ──────────────────────────────────────────────

# 그룹 1: 무거운 agent (input 큼, search_web 많음) — 순차로
# 그룹 2: 가벼운 agent — 병렬로
DISCOVERY_GROUPS = [
    ["cross_platform"],              # 단독
    ["community"],                    # 단독
    ["producer", "history"],          # 병렬 가능
]

AGENT_FUNCS = {
    "cross_platform": run_cross_platform,
    "producer":       run_producer,
    "community":      run_community,
    "history":        run_history,
}


def run_parallel_discovery():
    """Discovery Agent 그룹별 순차 실행. rate limit 회피."""
    print("=" * 60)
    print(f"🚀 Orchestrator 시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print("\n[1단계] Discovery Agents 실행 (그룹별 순차 + 그룹 내 병렬)\n")

    results = {}
    errors = {}
    start = time.time()
    today = datetime.now().strftime("%Y-%m-%d")

    for group_idx, group in enumerate(DISCOVERY_GROUPS, 1):
        print(f"  🔸 그룹 {group_idx}/{len(DISCOVERY_GROUPS)}: {group}")

        with ThreadPoolExecutor(max_workers=len(group)) as executor:
            futures = {}
            for name in group:
                result_path = DISCOVERY_DIR / f"{name}.json"
                if result_path.exists():
                    try:
                        with open(result_path) as f:
                            data = json.load(f)
                        if data.get("date") == today:
                            print(f"     ⏭️ {name} 이미 완료 — 건너뜀")
                            results[name] = data
                            continue
                    except Exception:
                        pass
                futures[executor.submit(AGENT_FUNCS[name])] = name
                
            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result()
                    results[name] = result
                    print(f"     ✅ {name} 완료")
                except Exception as e:
                    errors[name] = str(e)
                    print(f"     ❌ {name} 실패: {e}")

        # 그룹 간 45초 대기 (rate limit window 60초, 여유 확보)
        if group_idx < len(DISCOVERY_GROUPS):
            print(f"  ⏸️  45초 대기 (rate limit window 회복)")
            time.sleep(45)

    elapsed = round(time.time() - start, 1)
    print(f"\n  전체 실행 완료: {elapsed}초")
    print(f"  성공: {len(results)}개 / 실패: {len(errors)}개")

    if errors:
        print(f"  실패 목록: {list(errors.keys())}")

    return results, errors


def run_orchestrator():
    # 1단계: 병렬 Discovery
    results, errors = run_parallel_discovery()

    # Discovery 결과 없으면 Analysis 중단
    if not results:
        print("\n❌ 모든 Discovery Agent 실패 — Analysis 중단")
        sys.exit(1)

    print(f"\n✅ Discovery 완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    print(f"\n완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    run_orchestrator()
