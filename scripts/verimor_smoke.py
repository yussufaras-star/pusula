import sys, os, httpx, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

key = os.environ["VERIMOR_API_KEY"]
r = httpx.get("https://api.bulutsantralim.com/cdrs",
              params={"key": key, "page": 1}, timeout=60)
print("HTTP", r.status_code)
d = r.json()
print("pagination:", json.dumps(d.get("pagination"), indent=2))
for c in d.get("cdrs", [])[:3]:
    print(json.dumps(c, ensure_ascii=False, indent=2))
