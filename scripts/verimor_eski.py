import sys, os, httpx
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
key = os.environ["VERIMOR_API_KEY"]

def say(bas, bit, rp):
    r = httpx.get("https://api.bulutsantralim.com/cdrs",
                  params={"key": key, "start_stamp_from": bas,
                          "start_stamp_to": bit, "recording_present": rp,
                          "limit": 10}, timeout=60)
    return r.json().get("pagination", {}).get("total_count", "?") if r.status_code == 200 else f"HTTP {r.status_code}"

aylar = [
    ("2025-08","2025-08-01 00:00:00 UTC","2025-08-31 23:59:59 UTC"),
    ("2025-09","2025-09-01 00:00:00 UTC","2025-09-30 23:59:59 UTC"),
    ("2025-10","2025-10-01 00:00:00 UTC","2025-10-31 23:59:59 UTC"),
    ("2025-11","2025-11-01 00:00:00 UTC","2025-11-30 23:59:59 UTC"),
    ("2025-12","2025-12-01 00:00:00 UTC","2025-12-31 23:59:59 UTC"),
    ("2026-01","2026-01-01 00:00:00 UTC","2026-01-31 23:59:59 UTC"),
]
print(f"{'ay':<10}{'kayitli':>9}{'silinmis':>10}")
for ad, bas, bit in aylar:
    print(f"{ad:<10}{say(bas,bit,'true'):>9}{say(bas,bit,'deleted'):>10}")
