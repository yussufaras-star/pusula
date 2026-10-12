import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from pusula.zoho.crm import coql

q = ("select id, Call_Start_Time, Voice_Recording__s, Call_Duration_in_seconds, "
     "Outgoing_Call_Status, Scheduled_In_CRM from Calls "
     "where Call_Start_Time > '2026-06-01T00:00:00+03:00'")

toplam = kayitli = planlanmis = 0
for r in coql(q):
    toplam += 1
    if r.get("Voice_Recording__s"):
        kayitli += 1
    if str(r.get("Scheduled_In_CRM")) == "True":
        planlanmis += 1
print(f"toplam: {toplam}")
print(f"ses kaydi olan: {kayitli}")
print(f"Scheduled_In_CRM=True: {planlanmis}")
