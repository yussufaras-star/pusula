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
    if r.status_code != 200:
        return f"HTTP {r.status_code}"
    return r.json().get("pagination", {}).get("total_count", "?")

aylar = [
    ("2026-02","2026-02-01 00:00:00 UTC","2026-02-28 23:59:59 UTC"),
    ("2026-03","2026-03-01 00:00:00 UTC","2026-03-31 23:59:59 UTC"),
    ("2026-04","2026-04-01 00:00:00 UTC","2026-04-30 23:59:59 UTC"),
    ("2026-05","2026-05-01 00:00:00 UTC","2026-05-31 23:59:59 UTC"),
    ("2026-06","2026-06-01 00:00:00 UTC","2026-06-30 23:59:59 UTC"),
    ("2026-07","2026-07-01 00:00:00 UTC","2026-07-31 23:59:59 UTC"),
]

print(f"{'ay':<10}{'kayitli':>9}{'silinmis':>10}")
for ad, bas, bit in aylar:
    print(f"{ad:<10}{say(bas,bit,'true'):>9}{say(bas,bit,'deleted'):>10}")
