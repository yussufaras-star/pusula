import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from pusula.zoho.crm import coql

FIELDS = ["id","Subject","Call_Type","Outgoing_Call_Status","Scheduled_In_CRM",
          "Call_Start_Time","Call_Duration_in_seconds","Call_Result",
          "Gelen_Arama_Sonucu","Dialled_Number","Caller_ID",
          "Telephony_External_ID__s","Voice_Recording__s","Call_Agenda",
          "Record_Status__s","Who_Id","What_Id","Owner"]

ids = sys.argv[1].split(",")
q = ("select " + ",".join(FIELDS) + " from Calls "
     "where id in (" + ",".join(ids) + ")")

for r in coql(q):
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    print("-" * 60)
