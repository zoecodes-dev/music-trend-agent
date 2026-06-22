"""
curation_labeler.py — Path 2 예시 기반 큐레이션 학습용 라벨링 도구

스냅샷에서 신호 후보를 추출해 하나씩 보여주고, O/X 키 입력만으로 라벨링.
이유는 묻지 않는다 — 시선(perspective)은 패턴으로만 축적한다.

사용법:
  python scripts/curation_labeler.py --date 2026-06-04   # 특정 날짜
  python scripts/curation_labeler.py --days 7            # 최근 7일치 순회
  python scripts/curation_labeler.py --stats             # 라벨 분포 확인

키:
  o = 끌림(O)   x = 안끌림(X)   Enter/s = 스킵   u = 직전 취소   q = 저장 후 종료

출력: data/curation_labels.json
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
SNAP_DIR = ROOT / "snapshots"
LABELS_PATH = ROOT / "data" / "curation_labels.json"

sys.path.insert(0, str(ROOT))
try:
    from utils.normalize import canonicalize
except ImportError:
    def canonicalize(name: str) -> str:
        return name.strip()


# ─────────────────────────────────────────────
# 신호 후보 추출 — prepare_snapshot_summary가 agent에 주입하는 것과 같은 범위
# ─────────────────────────────────────────────

def _vel_signal(v):
    if v == "NEW":
        return "[NEW]"
    try:
        iv = int(v)
        return f"[{iv:+d}]" if iv > 0 else None
    except (TypeError, ValueError):
        return None


def extract_candidates(snapshot: dict) -> list[dict]:
    """스냅샷 → (artist, source, context) 후보 리스트.
    같은 아티스트는 canonicalize 후 소스 병합. 다중 소스 우선 정렬."""
    src = snapshot.get("sources", {})
    raw = []  # (artist, source_tag, context)

    # 차트류 — 신규/상승만
    chart_specs = [
        ("apple_kr", "Apple KR"), ("apple_us", "Apple US"),
        ("apple_jp", "Apple JP"), ("apple_gb", "Apple GB"),
        ("lastfm_global", "Last.fm Global"), ("lastfm_us", "Last.fm US"),
        ("lastfm_uk", "Last.fm UK"), ("kworb_apple_ww", "Kworb WW"),
    ]
    for key, tag in chart_specs:
        for t in src.get(key, []) or []:
            mark = _vel_signal(t.get("velocity"))
            if t.get("is_new"):
                mark = "[NEW]"
            if not mark:
                continue
            raw.append((t.get("artist", ""), tag,
                        f"{tag} #{t.get('rank','?')} {t.get('title','')} {mark}"))

    # YouTube Trending KR — 신규/상승만
    for t in src.get("youtube_kr", []) or []:
        mark = _vel_signal(t.get("velocity"))
        if not mark:
            continue
        raw.append((t.get("channel", ""), "YT Trending",
                    f"YT Trending #{t.get('rank','?')} {t.get('title','')[:60]} "
                    f"{t.get('views',0):,}v {mark}"))

    # YouTube Rising — 전 항목 (collector가 이미 4중 필터)
    for v in src.get("youtube_rising", []) or []:
        delta = f"Δ{v['vpd_delta']:+,}/d" if v.get("vpd_delta") is not None else "신규"
        raw.append((v.get("channel", ""), "YT Rising",
                    f"YT Rising {v.get('title','')[:60]} {v.get('views_per_day',0):,}/d {delta}"))

    # Melon — TOP100 신규 + HOT100 신규/상승 (아티스트 단위)
    melon = src.get("melon", {}) if isinstance(src.get("melon"), dict) else {}
    for t in melon.get("top100", []) or []:
        if t.get("is_new"):
            raw.append((t.get("artist", ""), "Melon TOP",
                        f"Melon TOP100 #{t.get('rank','?')} {t.get('title','')} [NEW]"))
    hot_by_artist = defaultdict(list)
    for t in melon.get("hot100", []) or []:
        if t.get("is_new") or t.get("trend") == "up":
            hot_by_artist[t.get("artist", "")].append(t)
    for artist, tracks in hot_by_artist.items():
        new_cnt = sum(1 for x in tracks if x.get("is_new"))
        album = " 【앨범컴백 의심】" if new_cnt >= 3 else ""
        tr = ", ".join(f"{x['title']}#{x['rank']}" for x in sorted(tracks, key=lambda x: x["rank"])[:5])
        raw.append((artist, "Melon HOT", f"Melon HOT100{album}: {tr}"))

    # K-pop Comeback 큐 노출분
    kc = src.get("kpop_comeback", {}) if isinstance(src.get("kpop_comeback"), dict) else {}
    for exp in kc.get("today_exposures", []) or []:
        raw.append((exp.get("artist", ""), "Comeback Intel",
                    f"Comeback Intel: {exp.get('sample_context','')[:120]}"))

    # Emerging Intel — 텍스트 신호 (아티스트 미추출 답변, 쿼리 단위로 라벨)
    em = src.get("emerging_artist", {}) if isinstance(src.get("emerging_artist"), dict) else {}
    for q in em.get("artist_to_watch", []) or []:
        label = f"[Emerging] {q.get('query','')[:45]}"
        raw.append((label, "Emerging", f"{q.get('answer','')[:200]}"))

    # Reddit (활성화 시)
    for r in src.get("reddit_signals", []) or []:
        for a in r.get("artists", []):
            raw.append((a, "Reddit",
                        f"r/{r.get('subreddit','')} [{r.get('score',0)}↑] {r.get('title','')[:80]}"))

    # 아티스트 단위 병합
    grouped = {}
    for artist, tag, ctx in raw:
        if not artist:
            continue
        key = canonicalize(artist)
        g = grouped.setdefault(key, {"artist": key, "sources": [], "contexts": []})
        if tag not in g["sources"]:
            g["sources"].append(tag)
        if ctx not in g["contexts"]:
            g["contexts"].append(ctx)

    items = list(grouped.values())
    # 다중 소스 우선 (교차 신호가 정보량 최대), 그 안에선 이름순 고정 (재현성)
    items.sort(key=lambda g: (-len(g["sources"]), g["artist"]))
    return items


# ─────────────────────────────────────────────
# 라벨 저장소
# ─────────────────────────────────────────────

def load_labels() -> dict:
    if LABELS_PATH.exists():
        return json.loads(LABELS_PATH.read_text())
    return {"version": 1, "labels": []}


def save_labels(store: dict):
    LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LABELS_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=2))


def labeled_keys(store: dict) -> set:
    return {(l["date"], l["artist"]) for l in store["labels"]}


# ─────────────────────────────────────────────
# 인터랙티브 루프
# ─────────────────────────────────────────────

def label_day(date_str: str, store: dict, limit: int | None) -> bool:
    """True 반환 = 계속, False = 사용자가 q로 종료."""
    snap_path = SNAP_DIR / f"snapshot_{date_str}.json"
    if not snap_path.exists():
        print(f"  ({date_str} 스냅샷 없음 — 스킵)")
        return True

    snapshot = json.loads(snap_path.read_text())
    items = extract_candidates(snapshot)
    done = labeled_keys(store)
    pending = [i for i in items if (date_str, i["artist"]) not in done]
    already = len(items) - len(pending)
    if limit:
        pending = pending[:limit]

    if not pending:
        print(f"  ({date_str} 라벨할 항목 없음 — {len(items)}개 전부 완료)")
        return True

    lim_str = f", 이번 세션 {len(pending)}개 제한" if limit and already + len(pending) < len(items) else ""
    print(f"\n━━━ {date_str} — 남은 후보 {len(items)-already}개 (전체 {len(items)}, 기라벨 {already}{lim_str}) ━━━")
    session = []  # undo용

    idx = 0
    while idx < len(pending):
        item = pending[idx]
        n_src = len(item["sources"])
        cross = " ⚡교차" if n_src >= 2 else ""
        print(f"\n[{idx+1}/{len(pending)}] {item['artist']}  ({n_src}소스{cross}: {', '.join(item['sources'])})")
        for c in item["contexts"][:4]:
            print(f"    · {c}")

        try:
            key = input("  o/x/Enter(스킵)/u/q > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            key = "q"

        if key == "q":
            save_labels(store)
            print(f"\n저장 완료 → {LABELS_PATH}")
            return False
        if key == "u":
            if session:
                last = session.pop()
                store["labels"].remove(last)
                idx -= 1
                print(f"  ↩ 취소: {last['artist']} ({last['label']})")
            continue

        label = {"o": "O", "x": "X"}.get(key, "skip")
        rec = {
            "date": date_str,
            "artist": item["artist"],
            "sources": item["sources"],
            "contexts": item["contexts"][:4],
            "label": label,
            "labeled_at": datetime.now().isoformat(timespec="seconds"),
        }
        store["labels"].append(rec)
        session.append(rec)
        save_labels(store)  # write-through — 중단 안전
        idx += 1

    return True


def print_stats(store: dict):
    labels = store["labels"]
    if not labels:
        print("라벨 없음")
        return
    by = defaultdict(int)
    days = set()
    for l in labels:
        by[l["label"]] += 1
        days.add(l["date"])
    o, x, s = by.get("O", 0), by.get("X", 0), by.get("skip", 0)
    print(f"총 {len(labels)}건 / {len(days)}일치")
    print(f"  O(끌림): {o}  X(안끌림): {x}  skip: {s}")
    if o + x:
        print(f"  O 비율: {o/(o+x)*100:.0f}% (skip 제외)")
    # 교차 신호 vs 단일 소스에서의 O 비율 — 시선이 정량 신호와 얼마나 다른지 1차 지표
    multi_o = sum(1 for l in labels if l["label"] == "O" and len(l["sources"]) >= 2)
    multi_all = sum(1 for l in labels if l["label"] in ("O", "X") and len(l["sources"]) >= 2)
    single_o = sum(1 for l in labels if l["label"] == "O" and len(l["sources"]) == 1)
    single_all = sum(1 for l in labels if l["label"] in ("O", "X") and len(l["sources"]) == 1)
    if multi_all:
        print(f"  교차(2+소스) O 비율: {multi_o/multi_all*100:.0f}% ({multi_o}/{multi_all})")
    if single_all:
        print(f"  단일 소스 O 비율: {single_o/single_all*100:.0f}% ({single_o}/{single_all})")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="YYYY-MM-DD 단일 날짜")
    p.add_argument("--days", type=int, help="최근 N일치 스냅샷 순회")
    p.add_argument("--limit", type=int, help="날짜당 최대 항목 수")
    p.add_argument("--stats", action="store_true", help="라벨 분포만 출력")
    args = p.parse_args()

    store = load_labels()

    if args.stats:
        print_stats(store)
        return

    if args.date:
        dates = [args.date]
    else:
        n = args.days or 1
        all_snaps = sorted(SNAP_DIR.glob("snapshot_*.json"), reverse=True)
        dates = [f.stem.replace("snapshot_", "") for f in all_snaps[:n]]
        dates.reverse()  # 오래된 날부터 — 시간순 시선 유지

    if not dates:
        print("스냅샷 없음")
        return

    for d in dates:
        if not label_day(d, store, args.limit):
            return

    save_labels(store)
    print(f"\n전체 완료 → {LABELS_PATH}")
    print_stats(store)


if __name__ == "__main__":
    main()
