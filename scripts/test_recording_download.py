import sys, os, httpx
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from pusula.zoho.crm import coql
from pusula.zoho.auth import get_access_token

q = ("select id, Voice_Recording__s from Calls "
     "where Call_Start_Time > '2026-07-01T00:00:00+03:00'")
url = None
for r in coql(q):
    if r.get("Voice_Recording__s"):
        url = r["Voice_Recording__s"]
        break
print("URL:", url)

tok = get_access_token()
denemeler = [
    ("oauth header",     {"Authorization": f"Zoho-oauthtoken {tok}"}),
    ("bearer",           {"Authorization": f"Bearer {tok}"}),
    ("oauth + accept",   {"Authorization": f"Zoho-oauthtoken {tok}", "Accept": "*/*"}),
    ("header yok",       {}),
]
for ad, h in denemeler:
    try:
        r2 = httpx.get(url, headers=h, follow_redirects=True, timeout=60)
        print(f"\n--- {ad}: HTTP {r2.status_code}, {len(r2.content)} byte")
        print(r2.text[:400])
    except Exception as e:
        print(f"\n--- {ad}: HATA {e}")
