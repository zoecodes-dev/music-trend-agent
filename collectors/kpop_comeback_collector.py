import os
from datetime import datetime
from tavily import TavilyClient
from dotenv import load_dotenv

load_dotenv()

client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))


def collect_comeback_schedule():
    """
    이번 달 K-pop 컴백 일정 + 한국 인디·신예 수집.
    Tavily 4회 (영문 1 + 한국어 3).
    """
    now = datetime.now()
    eng_month = now.strftime("%B %Y")
    kor_month = f"{now.year}년 {now.month}월"

    queries = [
        f"kpop comeback schedule {eng_month}",         # 글로벌 시각
        f"{kor_month} K팝 컴백 일정 발매",              # 한국 매체 메이저
        f"{kor_month} K팝 신예 데뷔 그룹",              # 신예·데뷔
        f"{kor_month} 한국 인디밴드 신곡 인기",          # 인디·언더그라운드
    ]

    comebacks = []
    success_count = 0

    for q in queries:
        try:
            result = client.search(
                query=q,
                search_depth="basic",
                max_results=5,
                include_answer=True,
            )

            if result.get("answer"):
                comebacks.append({
                    "type": "schedule_summary",
                    "query": q,
                    "content": result["answer"],
                    "sources": [r["url"] for r in result.get("results", [])],
                })

            for r in result.get("results", []):
                comebacks.append({
                    "type": "article",
                    "query": q,
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", "")[:300],
                    "published_date": r.get("published_date", ""),
                })

            success_count += 1

        except Exception as e:
            print(f"  ❌ 쿼리 실패 '{q[:40]}': {e}")

    print(f"  ✅ 컴백 일정: {len(comebacks)}개 항목 ({success_count}/{len(queries)}개 쿼리)")
    return comebacks


def collect_kpop_comeback_intel():
    """
    K-pop 컴백 일정 + 한국 인디·신예 수집. Tavily 4회.
    아티스트 반응 수집은 agent가 schedule 텍스트 읽고 직접 search_web으로 처리.
    """
    schedule = collect_comeback_schedule()

    return {
        "schedule": schedule,
        "collected_at": datetime.now().isoformat(),
        "tavily_calls_used": 4,
    }


if __name__ == "__main__":
    print("🎤 K-pop Comeback Intel 수집 테스트\n")
    data = collect_kpop_comeback_intel()

    print(f"\n📅 수집 결과: {len(data['schedule'])}개 항목")
    for item in data["schedule"][:5]:
        if item["type"] == "schedule_summary":
            print(f"\n[summary | query: {item['query']}]")
            print(f"  {item['content'][:300]}")
        else:
            print(f"\n[article | query: {item['query']}]")
            print(f"  {item['title']}")
            print(f"  {item['snippet'][:150]}")