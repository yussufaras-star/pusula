import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from pusula.zoho.crm import coql

q = ("select id, Phone, Mobile, Email, Secondary_Email, Full_Name "
     "from Leads where Modified_Time > '2026-07-01T00:00:00+03:00'")

t = p = m = e = 0
for r in coql(q):
    t += 1
    if r.get("Phone"): p += 1
    if r.get("Mobile"): m += 1
    if r.get("Email"): e += 1
    if t <= 3:
        print(r)
    if t >= 500:
        break
print(f"\n{t} lead: Phone={p}, Mobile={m}, Email={e}")
