import sys, os
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from pusula.zoho.crm import coql

q = ("select id, Call_Start_Time, Voice_Recording__s, "
     "Call_Duration_in_seconds, Scheduled_In_CRM from Calls "
     "where Call_Start_Time > '2024-01-01T00:00:00+03:00'")

aylar = defaultdict(lambda: [0, 0, 0, 0])
for r in coql(q):
    t = r.get("Call_Start_Time")
    if not t:
        continue
    ay = str(t)[:7]
    sure = r.get("Call_Duration_in_seconds") or 0
    aylar[ay][0] += 1
    if r.get("Voice_Recording__s"):
        aylar[ay][1] += 1
    if sure > 0:
        aylar[ay][2] += 1
        if r.get("Voice_Recording__s"):
            aylar[ay][3] += 1

print(f"{'ay':<10}{'cagri':>7}{'kayit':>7}{'konusma':>9}{'k.kayit':>9}{'oran':>7}")
for ay in sorted(aylar):
    t, v, k, kv = aylar[ay]
    o = f"{kv/k*100:.0f}%" if k else "-"
    print(f"{ay:<10}{t:>7}{v:>7}{k:>9}{kv:>9}{o:>7}")
