"""Builds a demo folder with fictional data, for screenshots and trying the dashboard:
    python3 scripts/demo-data.py /tmp/demo && cd /tmp/demo && autoapply serve   # http://127.0.0.1:8799
"""
import sys, json, random
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autoapply.db import DB
from autoapply import history
root = Path(sys.argv[1]); (root/"data").mkdir(parents=True, exist_ok=True)
db = DB(root/"data"/"autoapply.db")
# Demo data only: fictional companies.
rows = [["Position","Company","Location","Date Applied","Salary","Found on","Applied via","Notes"]]
cos = ["Brightline","Cobalt Labs","Fernwood Health","Gridpoint","Harbor AI","Juniper Data","Kestrel","Lumen Works","Maple Analytics","Nimbus","Orchard","Pinecrest","Quarry","Redwood Metrics","Summit Health","Tidewater"]
plat = ["Greenhouse","Lever","Ashby","Workday","Greenhouse","Ashby"]
random.seed(4)
d0 = datetime(2026,7,20)
for i,c in enumerate(cos*2):
    d = d0 + timedelta(days=int(i*3.4))
    note = random.choice(["","",f"Rejected {(d+timedelta(days=random.randint(5,25))).month}/{(d+timedelta(days=random.randint(5,25))).day}",""])
    rows.append([random.choice(["Senior Analytics Engineer","Staff Data Engineer","Analytics Engineering Manager","Data Platform Lead"]), c+("" if i<16 else " Group"), "Remote", f"{d.month}/{d.day}/2026", "$150K–$190K", "", random.choice(plat), note])
history.import_rows(db, rows)
now = datetime.now()
for k in range(40):
    jid=f"greenhouse:demo:{k}"
    db.upsert_job({"id":jid,"ats":"greenhouse","company":random.choice(cos),"title":"Analytics Engineer","location":"Remote (US)","url":"https://example.com","apply_url":"https://example.com","description":""})
    db.update(jid, found_at=(now-timedelta(days=random.randint(0,120))).strftime("%Y-%m-%d %H:%M"))
for k,(c,t,p) in enumerate([("Harbor AI","Senior Analytics Engineer","$170K–$200K"),("Pinecrest","Staff Data Engineer, Platform","$185K–$215K")]):
    jid=f"greenhouse:ready:{k}"
    db.upsert_job({"id":jid,"ats":"greenhouse","company":c,"title":t,"location":"Remote (US)","url":"https://example.com","apply_url":"https://example.com","description":""})
    db.update(jid,status="filled",pay=p,plan_json={"url":"https://example.com"},report_json={"filled":[],"held":[],"unanswered":[],"errors":[]})
for k in range(6):
    db.update(f"greenhouse:demo:{k}", status="skipped", error=random.choice(["onsite only","pay below range","title too junior"]))
ev=[("interview","Jordan Lee <jordan@cobaltlabs.example>","Next steps: schedule time with the hiring manager"),("rejected","Fernwood Health Talent <no-reply@greenhouse.io>","Update on your application"),("confirmed","Harbor AI <no-reply@ashbyhq.com>","Thank you for applying to Harbor AI"),("recruiter","Sam Rivera <sam@talentco.example>","Senior Analytics Engineer, remote, fintech client"),("recruiter","Priya Nair <priya@recruitpartners.example>","Data platform lead role"),("recruiter","Alex Kim <alex@staffingone.example>","Contract-to-hire analytics engineer")]
for i,(k,s,sub) in enumerate(ev):
    db.add_event({"id":f"e{i}","job_id":None,"kind":k,"at":(now-timedelta(days=i*2)).strftime("%Y-%m-%d %H:%M"),"sender":s,"subject":sub})
for kind in ["daily","find","sync-email","brief"]:
    db.log_run(kind, True, "demo")
(root/"config.yaml").write_text("data_dir: ./data\nreview: {host: 127.0.0.1, port: 8799}\nwork_search: {label: Work search, weekly_minimum: 3}\n")
(root/"profile.yaml").write_text((Path(__file__).resolve().parents[1] / "profile.example.yaml").read_text())
