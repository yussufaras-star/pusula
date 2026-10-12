import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from pusula.zoho.crm import coql

gunler = ["2026-04-08","2026-04-15","2026-04-22","2026-04-29"]

for g in gunler:
    q = (f"select id, Call_Start_Time, Voice_Recording__s, Call_Duration_in_seconds "
         f"from Calls where Call_Start_Time > '{g}T00:00:00+03:00' "
         f"and Call_Start_Time < '{g}T23:59:59+03:00'")
    for r in coql(q):
        if r.get("Voice_Recording__s") and (r.get("Call_Duration_in_seconds") or 0) > 30:
            print(f"\n{g}")
            print(r["Voice_Recording__s"])
            break
