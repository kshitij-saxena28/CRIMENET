"""
DARK CRIMENET — deterministic, self-contained synthetic investigation dataset.

No external dataset is required to run the application.
Run:
    python scripts/seed_demo.py
Regenerate from scratch:
    python scripts/seed_demo.py --reset
"""
import argparse, hashlib, json, math, random, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.core.config import settings
from backend.app.db.database import Base, engine, SessionLocal
from backend.app.models.models import Entity, Relationship, Event, Case, Alert, Task, Evidence, AuditEvent

SEED = 26189
rng = random.Random(SEED)
BASE_TIME = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)

def iso(dt):
    return dt.replace(tzinfo=timezone.utc).isoformat()

def reset_database():
    if not settings.is_demo:
        raise SystemExit("Refusing --reset: APP_ENV is not 'demo'. This would destroy all users, cases and evidence.")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    # Keep the repository structure unchanged; only use the existing evidence_store.
    evdir = settings.evidence_path
    evdir.mkdir(parents=True, exist_ok=True)
    for p in evdir.iterdir():
        if p.is_file():
            p.unlink()

def add_entity(db, eid, name, typ, lat=None, lon=None, attrs=None):
    if not db.query(Entity).filter_by(external_id=eid).first():
        db.add(Entity(external_id=eid, name=name, entity_type=typ, confidence=.96,
                      attributes=json.dumps({**(attrs or {}), "demo_seed": True}), latitude=lat, longitude=lon))

def add_rel(db, s, t, rel, ref, tm, conf=.9, meta=None):
    db.add(Relationship(source_id=s, target_id=t, relation_type=rel, confidence=conf,
                        source_ref=ref, event_time=tm, metadata_json=json.dumps(meta or {}),
                        verification_state="verified", model_version="synthetic-grounded-v2"))

def add_event(db, eid, typ, entity, related="", tm=None, lat=None, lon=None,
              amount=None, duration=None, ref="SYNTHETIC-DEMO", meta=None):
    db.add(Event(event_id=eid, event_type=typ, entity_id=entity, related_entity_id=related,
                 event_time=tm, latitude=lat, longitude=lon, amount=amount,
                 duration_seconds=duration, source_ref=ref,
                 metadata_json=json.dumps(meta or {})))

FIRST = ["Aarav","Nisha","Kabir","Mira","Rohan","Ishaan","Ananya","Vikram","Meera","Arjun",
         "Neha","Aditya","Karan","Riya","Dev","Simran","Manav","Tanya","Rahul","Priya"]
LAST = ["Mehta","Rao","Shah","Sen","Das","Kapoor","Malhotra","Verma","Iyer","Bhat",
        "Khanna","Sethi","Joshi","Nair","Singh","Gupta","Chopra","Agarwal","Kulkarni","Patel"]

def main(reset=False):
    if reset:
        reset_database()
    else:
        Base.metadata.create_all(bind=engine)

    db = SessionLocal()

    # Loading the bundled dataset is intentionally idempotent. This prevents the UI
    # Load Demo Dataset button (and repeated CLI runs) from multiplying synthetic rows.
    if not reset:
        existing_demo = db.query(Case).filter(Case.title.like("Synthetic Network Investigation%")).count()
        if existing_demo:
            print("DARK CRIMENET synthetic dataset already loaded; nothing to do.")
            db.close()
            return

    # ---- Core entities ----
    people = []
    for i in range(1, 81):
        eid=f"P{i:03d}"
        if i <= 20:
            name=f"{FIRST[i-1]} {LAST[i-1]}"
        else:
            name=f"{FIRST[(i*7)%len(FIRST)]} {LAST[(i*11)%len(LAST)]} {i}"
        lat=28.54 + rng.random()*0.18
        lon=77.16 + rng.random()*0.24
        add_entity(db,eid,name,"PERSON",lat,lon,{"age_band":rng.choice(["18-25","26-35","36-50","50+"]),
                                                  "city":"Delhi NCR","synthetic":True})
        people.append(eid)

    phones=[]
    for i in range(1, 41):
        eid=f"PH{i:03d}"; num=f"98{70000000+i:08d}"[-10:]
        add_entity(db,eid,num,"PHONE",attrs={"number":num,"carrier":rng.choice(["Airtel","Jio","Vi"])})
        phones.append(eid)

    vehicles=[]
    for i in range(1, 31):
        eid=f"V{i:03d}"
        plate=f"DL{(i%10)+1:02d}{chr(65+(i%26))}{chr(65+((i*3)%26))}{1000+i}"
        add_entity(db,eid,plate,"VEHICLE",28.54+rng.random()*.18,77.16+rng.random()*.24,
                   {"plate":plate,"type":rng.choice(["Sedan","SUV","Hatchback","Van"])})
        vehicles.append(eid)

    accounts=[]
    for i in range(1, 51):
        eid=f"A{i:03d}"; acct=f"ACCT-{1000+i}"
        add_entity(db,eid,acct,"ACCOUNT",attrs={"account_number":acct,"bank":rng.choice(["Axis","HDFC","ICICI","SBI"])})
        accounts.append(eid)

    locations=[]
    loc_names=["Connaught Place","Sector 21","Karol Bagh","Gurugram Cyber Hub","Noida Sector 62",
               "Dwarka","Lajpat Nagar","Saket","Anand Vihar","IGI Airport","Ghaziabad","Faridabad",
               "Okhla","Vasant Kunj","Rajouri Garden","Janakpuri","Rohini","Mayur Vihar",
               "Greater Kailash","Nehru Place","Chandni Chowk","New Delhi Railway Station",
               "Kalkaji","Pitampura","Hauz Khas","Shalimar Bagh","Botanical Garden","MG Road","Golf Course","Aerocity"]
    for i,n in enumerate(loc_names,1):
        eid=f"L{i:03d}"; lat=28.50+rng.random()*.28; lon=77.05+rng.random()*.38
        add_entity(db,eid,n,"LOCATION",lat,lon,{"city":"Delhi NCR"})
        locations.append(eid)

    orgs=[]
    for i,n in enumerate(["Northstar Logistics","Vertex Trading","Bluewave Exports","Metro Courier","Apex Services"],1):
        eid=f"ORG{i:02d}"; add_entity(db,eid,n,"ORGANIZATION",28.55+rng.random()*.15,77.15+rng.random()*.25,
                                      {"sector":rng.choice(["logistics","trading","services"])})
        orgs.append(eid)

    cases=[]
    for i in range(1,9):
        cn=f"CASE-2026-{100+i:03d}"
        cases.append(cn)
        db.add(Case(case_number=cn,title=f"Synthetic Network Investigation {chr(64+i)}",
                    summary="Synthetic authorized case combining communications, finance, vehicles, locations, FIR references and temporal events.",
                    status="Active" if i<7 else "Review", created_by="seed_demo", visibility="shared", is_demo=True))

    db.flush()

    # ---- Stable ownership / association layer ----
    for i,p in enumerate(people):
        ph=phones[i % len(phones)]; v=vehicles[(i*3) % len(vehicles)]; a=accounts[i % len(accounts)]
        t=BASE_TIME+timedelta(days=i//30,hours=i%30)
        case=cases[i % len(cases)]
        for eid0 in (p, ph, v, a):
            ent=db.query(Entity).filter_by(external_id=eid0).first()
            attrs=json.loads(ent.attributes or "{}") if ent else {}
            nums=list(attrs.get("case_numbers", []))
            if case not in nums: nums.append(case)
            attrs["case_numbers"]=nums; ent.attributes=json.dumps(attrs, ensure_ascii=False)
        add_rel(db,p,ph,"REGISTERED_TO",f"CDR-MASTER-{i:04d}",t,.98,{"record":"subscriber","case_number":case})
        add_rel(db,p,v,"USED",f"VEH-MASTER-{i:04d}",t,.90,{"record":"vehicle_registry","case_number":case})
        add_rel(db,p,a,"OWNS",f"BANK-KYC-{i:04d}",t,.96,{"record":"account_profile","case_number":case})

    for i,org in enumerate(orgs):
        for p in people[i*12:(i+1)*12]:
            case=cases[people.index(p) % len(cases)]
            ent=db.query(Entity).filter_by(external_id=p).first()
            attrs=json.loads(ent.attributes or "{}") if ent else {}
            nums=list(attrs.get("case_numbers", []))
            if case not in nums: nums.append(case)
            attrs["case_numbers"]=nums; ent.attributes=json.dumps(attrs, ensure_ascii=False)
            add_rel(db,p,org,"ASSOCIATED_WITH",f"ORG-{i+1:02d}-{p}",BASE_TIME+timedelta(days=i),.86,{"case_number":case})

    # ---- Deliberate investigation story / bridges ----
    bridges=[
        ("P001","P021","CALLED"),("P021","P041","CALLED"),("P041","P061","CALLED"),
        ("P001","P003","MET"),("P003","P021","ASSOCIATED_WITH"),("P021","P061","ASSOCIATED_WITH"),
        ("P003","V001","USED"),("P004","V001","USED"), # contradiction
        ("P001","A001","TRANSFERRED_TO"),("A001","A021","TRANSFERRED_TO"),
        ("A021","A041","TRANSFERRED_TO"),("A041","P061","TRANSFERRED_TO"),
        ("P061","F001","MENTIONED_IN"),("ORG01","P061","ASSOCIATED_WITH")
    ]
    for j,(s,t,r) in enumerate(bridges):
        add_rel(db,s,t,r,f"CASE-LINK-{j+1:03d}",BASE_TIME+timedelta(days=3,hours=j),.93,
                {"investigation_seed":True,"case_number":cases[0]})
        for eid0 in (s,t):
            ent=db.query(Entity).filter_by(external_id=eid0).first()
            if ent:
                attrs=json.loads(ent.attributes or "{}")
                nums=list(attrs.get("case_numbers", []))
                if cases[0] not in nums: nums.append(cases[0])
                attrs["case_numbers"]=nums; ent.attributes=json.dumps(attrs, ensure_ascii=False)

    # ---- CDR events ----
    eid=1
    for i in range(1100):
        caller=people[(i*17+3)%len(people)]
        callee=people[(i*31+11)%len(people)]
        if caller==callee: callee=people[(people.index(caller)+1)%len(people)]
        # Inject a burst around the core network.
        if i%13==0:
            caller=people[0]
            callee=rng.choice([people[1],people[2],people[20],people[40],people[60]])
        tm=BASE_TIME+timedelta(hours=rng.randrange(0,24*30),minutes=rng.randrange(0,60))
        dur=int(max(8,rng.lognormvariate(4.8,.65)))
        case=cases[i % len(cases)]
        add_event(db,f"E{eid:05d}","COMMUNICATION",caller,callee,tm,
                  28.50+rng.random()*.30,77.05+rng.random()*.40,None,dur,
                  f"CDR-{2026}{i:05d}",{"direction":"outgoing","channel":"voice","case_number":case})
        eid+=1

    # ---- Financial events ----
    for i in range(850):
        src=accounts[(i*7+1)%len(accounts)]
        dst=accounts[(i*19+9)%len(accounts)]
        if src==dst: dst=accounts[(accounts.index(src)+1)%len(accounts)]
        if i%17==0:
            src=accounts[0]; dst=rng.choice([accounts[20],accounts[21],accounts[40],accounts[41]])
        amount=round(rng.uniform(2500,45000),2)
        if i%29==0: amount=round(rng.uniform(140000,450000),2)
        tm=BASE_TIME+timedelta(hours=rng.randrange(0,24*30),minutes=rng.randrange(0,60))
        case=cases[(i*3) % len(cases)]
        add_event(db,f"E{eid:05d}","TRANSACTION",src,dst,tm,None,None,amount,None,
                  f"BANK-{i:05d}",{"currency":"INR","channel":rng.choice(["NEFT","IMPS","UPI"]),"case_number":case})
        eid+=1

    # ---- Vehicle/location sightings ----
    for i in range(260):
        v=vehicles[(i*5+2)%len(vehicles)]
        p=people[(i*9+4)%len(people)]
        loc=locations[(i*7+3)%len(locations)]
        le=db.query(Entity).filter_by(external_id=loc).first()
        tm=BASE_TIME+timedelta(hours=rng.randrange(0,24*30),minutes=rng.randrange(0,60))
        case=cases[(i*5) % len(cases)]
        add_event(db,f"E{eid:05d}","VEHICLE_SIGHTING",v,p,tm,le.latitude,le.longitude,None,None,
                  f"CAM-{1000+i}",{"location_id":loc,"case_number":case})
        eid+=1

    # ---- Meetings, location events and case references ----
    for i in range(140):
        p1=people[(i*3)%len(people)]; p2=people[(i*13+7)%len(people)]
        if p1==p2: p2=people[(people.index(p1)+2)%len(people)]
        loc=locations[(i*11)%len(locations)]
        le=db.query(Entity).filter_by(external_id=loc).first()
        tm=BASE_TIME+timedelta(hours=rng.randrange(0,24*30))
        case=cases[(i*7) % len(cases)]
        add_event(db,f"E{eid:05d}","MEETING",p1,p2,tm,le.latitude,le.longitude,None,None,
                  f"SURV-{2000+i}",{"location_id":loc,"case_number":case})
        eid+=1

    # Fix the one generated event id typo safely by deleting malformed object if present.
    bad=db.query(Event).filter(Event.event_id.like("E%]")).all()
    for b in bad: db.delete(b)
    db.flush()

    for i in range(180):
        p=people[(i*5+1)%len(people)]; loc=locations[(i*7+2)%len(locations)]
        le=db.query(Entity).filter_by(external_id=loc).first()
        tm=BASE_TIME+timedelta(hours=rng.randrange(0,24*30))
        case=cases[(i*11) % len(cases)]
        add_event(db,f"E{eid:05d}","LOCATION_EVENT",p,loc,tm,le.latitude,le.longitude,None,None,
                  f"SURV-LOC-{i:04d}",{"location_id":loc,"case_number":case})
        eid+=1

    # FIR/case-linked records.
    for i,p in enumerate(people[:35]):
        case=cases[i%len(cases)]
        fid=f"F{i+1:03d}"
        add_entity(db,fid,f"FIR-{100+i}","CASE",attrs={"case_number":case,"police_station":"Synthetic Station"})
        add_rel(db,p,fid,"MENTIONED_IN",f"FIR-{100+i}",BASE_TIME+timedelta(days=10+i%15),.91,{"case_number":case})

    # ---- Seed alerts and tasks ----
    db.flush()
    alert_specs=[
        ("AL-001","High","Conflicting vehicle association","P003",.91,cases[0],
         ["Vehicle V001 is linked to multiple users","Two independent vehicle records disagree","Underlying source records are available"],
         ["VEH-77","VEH-91"]),
        ("AL-002","High","Unusual transaction chain","A001",.88,cases[1],
         ["High-value transaction burst","Funds move across multiple accounts in a short window","Transaction records are available"],
         ["BANK-00000","BANK-00017","BANK-00029"]),
        ("AL-003","Review","Bridge position in communication network","P001",.79,cases[0],
         ["High network importance","Connects otherwise separated communication clusters","CDR source records are available"],
         ["CDR-100000","CDR-100013"]),
        ("AL-004","Review","Recurring late-hour communication burst","P003",.76,cases[2],
         ["Repeated communication events","Late-hour activity cluster","CDR records require human review"],
         ["CDR-100029","CDR-100087"])
    ]
    for code,sev,title,eid0,conf,case_number,fac,refs in alert_specs:
        db.add(Alert(code=code,severity=sev,title=title,status="New",confidence=conf,entity_id=eid0,case_number=case_number,
                     explanation=json.dumps(fac),source_refs=json.dumps(refs),model_version="synthetic-rules-v2"))
    for i,cn in enumerate(cases[:6],1):
        db.add(Task(case_number=cn,title=["Verify vehicle relationship","Review transaction chain","Validate CDR bridge","Check location sequence"][i%4],
                    assignee="investigator",status="Open"))

    # ---- Built-in evidence vault ----
    evidence_dir=settings.evidence_path; evidence_dir.mkdir(parents=True, exist_ok=True)
    docs={
        "case_2026_101_fir.txt": """SYNTHETIC FIR RECORD — CASE-2026-101
Subject references: P001 Aarav Mehta, P003 Kabir Shah, vehicle V001 DL02AB1001.
The record contains authorized synthetic observations only. Vehicle V001 appears in two independent records associated with P003 and P004.
Source references: FIR-100, VEH-77, VEH-91.
""",
        "case_2026_101_cdr.txt": """SYNTHETIC CDR EXTRACT — CASE-2026-101
P001 communicates with P021; P021 communicates with P041; P041 communicates with P061.
P003 communicates with P004 and P006 during the review period.
Source references: CDR-100000, CDR-100013, CDR-100029.
""",
        "case_2026_101_bank.txt": """SYNTHETIC FINANCIAL RECORD — CASE-2026-101
A001 transfers funds to A021; A021 transfers funds to A041; A041 is associated with P061.
Large transactions are synthetic test records intended to exercise anomaly detection.
Source references: BANK-00000, BANK-00017, BANK-00029.
"""
    }
    for fn,content in docs.items():
        path=evidence_dir/fn; data=content.encode(); path.write_bytes(data)
        digest=hashlib.sha256(data).hexdigest()
        db.add(Evidence(evidence_id="EV-"+digest[:12].upper(),filename=fn,media_type="text/plain",
                        source="Synthetic case evidence",sha256=digest,stored_path=(str((evidence_dir/fn).relative_to(ROOT)) if str(evidence_dir).startswith(str(ROOT)) else str(evidence_dir/fn)),
                        uploaded_by="seed_demo",integrity_status="pending",case_number=cases[0],document_id="SEED-"+fn))

    db.commit()

    print("DARK CRIMENET synthetic dataset ready.")
    print(f"Entities: {db.query(Entity).count():,}")
    print(f"Relationships: {db.query(Relationship).count():,}")
    print(f"Events: {db.query(Event).count():,}")
    print(f"Cases: {db.query(Case).count():,}")
    print(f"Alerts: {db.query(Alert).count():,}")
    print(f"Evidence: {db.query(Evidence).count():,}")
    db.close()

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--reset",action="store_true",help="DESTRUCTIVE: drop every table and delete stored evidence (demo env only)")
    ap.add_argument("--yes-delete-everything",action="store_true",help="required together with --reset")
    args=ap.parse_args()
    if args.reset and not args.yes_delete_everything:
        raise SystemExit("--reset deletes all data. Re-run with --reset --yes-delete-everything to confirm.")
    main(args.reset)
