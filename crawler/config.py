from __future__ import annotations

from pathlib import Path
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


BASE_URL = "https://govt.westlaw.com"
START_URL = "https://govt.westlaw.com/calregs/Index"
USER_AGENT = os.getenv(
    "CCR_USER_AGENT",
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
)
CCR_BROWSER_CHANNEL = os.getenv("CCR_BROWSER_CHANNEL", "chromium")
CCR_CDP_URL = os.getenv("CCR_CDP_URL") or None

MAX_CONCURRENT_REQUESTS = 5
REQUEST_TIMEOUT_MS = 90_000
RETRY_DELAYS_SECONDS = (1, 2, 4)
RANDOM_DELAY_RANGE_SECONDS = (0.5, 1.5)
CHECKPOINT_EVERY = 50
CCR_PROXY = os.getenv("CCR_PROXY") or None

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
URLS_DIR = DATA_DIR / "urls"
RAW_DIR = DATA_DIR / "raw"
HTML_DIR = RAW_DIR / "html"
REPORTS_DIR = DATA_DIR / "reports"
CHECKPOINT_DIR = DATA_DIR / "checkpoints"
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"
BROWSER_STATE_PATH = DATA_DIR / "browser_state.json"

DISCOVERED_URLS_PATH = URLS_DIR / "discovered_urls.jsonl"
FETCH_MANIFEST_PATH = RAW_DIR / "pages_raw.jsonl"
SECTIONS_PATH = RAW_DIR / "sections.jsonl"
FAILURES_PATH = RAW_DIR / "failures.jsonl"
COVERAGE_REPORT_PATH = REPORTS_DIR / "coverage_report.md"
QDRANT_DIR = Path(os.getenv("LOCALAPPDATA", str(DATA_DIR))) / "ccr-agent-qdrant"
QDRANT_LOCAL_PATH = Path(os.getenv("QDRANT_LOCAL_PATH", str(QDRANT_DIR / "ccr_qdrant")))
QDRANT_COLLECTION = "ccr_sections"

TITLE_NAMES = {
    1: "General Provisions",
    2: "Administration",
    3: "Food and Agriculture",
    4: "Business Regulations",
    5: "Education",
    7: "Harbors and Navigation",
    8: "Industrial Relations",
    9: "Rehabilitative and Developmental Services",
    10: "Investment",
    11: "Law",
    12: "Military and Veterans Affairs",
    13: "Motor Vehicles",
    14: "Natural Resources",
    15: "Crime Prevention and Corrections",
    16: "Professional and Vocational Regulations",
    17: "Public Health",
    18: "Public Revenues",
    19: "Public Safety",
    20: "Public Utilities and Energy",
    21: "Public Works",
    22: "Social Security",
    23: "Waters",
    24: "California Building Standards Code",
    25: "Housing and Community Development",
    26: "Toxics",
    27: "Environmental Protection",
    28: "Managed Health Care",
}
