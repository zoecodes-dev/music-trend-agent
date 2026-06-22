"""경로·환경 상수. 패키지 어디서 임포트해도 루트 기준 경로·import가 동작하도록 sys.path 보정."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "snapshots"
REPORTS_DIR = ROOT / "reports"
DISCOVERY_DIR = ROOT / "discovery_results"
WATCHLIST_PATH = ROOT / "data" / "watchlist.json"
ARTIST_CACHE_PATH = ROOT / "data" / "artist_cache.json"
HISTORY_PATH = ROOT / "data" / "verification_history.json"
