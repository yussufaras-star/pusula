import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from pusula.zoho.crm import coql

q = ("select id, Call_Duration_in_seconds, Voice_Recording__s, "
     "Call_Type, Outgoing_Call_Status, Scheduled_In_CRM from Calls "
     "where Call_Start_Time > '2026-06-01T00:00:00+03:00'")

kova = {}
for r in coql(q):
    sure = r.get("Call_Duration_in_seconds") or 0
    planli = str(r.get("Scheduled_In_CRM")) == "True"
    if planli:            k = "planlanmis (arama degil)"
    elif sure == 0:       k = "sure 0"
    elif sure < 10:       k = "1-9 sn"
    elif sure < 30:       k = "10-29 sn"
    elif sure < 120:      k = "30-119 sn"
    else:                 k = "120+ sn"
    t, v = kova.get(k, (0, 0))
    kova[k] = (t + 1, v + (1 if r.get("Voice_Recording__s") else 0))

print(f"{'kova':<28}{'toplam':>8}{'kayitli':>9}{'oran':>8}")
for k, (t, v) in sorted(kova.items(), key=lambda x: -x[1][0]):
    print(f"{k:<28}{t:>8}{v:>9}{v/t*100:>7.0f}%")
