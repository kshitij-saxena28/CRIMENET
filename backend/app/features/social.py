"""Social media intelligence: a lawful open-source-intelligence workbench.

Officers IMPORT public material they collected lawfully (pasted text, JSON/CSV exports, manual items). The tool never scrapes, logs into a
platform, or contacts one. Each import records the source, who collected it, when, the legal basis / authority note (required) and a SHA-256 of the
raw import. From the material the module extracts handles, hashtags, links, phones, e-mails, UPI IDs, crypto wallets, places and languages;
suggests (never silently makes) links to case entities; and offers descriptive analysis (activity, interaction network, coordinated activity,
cross-account hints) plus a transparent, multilingual content-flag lexicon with human review.

Nothing here judges a person. Flags mean "this wording deserves a human look", every flag lists what matched and why it might be wrong.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, UniqueConstraint

from ai_engine.social import analysis as an
from ai_engine.social import extract as ex
from ai_engine.social import ingest
from ai_engine.social import lexicon as lex
from ai_engine.social.timeutil import fmt_local, iso_utc, parse_ts, zone_label
from backend.app.db.database import Base
from security.rbac import allowed

SAMPLE_PATH = Path(__file__).resolve().parents[3] / "demo_dataset" / "social" / "sample_posts.json"
MAX_CONTENT = 3_000_000
REVIEW_STATUSES = ("relevant", "false_positive", "reviewed", "unreviewed")
SAMPLE_BASIS = "SYNTHETIC SAMPLE DATA supplied with the software. No real persons or accounts. For training and demonstration only."
LAWFUL_NOTICE = ("Import only public material you collected lawfully and are authorised to hold for this case. This tool never scrapes platforms or "
                 "logs in to accounts; do not use it to bypass a platform's access controls. Record the authority in the legal-basis field.")
LIMITS = [
    "Only what you import is analysed. Absence of a post, account or link in the results says nothing about whether it exists.",
    "Timestamps, follower counts and text are as exported by whoever collected them; the tool cannot check they are complete or unedited.",
    "Content flags are wording matches from a published lexicon. They miss paraphrase and slang and they fire on quotation, satire, song lyrics and news.",
    "Link suggestions and attribution hints show shared identifiers, not identity. Confirm through lawful process before treating accounts as one person.",
    "Language detection is a script/word heuristic and is unreliable on very short or mixed-language posts.",
    "URL shorteners are flagged but not expanded: the analysis never follows a link.",
    "Automatic collection sees only what an official API or public feed returns for the key your unit holds. It cannot see private accounts, deleted posts or anything the platform withholds, and a quiet search proves nothing.",
]


# ----------------------------------------------------------------------------------------------------------------- models
class SocialImport(Base):
    __tablename__ = "soc_imports"
    id = Column(Integer, primary_key=True)
    import_id = Column(String(40), unique=True, index=True)
    case_number = Column(String(100), index=True)
    source = Column(String(300), default="")
    source_type = Column(String(30), default="paste")
    filename = Column(String(255), default="")
    legal_basis = Column(Text, default="")
    note = Column(Text, default="")
    collected_by = Column(String(100), default="")
    collected_at = Column(DateTime, nullable=True)
    imported_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    sha256 = Column(String(64), index=True)
    bytes_in = Column(Integer, default=0)
    assumed_timezone = Column(String(20), default="IST")
    n_posts = Column(Integer, default=0)
    n_accounts = Column(Integer, default=0)
    n_duplicates = Column(Integer, default=0)
    n_flags = Column(Integer, default=0)
    warnings_json = Column(Text, default="[]")


class SocialAccount(Base):
    __tablename__ = "soc_accounts"
    __table_args__ = (UniqueConstraint("case_number", "platform", "handle", name="uq_soc_account"),)
    id = Column(Integer, primary_key=True)
    case_number = Column(String(100), index=True)
    platform = Column(String(30), index=True)
    handle = Column(String(80), index=True)
    display_name = Column(String(150), default="")
    bio = Column(Text, default="")
    followers = Column(Integer, nullable=True)
    following = Column(Integer, nullable=True)
    profile_url = Column(String(500), default="")
    location_text = Column(String(200), default="")
    extracted_json = Column(Text, default="{}")
    first_import_id = Column(String(40), default="")
    last_import_id = Column(String(40), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class SocialPost(Base):
    __tablename__ = "soc_posts"
    __table_args__ = (UniqueConstraint("case_number", "post_uid", name="uq_soc_post"),)
    id = Column(Integer, primary_key=True)
    post_uid = Column(String(40), index=True)
    case_number = Column(String(100), index=True)
    import_id = Column(String(40), index=True)
    account_id = Column(Integer, index=True)
    platform = Column(String(30), index=True)
    handle = Column(String(80), index=True)
    post_id = Column(String(80), default="")
    url = Column(String(500), default="")
    posted_at = Column(DateTime, nullable=True, index=True)
    text = Column(Text, default="")
    likes = Column(Integer, nullable=True)
    shares = Column(Integer, nullable=True)
    replies = Column(Integer, nullable=True)
    reply_to = Column(String(80), default="")
    repost_of = Column(String(80), default="")
    location_text = Column(String(200), default="")
    media_json = Column(Text, default="[]")
    lang = Column(String(20), default="")
    extracted_json = Column(Text, default="{}")
    text_sha256 = Column(String(64), default="")


class SocialFlag(Base):
    __tablename__ = "soc_flags"
    __table_args__ = (UniqueConstraint("case_number", "item_type", "item_ref", "category", name="uq_soc_flag"),)
    id = Column(Integer, primary_key=True)
    case_number = Column(String(100), index=True)
    item_type = Column(String(10), default="post")  # post | account (bio)
    item_ref = Column(String(40), index=True)  # post_uid or account id
    category = Column(String(40), index=True)
    phrase = Column(String(200), default="")
    rule = Column(String(60), default="")
    lang = Column(String(20), default="")
    score = Column(Float, default=0.0)
    level = Column(String(10), default="")
    context_json = Column(Text, default="[]")
    import_id = Column(String(40), default="")
    review_status = Column(String(20), default="unreviewed", index=True)
    review_note = Column(Text, default="")
    reviewed_by = Column(String(100), default="")
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class SocialFlagReview(Base):
    """Append-only history of review decisions on a flag."""
    __tablename__ = "soc_flag_reviews"
    id = Column(Integer, primary_key=True)
    case_number = Column(String(100), index=True)
    flag_id = Column(Integer, index=True)
    status = Column(String(20))
    note = Column(Text, default="")
    reviewer = Column(String(100), default="")
    at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class SocialLink(Base):
    __tablename__ = "soc_links"
    id = Column(Integer, primary_key=True)
    case_number = Column(String(100), index=True)
    account_id = Column(Integer, index=True)
    entity_id = Column(String(100), index=True)
    social_entity_id = Column(String(100), default="")
    match_type = Column(String(20), default="")
    relationship_id = Column(Integer, nullable=True)
    source_ref = Column(String(120), default="")
    note = Column(Text, default="")
    accepted_by = Column(String(100), default="")
    accepted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


# ----------------------------------------------------------------------------------------------------------------- inputs
class ImportIn(BaseModel):
    source: str = Field(min_length=2, max_length=300)
    legal_basis: str = Field(min_length=10, max_length=1000)
    content: str = Field(default="", max_length=MAX_CONTENT)
    records: list[dict] = Field(default_factory=list, max_length=500)
    filename: str = Field(default="", max_length=255)
    source_type: str = Field(default="paste", max_length=30)
    assumed_timezone: str = Field(default="IST", max_length=10)
    default_platform: str = Field(default="", max_length=30)
    default_handle: str = Field(default="", max_length=80)
    collected_at: str | None = Field(default=None, max_length=40)
    note: str = Field(default="", max_length=1000)


class SampleIn(BaseModel):
    seed_entities: bool = False


class ReviewIn(BaseModel):
    status: str = Field(max_length=20)
    note: str = Field(default="", max_length=500)


class AcceptIn(BaseModel):
    account_id: int
    entity_id: str = Field(min_length=1, max_length=100)
    match_type: str = Field(min_length=2, max_length=20)
    note: str = Field(default="", max_length=500)


# ----------------------------------------------------------------------------------------------------------------- helpers
def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _jl(text, default):
    try:
        v = json.loads(text or "")
        return v if isinstance(v, type(default)) else default
    except (ValueError, TypeError):
        return default


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _masked(user) -> bool:
    return not allowed(user["role"], "sensitive_read")


def _mask_ex(e: dict) -> dict:
    """Mask identifier values inside an extraction dict (for roles without sensitive_read)."""
    e = dict(e)
    e["phones"] = [ex.mask_value("phone", p) for p in e.get("phones", [])]
    e["emails"] = [ex.mask_value("email", p) for p in e.get("emails", [])]
    e["upi_ids"] = [ex.mask_value("upi", p) for p in e.get("upi_ids", [])]
    e["wallets"] = [{**w, "value": ex.mask_value("wallet", w["value"])} for w in e.get("wallets", [])]
    return e


def _url_keys(extracted: dict) -> list[str]:
    return list(dict.fromkeys(ex.normalise_url(u["url"]) for u in extracted.get("urls", []) if u.get("url")))


def _post_uid(cn, rec) -> str:
    base = "|".join([cn, rec["platform"], rec["handle"], rec["post_id"] or (iso_utc(rec["ts"]) + "|" + hashlib.sha256(rec["text"].encode()).hexdigest())])
    return "p_" + _sha(base.encode())[:14]


def _account_ref(a) -> str:
    return f"{a.platform}:@{a.handle}"


def _post_dict(p: SocialPost, masked: bool, flags=None, acct=None) -> dict:
    e = _jl(p.extracted_json, {})
    text = ex.mask_text(p.text) if masked else p.text
    if masked:
        e = _mask_ex(e)
    return {"id": p.id, "uid": p.post_uid, "import_id": p.import_id, "account_id": p.account_id, "platform": p.platform, "handle": p.handle, "post_id": p.post_id,
            "url": p.url, "posted_at": iso_utc(p.posted_at) or None, "posted_at_ist": fmt_local(p.posted_at, "IST") or None, "text": text,
            "likes": p.likes, "shares": p.shares, "replies": p.replies, "reply_to": p.reply_to, "repost_of": p.repost_of, "location_text": p.location_text,
            "media": _jl(p.media_json, []), "lang": p.lang, "extracted": e, "text_sha256": p.text_sha256,
            "flags": [_flag_dict(f, masked) for f in (flags or [])]}


def _flag_dict(f: SocialFlag, masked: bool = False, post_text=None) -> dict:
    lab = lex.CATEGORIES.get(f.category, {})
    d = {"id": f.id, "item_type": f.item_type, "item_ref": f.item_ref, "category": f.category, "label": lab.get("label", f.category), "severity": lab.get("severity", ""),
         "phrase": ex.mask_text(f.phrase) if masked else f.phrase, "rule": f.rule, "lang": f.lang, "score": f.score, "level": f.level,
         "why": lab.get("why", ""), "context_notes": _jl(f.context_json, []), "caveat": lex.CAVEAT, "review_status": f.review_status, "review_note": f.review_note,
         "reviewed_by": f.reviewed_by, "reviewed_at": iso_utc(f.reviewed_at) or None, "import_id": f.import_id}
    if post_text is not None:
        d["text"] = ex.mask_text(post_text) if masked else post_text
    return d


def _account_dict(a: SocialAccount, masked: bool, stats: dict | None = None) -> dict:
    e = _jl(a.extracted_json, {})
    bio = ex.mask_text(a.bio) if masked else a.bio
    if masked:
        e = _mask_ex(e)
    s = stats or {}
    return {"id": a.id, "ref": _account_ref(a), "platform": a.platform, "handle": a.handle, "display_name": a.display_name, "bio": bio, "followers": a.followers,
            "following": a.following, "profile_url": a.profile_url, "location_text": a.location_text, "extracted": e, "posts": s.get("posts", 0), "flags": s.get("flags", 0),
            "first_post": s.get("first"), "last_post": s.get("last"), "import_id": a.last_import_id}


def _load_case(ctx, db, user, case, *, perm_name=None):
    return ctx.scope(db, user, case)


# ----------------------------------------------------------------------------------------------------------------- import
def _merge_extraction(text: str, rec: dict) -> dict:
    e = ex.extract(text)
    for m in rec.get("mentions", []):
        if m and m not in e["mentions"]:
            e["mentions"].append(m)
    if rec.get("location"):
        for p in ex.find_places(rec["location"]):
            if p["name"] not in [x["name"] for x in e["places"]]:
                e["places"].append({**p, "from_field": True})
    return e


def run_import(ctx, db, user, cn: str, records: list[dict], meta: dict) -> dict:
    """Store parsed records, extract, flag. ``meta``: source, source_type, filename, legal_basis, note, sha256, bytes_in, assumed_timezone, collected_at, warnings."""
    import_id = "IMP-" + _sha((cn + meta["sha256"] + iso_utc(_now()) + user["sub"] + uuid.uuid4().hex).encode())[:10].upper()
    existing_accts = {(a.platform, a.handle): a for a in db.query(SocialAccount).filter_by(case_number=cn).all()}
    n_new_posts = n_dup = n_new_accts = 0
    flagged_posts: list[tuple[str, list[dict]]] = []
    flag_rows = 0
    seen_uids = {u for (u,) in db.query(SocialPost.post_uid).filter_by(case_number=cn).all()}

    def get_account(rec):
        nonlocal n_new_accts
        key = (rec["platform"], rec["handle"])
        a = existing_accts.get(key)
        if a is None:
            a = SocialAccount(case_number=cn, platform=rec["platform"], handle=rec["handle"], first_import_id=import_id)
            db.add(a)
            existing_accts[key] = a
            n_new_accts += 1
        return a

    def update_account(a, rec):
        for src, dst in (("display_name", "display_name"), ("bio", "bio"), ("profile_url", "profile_url")):
            if rec.get(src):
                setattr(a, dst, rec[src])
        for k in ("followers", "following"):
            if rec.get(k) is not None:
                setattr(a, k, rec[k])
        if rec.get("location") and not rec["is_post"]:
            a.location_text = rec["location"]
        a.last_import_id = import_id
        if rec.get("bio"):
            a.extracted_json = json.dumps(_merge_extraction(a.bio + " " + (a.display_name or ""), {}), ensure_ascii=False)

    for rec in records:
        a = get_account(rec)
        if rec["is_account"]:
            update_account(a, rec)
        else:
            a.last_import_id = a.last_import_id or import_id
    db.flush()
    # account bios: flags
    for a in existing_accts.values():
        if a.bio and a.last_import_id == import_id:
            for f in lex.flag_text(a.bio):
                if not db.query(SocialFlag).filter_by(case_number=cn, item_type="account", item_ref=str(a.id), category=f["category"]).first():
                    db.add(SocialFlag(case_number=cn, item_type="account", item_ref=str(a.id), category=f["category"], phrase=f["phrase"], rule=f["rule"], lang=f["lang"],
                                      score=f["score"], level=f["level"], context_json=json.dumps(f["context_notes"]), import_id=import_id))
                    flag_rows += 1
    for rec in records:
        if not rec["is_post"]:
            continue
        uid = _post_uid(cn, rec)
        if uid in seen_uids:
            n_dup += 1
            continue
        seen_uids.add(uid)
        a = get_account(rec)
        e = _merge_extraction(rec["text"], rec)
        db.add(SocialPost(post_uid=uid, case_number=cn, import_id=import_id, account_id=a.id, platform=rec["platform"], handle=rec["handle"], post_id=rec["post_id"],
                          url=rec["url"], posted_at=rec["ts"], text=rec["text"], likes=rec["likes"], shares=rec["shares"], replies=rec["replies"], reply_to=rec["reply_to"],
                          repost_of=rec["repost_of"], location_text=rec["location"], media_json=json.dumps(rec["media"]), lang=e["language"]["code"],
                          extracted_json=json.dumps(e, ensure_ascii=False, default=str), text_sha256=_sha(rec["text"].encode())))
        n_new_posts += 1
        fl = lex.flag_text(rec["text"])
        for f in fl:
            db.add(SocialFlag(case_number=cn, item_type="post", item_ref=uid, category=f["category"], phrase=f["phrase"], rule=f["rule"], lang=f["lang"], score=f["score"],
                              level=f["level"], context_json=json.dumps(f["context_notes"]), import_id=import_id))
            flag_rows += 1
        if fl:
            flagged_posts.append((uid, fl))
    row = SocialImport(import_id=import_id, case_number=cn, source=meta["source"], source_type=meta["source_type"], filename=meta.get("filename", ""),
                       legal_basis=meta["legal_basis"], note=meta.get("note", ""), collected_by=user["sub"], collected_at=meta.get("collected_at") or _now(),
                       imported_at=_now(), sha256=meta["sha256"], bytes_in=meta["bytes_in"], assumed_timezone=meta["assumed_timezone"], n_posts=n_new_posts,
                       n_accounts=n_new_accts, n_duplicates=n_dup, n_flags=flag_rows, warnings_json=json.dumps(meta.get("warnings", [])))
    db.add(row)
    db.commit()
    detail = {"import_id": import_id, "source_type": meta["source_type"], "sha256": meta["sha256"], "posts": n_new_posts, "accounts": n_new_accts,
              "duplicates": n_dup, "flags": flag_rows, "legal_basis_len": len(meta["legal_basis"])}
    ctx.audit(db, user["sub"], "social.imported", {"case_number": cn, **detail, "source": meta["source"][:120]})
    ctx.hooks.emit("social_imported", db=db, user=user, case_number=cn, ref=import_id, detail=detail)
    for uid, fl in flagged_posts[:25]:
        ctx.hooks.emit("social_item_flagged", db=db, user=user, case_number=cn, ref=uid,
                       detail={"action": "created", "import_id": import_id, "categories": [f["category"] for f in fl], "max_score": max(f["score"] for f in fl)})
    return {"import_id": import_id, "posts_added": n_new_posts, "duplicates_skipped": n_dup, "accounts_added": n_new_accts, "flags_raised": flag_rows,
            "sha256": meta["sha256"], "warnings": meta.get("warnings", []),
            "message": f"Imported {n_new_posts} post(s) and {n_new_accts} new account(s); {flag_rows} wording flag(s) need review. Flags are prompts for a human look, not findings."}


# ----------------------------------------------------------------------------------------------------------------- link suggestions
_EMAIL_RX = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
_ATTR_KIND = {"phone": "phone", "mobile": "phone", "msisdn": "phone", "email": "email", "mail": "email", "upi": "upi", "upi_id": "upi", "vpa": "upi",
              "handle": "handle", "username": "handle", "alias": "handle", "social": "handle"}


def _entity_keys(ent: dict) -> list[tuple[str, str]]:
    keys = []
    typ = str(ent.get("type", "")).upper()
    name = str(ent.get("name") or "")
    vals = [("name", name)] + [(str(k).lower(), str(v)) for k, v in (ent.get("attributes") or {}).items() if isinstance(v, (str, int)) and v != ""]
    for k, v in vals:
        kind = _ATTR_KIND.get(k)
        vs = v.strip()
        if k == "name":
            if typ == "PHONE" or ex.phone_key(vs) and re.fullmatch(r"[+\d\s\-()]{10,16}", vs):
                if ex.phone_key(vs):
                    keys.append(("phone", ex.phone_key(vs)))
                continue
            if _EMAIL_RX.match(vs):
                keys.append(("email", vs.lower()))
                continue
            m = re.fullmatch(r"([\w.\-]{2,49})@(" + "|".join(ex.UPI_HANDLES) + ")", vs, re.I)
            if m:
                keys.append(("upi", vs.lower()))
                continue
            if typ in ("PERSON",) and len(vs.split()) >= 2 and len(vs) >= 5:
                keys.append(("name", re.sub(r"\s*\(.*?\)\s*", " ", vs).strip().lower()))
            elif typ == "DIGITAL_IDENTIFIER" and vs.startswith("@"):
                keys.append(("handle", vs.lstrip("@").split()[0].lower()))
            continue
        if kind == "phone" and ex.phone_key(vs):
            keys.append(("phone", ex.phone_key(vs)))
        elif kind == "email" and _EMAIL_RX.match(vs):
            keys.append(("email", vs.lower()))
        elif kind == "upi" and "@" in vs:
            keys.append(("upi", vs.lower()))
        elif kind == "handle" and len(vs) >= 3:
            keys.append(("handle", vs.lstrip("@").lower()))
    return list(dict.fromkeys(keys))


def build_suggestions(db, cn: str, entities: list[dict], masked: bool, account_id: int | None = None) -> list[dict]:
    idx: dict[tuple[str, str], list[dict]] = defaultdict(list)
    names: list[tuple[str, dict]] = []
    for ent in entities:
        if str(ent.get("attributes", {}).get("kind", "")) == "social_account":
            continue
        for kind, val in _entity_keys(ent):
            (names.append((val, ent)) if kind == "name" else idx[(kind, val)].append(ent))
    q = db.query(SocialAccount).filter_by(case_number=cn)
    if account_id:
        q = q.filter_by(id=account_id)
    accounts = q.all()
    linked = {(l.account_id, l.entity_id, l.match_type) for l in db.query(SocialLink).filter_by(case_number=cn).all()}
    out: dict[tuple, dict] = {}

    def add(acct, ent, mtype, matched, where, post_uid, snippet, strength):
        key = (acct.id, ent["external_id"], mtype)
        conf = {"strong": 0.5, "medium": 0.4, "weak": 0.25}[strength] + (0.05 if where == "bio" else 0)
        cur = out.get(key)
        if cur:
            cur["evidence_count"] += 1
            if where == "bio" and cur["where"] != "bio":
                cur.update(where="bio", post_uid=None, snippet=snippet, confidence=round(conf, 2))
            return
        shown_name = ent.get("name", "")
        if masked and str(ent.get("type", "")).upper() in ("PHONE", "ACCOUNT", "EMAIL", "DIGITAL_IDENTIFIER", "VEHICLE"):
            from security.masking import mask_identifier
            shown_name = mask_identifier(shown_name)
        mt = matched
        if masked and mtype in ("phone", "email", "upi"):
            mt = ex.mask_value(mtype, matched)
        out[key] = {"id": hashlib.sha1("|".join(map(str, key)).encode()).hexdigest()[:16], "account_id": acct.id, "account": _account_ref(acct), "display_name": acct.display_name,
                    "entity_id": ent["external_id"], "entity_type": ent.get("type"), "entity_name": shown_name, "match_type": mtype, "matched_text": mt, "where": where,
                    "post_uid": post_uid, "snippet": ex.mask_text(snippet) if masked else snippet, "strength": strength, "confidence": round(conf, 2), "evidence_count": 1,
                    "status": "linked" if key in linked else "suggested",
                    "reason": {"phone": "The same phone number appears in the entity and in the social account's " + ("bio" if where == "bio" else "post") + ".",
                               "email": "The same e-mail address appears in the entity and in the social account's " + ("bio" if where == "bio" else "post") + ".",
                               "upi": "The same UPI ID appears in the entity and in the social account's " + ("bio" if where == "bio" else "post") + ".",
                               "handle": "A case entity records this handle as an alias or username.",
                               "name": "The entity's full name appears in the account's display name or bio. Names are weak evidence: many people share a name."}[mtype]}

    def scan(acct, e, where, post_uid, text):
        for p in e.get("phones", []):
            for ent in idx.get(("phone", ex.phone_key(p)), []):
                add(acct, ent, "phone", p, where, post_uid, text[:160], "strong")
        for m in e.get("emails", []):
            for ent in idx.get(("email", m.lower()), []):
                add(acct, ent, "email", m, where, post_uid, text[:160], "strong")
        for u in e.get("upi_ids", []):
            for ent in idx.get(("upi", u.lower()), []):
                add(acct, ent, "upi", u, where, post_uid, text[:160], "strong")

    for a in accounts:
        for ent in idx.get(("handle", a.handle), []):
            add(a, ent, "handle", "@" + a.handle, "bio", None, f"{a.display_name} @{a.handle}", "medium")
        prof_text = f"{a.display_name} {a.bio}".strip()
        scan(a, _jl(a.extracted_json, {}), "bio", None, prof_text)
        low = re.sub(r"\s+", " ", prof_text.lower())
        for val, ent in names:
            if re.search(r"(?<!\w)" + re.escape(val) + r"(?!\w)", low):
                add(a, ent, "name", ent.get("name", val), "bio", None, prof_text[:160], "weak")
    aid = {a.id for a in accounts}
    if aid:
        for p in db.query(SocialPost).filter(SocialPost.case_number == cn, SocialPost.account_id.in_(aid)).all():
            e = _jl(p.extracted_json, {})
            if e.get("phones") or e.get("emails") or e.get("upi_ids"):
                acct = next(a for a in accounts if a.id == p.account_id)
                scan(acct, e, "post", p.post_uid, p.text)
    rows = list(out.values())
    order = {"strong": 0, "medium": 1, "weak": 2}
    rows.sort(key=lambda r: (r["status"] == "linked", order[r["strength"]], -r["confidence"], r["account"]))
    return rows


# ----------------------------------------------------------------------------------------------------------------- analysis inputs
def _analysis_inputs(db, cn: str, limit: int = 20000):
    accs = {a.id: a for a in db.query(SocialAccount).filter_by(case_number=cn).all()}
    posts = []
    ids_by_acct: dict[int, list] = defaultdict(list)
    for p in db.query(SocialPost).filter_by(case_number=cn).order_by(SocialPost.posted_at.asc()).limit(limit).all():
        e = _jl(p.extracted_json, {})
        posts.append({"uid": p.post_uid, "account": f"{p.platform}:{p.handle}", "platform": p.platform, "handle": p.handle, "ts": p.posted_at, "text": p.text,
                      "mentions": e.get("mentions", []), "reply_to": p.reply_to, "repost_of": p.repost_of, "hashtags": e.get("hashtags", []), "urls": _url_keys(e),
                      "lang": p.lang})
        for kind, val in ex.identifiers(e):
            ids_by_acct[p.account_id].append((kind, val, "post"))
        for u in _url_keys(e):
            if "/" in u or u.split("/")[0] not in an.COMMON_DOMAINS:
                ids_by_acct[p.account_id].append(("url", u, "post"))
    accounts = []
    for a in accs.values():
        e = _jl(a.extracted_json, {})
        ids = [(k, v, "bio") for k, v in ex.identifiers(e)] + [("url", _u, "bio") for _u in _url_keys(e)] + ids_by_acct.get(a.id, [])
        accounts.append({"key": f"{a.platform}:{a.handle}", "platform": a.platform, "handle": a.handle, "display_name": a.display_name, "bio": a.bio, "ids": ids})
    return posts, accounts


# ----------------------------------------------------------------------------------------------------------------- API
def build(ctx):
    router = APIRouter(prefix="/social", tags=["social"])
    perm = ctx.require_perm

    def case_of(db, user, case):
        return ctx.scope(db, user, case)

    # ---------------- transparency
    @router.get("/lexicon")
    def lexicon(user=Depends(perm("read"))):
        return {**lex.lexicon_listing(), "limits": LIMITS}

    @router.get("/evaluation")
    def evaluation(user=Depends(perm("read"))):
        from ai_engine.social.evaluate import evaluate
        out = {}
        for sp in ("dev", "heldout"):
            r = evaluate(sp)
            r.pop("errors", None)
            out[sp] = r
        out["note"] = ("Measured on a small synthetic corpus written by the developers. 'dev' posts were used while writing the lexicon (optimistic); 'heldout' posts "
                       "were written afterwards and never used to tune it (a fairer, but still small and synthetic, estimate). Real-world accuracy will be lower.")
        return out

    # ---------------- import
    @router.post("/{case}/import")
    def import_material(case: str, x: ImportIn, db=Depends(ctx.get_db), user=Depends(perm("write"))):
        cn = case_of(db, user, case)
        if len(x.legal_basis.split()) < 3:
            raise HTTPException(400, "State the legal basis or authority for collecting this material (at least a short sentence).")
        if not x.content.strip() and not x.records:
            raise HTTPException(400, "Paste some text, upload a JSON/CSV export or add an item to import.")
        if x.source_type not in ("paste", "json", "csv", "manual", "upload", "sample"):
            raise HTTPException(400, "Unknown source_type")
        defaults = {"platform": x.default_platform, "handle": x.default_handle}
        recs, warns, kind = ingest.parse_content(x.content, x.assumed_timezone, defaults) if x.content.strip() else ([], [], "manual")
        for raw in x.records:
            r = ingest.normalise_record(raw, x.assumed_timezone, defaults)
            if r:
                r["handle"] = r["handle"] or "unknown"
                recs.append(r)
        if not recs:
            raise HTTPException(400, "; ".join(warns) or "No posts or accounts could be read from this material.")
        raw_bytes = x.content.encode("utf-8") + (b"\n" + json.dumps(x.records, sort_keys=True, ensure_ascii=False).encode("utf-8") if x.records else b"")
        collected = parse_ts(x.collected_at, x.assumed_timezone) if x.collected_at else None
        meta = {"source": x.source.strip(), "source_type": x.source_type, "filename": x.filename,
                "legal_basis": re.sub(r"\s+", " ", x.legal_basis).strip(), "note": x.note, "sha256": _sha(raw_bytes), "bytes_in": len(raw_bytes),
                "assumed_timezone": zone_label(x.assumed_timezone), "collected_at": collected, "warnings": warns}
        res = run_import(ctx, db, user, cn, recs, meta)
        res["detected_format"] = kind
        return res

    @router.post("/{case}/import/sample")
    def import_sample(case: str, x: SampleIn, db=Depends(ctx.get_db), user=Depends(perm("write"))):
        cn = case_of(db, user, case)
        try:
            raw = SAMPLE_PATH.read_text(encoding="utf-8")
        except OSError:
            raise HTTPException(404, "The bundled sample file is missing")
        recs, warns, kind = ingest.parse_content(raw, "IST", None)
        meta = {"source": "Bundled synthetic sample (demo_dataset/social/sample_posts.json)", "source_type": "sample", "filename": "sample_posts.json", "legal_basis": SAMPLE_BASIS,
                "note": "Synthetic people and accounts only.", "sha256": _sha(raw.encode("utf-8")), "bytes_in": len(raw.encode("utf-8")), "assumed_timezone": "IST",
                "collected_at": None, "warnings": warns}
        res = run_import(ctx, db, user, cn, recs, meta)
        seeded = []
        if x.seed_entities:
            for spec in _json_seed(raw):
                try:
                    r = ctx.svc.add_entity(db, {"external_id": f"SOCSAMPLE-{_sha((cn + spec['name']).encode())[:8].upper()}", "name": spec["name"], "entity_type": spec["type"],
                                                "confidence": 0.5, "attributes": {**(spec.get("attributes") or {}), "synthetic": True, "source": "social-sample"}}, user["sub"], case_number=cn)
                    seeded.append({"name": spec["name"], "type": spec["type"], "status": r.get("status")})
                except ValueError:
                    pass
        res["seeded_entities"] = seeded
        return res

    @router.get("/{case}/imports")
    def imports(case: str, db=Depends(ctx.get_db), user=Depends(perm("read"))):
        cn = case_of(db, user, case)
        rows = db.query(SocialImport).filter_by(case_number=cn).order_by(SocialImport.id.desc()).limit(200).all()
        return {"case_number": cn, "notice": LAWFUL_NOTICE, "imports": [
            {"import_id": r.import_id, "source": r.source, "source_type": r.source_type, "filename": r.filename, "legal_basis": r.legal_basis, "note": r.note, "collected_by": r.collected_by,
             "collected_at": iso_utc(r.collected_at) or None, "imported_at": iso_utc(r.imported_at), "sha256": r.sha256, "posts": r.n_posts, "accounts": r.n_accounts,
             "duplicates_skipped": r.n_duplicates, "flags": r.n_flags, "assumed_timezone": r.assumed_timezone, "warnings": _jl(r.warnings_json, [])} for r in rows]}

    # ---------------- read views
    @router.get("/{case}/summary")
    def summary(case: str, db=Depends(ctx.get_db), user=Depends(perm("read"))):
        cn = case_of(db, user, case)
        masked = _masked(user)
        n_posts = db.query(SocialPost).filter_by(case_number=cn).count()
        n_acc = db.query(SocialAccount).filter_by(case_number=cn).count()
        flags = db.query(SocialFlag).filter_by(case_number=cn).all()
        by_cat = Counter(f.category for f in flags)
        by_status = Counter(f.review_status for f in flags)
        plat = Counter(p for (p,) in db.query(SocialPost.platform).filter_by(case_number=cn).all())
        langs = Counter(l or "unknown" for (l,) in db.query(SocialPost.lang).filter_by(case_number=cn).all())
        per_acct = Counter(a for (a,) in db.query(SocialPost.account_id).filter_by(case_number=cn).all())
        flagged_per_acct = Counter()
        post_flags = {f.item_ref: f for f in flags if f.item_type == "post"}
        by_uid = {}
        tags, places = Counter(), Counter()
        first = last = None
        for p in db.query(SocialPost).filter_by(case_number=cn).all():
            e = _jl(p.extracted_json, {})
            for t in e.get("hashtags", []):
                tags[t] += 1
            for pl in e.get("places", []):
                places[pl["name"]] += 1
            if p.posted_at:
                first = min(first, p.posted_at) if first else p.posted_at
                last = max(last, p.posted_at) if last else p.posted_at
            if p.post_uid in post_flags:
                flagged_per_acct[p.account_id] += 1
            by_uid[p.post_uid] = p
        distinct = defaultdict(set)
        for p in by_uid.values():
            for kind, val in ex.identifiers(_jl(p.extracted_json, {})):
                distinct[kind].add(val)
        accts = {a.id: a for a in db.query(SocialAccount).filter_by(case_number=cn).all()}
        top = [{"id": aid, "ref": _account_ref(accts[aid]), "display_name": accts[aid].display_name, "posts": n, "flagged_posts": flagged_per_acct.get(aid, 0),
                "followers": accts[aid].followers} for aid, n in per_acct.most_common(8) if aid in accts]
        recent = []
        for f in sorted([f for f in flags if f.item_type == "post" and f.review_status == "unreviewed"], key=lambda f: (-f.score, -f.id))[:8]:
            p = by_uid.get(f.item_ref)
            if p:
                recent.append({**_flag_dict(f, masked, p.text), "handle": p.handle, "platform": p.platform, "posted_at": iso_utc(p.posted_at) or None})
        last_import = db.query(SocialImport).filter_by(case_number=cn).order_by(SocialImport.id.desc()).first()
        return {"case_number": cn, "counts": {"posts": n_posts, "accounts": n_acc, "imports": db.query(SocialImport).filter_by(case_number=cn).count(), "flags": len(flags),
                                              "flags_unreviewed": by_status.get("unreviewed", 0), "flags_relevant": by_status.get("relevant", 0),
                                              "flags_false_positive": by_status.get("false_positive", 0)},
                "flags_by_category": [{"category": k, "label": lex.CATEGORIES.get(k, {}).get("label", k), "count": v} for k, v in by_cat.most_common()],
                "platforms": dict(plat), "languages": dict(langs), "top_accounts": top, "top_hashtags": [{"tag": t, "count": c} for t, c in tags.most_common(8)],
                "places": [{"name": t, "count": c} for t, c in places.most_common(8)], "identifiers_found": {k: len(v) for k, v in distinct.items()},
                "date_range": {"first": iso_utc(first) or None, "last": iso_utc(last) or None}, "recent_flags": recent,
                "last_import": ({"import_id": last_import.import_id, "imported_at": iso_utc(last_import.imported_at), "collected_by": last_import.collected_by} if last_import else None),
                "masked": masked, "notice": LAWFUL_NOTICE, "limits": LIMITS}

    @router.get("/{case}/accounts")
    def accounts(case: str, q: str = "", platform: str = "", limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), db=Depends(ctx.get_db), user=Depends(perm("read"))):
        cn = case_of(db, user, case)
        qq = db.query(SocialAccount).filter_by(case_number=cn)
        if platform:
            qq = qq.filter(SocialAccount.platform == ingest.norm_platform(platform))
        if q.strip():
            like = f"%{q.strip().lower()[:80]}%"
            from sqlalchemy import func, or_
            qq = qq.filter(or_(func.lower(SocialAccount.handle).like(like), func.lower(SocialAccount.display_name).like(like), func.lower(SocialAccount.bio).like(like)))
        total = qq.count()
        rows = qq.order_by(SocialAccount.id.desc()).offset(offset).limit(limit).all()
        stats = {}
        for a in rows:
            ps = db.query(SocialPost.posted_at).filter_by(case_number=cn, account_id=a.id).all()
            uids = [u for (u,) in db.query(SocialPost.post_uid).filter_by(case_number=cn, account_id=a.id).all()]
            fl = db.query(SocialFlag).filter(SocialFlag.case_number == cn, SocialFlag.item_type == "post", SocialFlag.item_ref.in_(uids)).count() if uids else 0
            dts = [t for (t,) in ps if t]
            stats[a.id] = {"posts": len(ps), "flags": fl, "first": iso_utc(min(dts)) if dts else None, "last": iso_utc(max(dts)) if dts else None}
        masked = _masked(user)
        return {"case_number": cn, "total": total, "masked": masked, "accounts": [_account_dict(a, masked, stats.get(a.id)) for a in rows]}

    @router.get("/{case}/accounts/{account_id}")
    def account_detail(case: str, account_id: int, db=Depends(ctx.get_db), user=Depends(perm("read"))):
        cn = case_of(db, user, case)
        a = db.query(SocialAccount).filter_by(case_number=cn, id=account_id).first()
        if not a:
            raise HTTPException(404, "Account not found")
        masked = _masked(user)
        posts = db.query(SocialPost).filter_by(case_number=cn, account_id=a.id).order_by(SocialPost.posted_at.desc()).limit(50).all()
        flags = {}
        for f in db.query(SocialFlag).filter(SocialFlag.case_number == cn, SocialFlag.item_type == "post", SocialFlag.item_ref.in_([p.post_uid for p in posts] or [""])).all():
            flags.setdefault(f.item_ref, []).append(f)
        bio_flags = [_flag_dict(f, masked) for f in db.query(SocialFlag).filter_by(case_number=cn, item_type="account", item_ref=str(a.id)).all()]
        dts = [p.posted_at for p in posts if p.posted_at]
        stats = {"posts": db.query(SocialPost).filter_by(case_number=cn, account_id=a.id).count(), "flags": sum(len(v) for v in flags.values()),
                 "first": iso_utc(min(dts)) if dts else None, "last": iso_utc(max(dts)) if dts else None}
        return {**_account_dict(a, masked, stats), "bio_flags": bio_flags, "recent_posts": [_post_dict(p, masked, flags.get(p.post_uid)) for p in posts]}

    @router.get("/{case}/posts")
    def posts(case: str, q: str = "", handle: str = "", platform: str = "", hashtag: str = "", flagged: bool = False, category: str = "", lang: str = "",
              date_from: str = "", date_to: str = "", limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db=Depends(ctx.get_db), user=Depends(perm("read"))):
        cn = case_of(db, user, case)
        from sqlalchemy import func
        qq = db.query(SocialPost).filter_by(case_number=cn)
        if handle:
            qq = qq.filter(SocialPost.handle == ingest.norm_handle(handle))
        if platform:
            qq = qq.filter(SocialPost.platform == ingest.norm_platform(platform))
        if lang:
            qq = qq.filter(SocialPost.lang == lang[:20])
        if q.strip():
            qq = qq.filter(func.lower(SocialPost.text).like(f"%{q.strip().lower()[:100]}%"))
        if hashtag.strip():
            tag = hashtag.strip().lstrip("#").lower()[:60]
            qq = qq.filter(SocialPost.extracted_json.like('%"' + tag.replace("%", "").replace('"', "") + '"%'))
        d1, d2 = parse_ts(date_from, "UTC") if date_from else None, parse_ts(date_to, "UTC") if date_to else None
        if d1:
            qq = qq.filter(SocialPost.posted_at >= d1)
        if d2:
            qq = qq.filter(SocialPost.posted_at <= d2)
        if flagged or category:
            fq = db.query(SocialFlag.item_ref).filter(SocialFlag.case_number == cn, SocialFlag.item_type == "post")
            if category:
                fq = fq.filter(SocialFlag.category == category)
            qq = qq.filter(SocialPost.post_uid.in_([u for (u,) in fq.all()] or [""]))
        total = qq.count()
        rows = qq.order_by(SocialPost.posted_at.desc(), SocialPost.id.desc()).offset(offset).limit(limit).all()
        flags = {}
        for f in db.query(SocialFlag).filter(SocialFlag.case_number == cn, SocialFlag.item_type == "post", SocialFlag.item_ref.in_([p.post_uid for p in rows] or [""])).all():
            flags.setdefault(f.item_ref, []).append(f)
        masked = _masked(user)
        return {"case_number": cn, "total": total, "masked": masked, "posts": [_post_dict(p, masked, flags.get(p.post_uid)) for p in rows]}

    @router.get("/{case}/flags")
    def list_flags(case: str, status: str = "", category: str = "", limit: int = Query(100, ge=1, le=500), db=Depends(ctx.get_db), user=Depends(perm("read"))):
        cn = case_of(db, user, case)
        qq = db.query(SocialFlag).filter_by(case_number=cn)
        if status:
            qq = qq.filter(SocialFlag.review_status == status)
        if category:
            qq = qq.filter(SocialFlag.category == category)
        total = qq.count()
        rows = qq.order_by(SocialFlag.score.desc(), SocialFlag.id.desc()).limit(limit).all()
        masked = _masked(user)
        posts_by = {p.post_uid: p for p in db.query(SocialPost).filter(SocialPost.case_number == cn, SocialPost.post_uid.in_([f.item_ref for f in rows if f.item_type == "post"] or [""])).all()}
        accts = {str(a.id): a for a in db.query(SocialAccount).filter(SocialAccount.case_number == cn).all()}
        out = []
        for f in rows:
            if f.item_type == "post":
                p = posts_by.get(f.item_ref)
                out.append({**_flag_dict(f, masked, p.text if p else ""), "handle": p.handle if p else "", "platform": p.platform if p else "", "posted_at": iso_utc(p.posted_at) if p else None,
                            "url": p.url if p else ""})
            else:
                a = accts.get(f.item_ref)
                out.append({**_flag_dict(f, masked, a.bio if a else ""), "handle": a.handle if a else "", "platform": a.platform if a else "", "posted_at": None, "url": a.profile_url if a else ""})
        return {"case_number": cn, "total": total, "masked": masked, "flags": out, "caveat": lex.CAVEAT, "statuses": list(REVIEW_STATUSES)}

    @router.post("/{case}/flags/{flag_id}/review")
    def review_flag(case: str, flag_id: int, x: ReviewIn, db=Depends(ctx.get_db), user=Depends(perm("review"))):
        cn = case_of(db, user, case)
        if x.status not in REVIEW_STATUSES:
            raise HTTPException(400, f"status must be one of: {', '.join(REVIEW_STATUSES)}")
        f = db.query(SocialFlag).filter_by(case_number=cn, id=flag_id).first()
        if not f:
            raise HTTPException(404, "Flag not found")
        note = re.sub(r"\s+", " ", x.note).strip()
        if x.status == "false_positive" and len(note) < 3:
            raise HTTPException(400, "Add a short note saying why this is a false positive.")
        f.review_status, f.review_note, f.reviewed_by, f.reviewed_at = x.status, note, user["sub"], _now()
        db.add(SocialFlagReview(case_number=cn, flag_id=f.id, status=x.status, note=note, reviewer=user["sub"], at=f.reviewed_at))
        db.commit()
        det = {"action": "review", "flag_id": f.id, "category": f.category, "status": x.status, "item_type": f.item_type}
        ctx.audit(db, user["sub"], "social.flag_reviewed", {"case_number": cn, **det, "note": note[:200]})
        ctx.hooks.emit("social_item_flagged", db=db, user=user, case_number=cn, ref=f.item_ref, detail=det)
        return {"status": "recorded", "flag": _flag_dict(f, _masked(user)), "message": "Review recorded. It changes how the item is triaged, not any fact in the case."}

    @router.get("/{case}/flags/{flag_id}/history")
    def flag_history(case: str, flag_id: int, db=Depends(ctx.get_db), user=Depends(perm("read"))):
        cn = case_of(db, user, case)
        if not db.query(SocialFlag).filter_by(case_number=cn, id=flag_id).first():
            raise HTTPException(404, "Flag not found")
        rows = db.query(SocialFlagReview).filter_by(case_number=cn, flag_id=flag_id).order_by(SocialFlagReview.id.asc()).all()
        return {"flag_id": flag_id, "history": [{"status": r.status, "note": r.note, "reviewer": r.reviewer, "at": iso_utc(r.at)} for r in rows]}

    # ---------------- linking to the case
    @router.get("/{case}/links/suggestions")
    def link_suggestions(case: str, account_id: int | None = None, db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn = case_of(db, user, case)
        ents = ctx.svc.entities(db, cn, role="investigator")  # unmasked for matching only; output is masked per role in build_suggestions
        sug = build_suggestions(db, cn, ents, _masked(user), account_id)
        return {"case_number": cn, "suggestions": sug, "pending": sum(1 for s in sug if s["status"] == "suggested"),
                "note": "Suggestions only. Accepting one creates an UNVERIFIED candidate relationship that must go through the normal verification flow."}

    @router.post("/{case}/links/accept")
    def accept_link(case: str, x: AcceptIn, db=Depends(ctx.get_db), user=Depends(perm("write"))):
        cn = case_of(db, user, case)
        a = db.query(SocialAccount).filter_by(case_number=cn, id=x.account_id).first()
        if not a:
            raise HTTPException(404, "Account not found")
        ents = ctx.svc.entities(db, cn, role="investigator")
        sug = next((s for s in build_suggestions(db, cn, ents, False, a.id) if s["entity_id"] == x.entity_id and s["match_type"] == x.match_type), None)
        if not sug:
            raise HTTPException(404, "That link is not among the current suggestions for this account")
        if sug["status"] == "linked":
            raise HTTPException(409, "This link has already been accepted")
        sid = "SOC-" + _sha(f"{cn}|{a.platform}|{a.handle}".encode())[:10].upper()
        try:
            ctx.svc.add_entity(db, {"external_id": sid, "name": f"@{a.handle} ({a.platform})", "entity_type": "DIGITAL_IDENTIFIER", "confidence": 0.6,
                                    "attributes": {"kind": "social_account", "platform": a.platform, "handle": a.handle, "display_name": a.display_name,
                                                   "profile_url": a.profile_url, "source": "social-intel", "source_ref": f"social:account:{a.id}"}}, user["sub"], case_number=cn)
            source_ref = f"social:{sug['post_uid']}" if sug["post_uid"] else f"social:account:{a.id}"
            rel = ctx.svc.add_relationship(db, {"source_id": x.entity_id, "target_id": sid, "relation_type": "LINKED_TO_SOCIAL_ACCOUNT", "confidence": min(sug["confidence"], 0.5),
                                                "source_ref": source_ref, "event_time": None, "verification_state": "candidate", "model_version": "social-link-v1",
                                                "metadata": {"social": {"match_type": x.match_type, "where": sug["where"], "post_uid": sug["post_uid"], "account_id": a.id,
                                                                        "strength": sug["strength"], "note": x.note, "proposed_by": user["sub"]}}}, user["sub"], case_number=cn)
        except ValueError as exc:
            raise ctx.bad(exc)
        db.add(SocialLink(case_number=cn, account_id=a.id, entity_id=x.entity_id, social_entity_id=sid, match_type=x.match_type, relationship_id=rel.get("id"),
                          source_ref=source_ref, note=x.note, accepted_by=user["sub"]))
        db.commit()
        ctx.audit(db, user["sub"], "social.link_accepted", {"case_number": cn, "account_id": a.id, "entity_id": x.entity_id, "match_type": x.match_type,
                                                            "relationship_id": rel.get("id"), "verification_state": "candidate", "source_ref": source_ref})
        return {"status": "candidate_created", "relationship_id": rel.get("id"), "social_entity_id": sid, "verification_state": "candidate", "source_ref": source_ref,
                "message": "Saved as an UNVERIFIED candidate relationship. It stays out of the verified graph until a reviewer verifies it."}

    # ---------------- analysis (compute only)
    @router.get("/{case}/analysis/timeline")
    def a_timeline(case: str, tz: str = "IST", db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn = case_of(db, user, case)
        posts, _ = _analysis_inputs(db, cn)
        return {"case_number": cn, **an.timeline(posts, tz)}

    @router.get("/{case}/analysis/network")
    def a_network(case: str, db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn = case_of(db, user, case)
        posts, _ = _analysis_inputs(db, cn)
        return {"case_number": cn, **an.network(posts)}

    @router.get("/{case}/analysis/coordinated")
    def a_coord(case: str, window_minutes: int = Query(60, ge=1, le=10080), similarity: float = Query(0.7, ge=0.3, le=1.0), db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn = case_of(db, user, case)
        posts, _ = _analysis_inputs(db, cn)
        return {"case_number": cn, **an.coordinated(posts, window_minutes, similarity)}

    @router.get("/{case}/analysis/attribution")
    def a_attr(case: str, db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn = case_of(db, user, case)
        _, accounts_ = _analysis_inputs(db, cn)
        res = an.attribution(accounts_)
        if _masked(user):
            for h in res["hints"]:
                if h["type"] in ("phone", "email", "upi", "wallet"):
                    h["value"] = ex.mask_value(h["type"], h["value"])
        return {"case_number": cn, **res}

    @router.get("/{case}/analysis/flags")
    def a_flags(case: str, db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn = case_of(db, user, case)
        fl = db.query(SocialFlag).filter_by(case_number=cn).all()
        cats = []
        for k, info in lex.CATEGORIES.items():
            items = [f for f in fl if f.category == k]
            cats.append({"category": k, "label": info["label"], "why": info["why"], "count": len(items), "unreviewed": sum(1 for f in items if f.review_status == "unreviewed"),
                         "relevant": sum(1 for f in items if f.review_status == "relevant"), "false_positive": sum(1 for f in items if f.review_status == "false_positive"),
                         "max_score": max((f.score for f in items), default=0)})
        n = sum(c["count"] for c in cats)
        fp = sum(c["false_positive"] for c in cats)
        rev = sum(c["relevant"] + c["false_positive"] for c in cats)
        head = (f"{n} wording flag(s) across {sum(1 for c in cats if c['count'])} categor{'y' if sum(1 for c in cats if c['count']) == 1 else 'ies'}; "
                f"{sum(c['unreviewed'] for c in cats)} still need a human look." + (f" Of the {rev} decided so far, {fp} were false positives." if rev else "")) if n else \
            "No wording flags in the imported material."
        return {"case_number": cn, "categories": cats, "headline": head, "caveat": lex.CAVEAT,
                "explain": "A flag means a published phrase pattern matched. Open the item, read the whole post, then mark it relevant, false positive or reviewed. "
                           "Nothing here measures anyone's mood, intent or guilt."}

    # ---------------- export
    @router.get("/{case}/report")
    def report(case: str, format: str = Query("json", pattern="^(json|csv)$"), db=Depends(ctx.get_db), user=Depends(perm("report"))):
        cn = case_of(db, user, case)
        masked = _masked(user)
        imps = db.query(SocialImport).filter_by(case_number=cn).order_by(SocialImport.id.asc()).all()
        fl = db.query(SocialFlag).filter_by(case_number=cn).order_by(SocialFlag.score.desc()).all()
        posts_by = {p.post_uid: p for p in db.query(SocialPost).filter_by(case_number=cn).all()}
        accts = {str(a.id): a for a in db.query(SocialAccount).filter_by(case_number=cn).all()}
        rows = []
        for f in fl:
            p = posts_by.get(f.item_ref) if f.item_type == "post" else None
            a = accts.get(f.item_ref) if f.item_type == "account" else None
            text = p.text if p else (a.bio if a else "")
            rows.append({"flag_id": f.id, "category": f.category, "score": f.score, "phrase": f.phrase, "review_status": f.review_status, "review_note": f.review_note,
                         "reviewed_by": f.reviewed_by, "handle": (p or a).handle if (p or a) else "", "platform": (p or a).platform if (p or a) else "",
                         "posted_at": iso_utc(p.posted_at) if p else "", "url": p.url if p else "", "text": ex.mask_text(text) if masked else text})
        ctx.audit(db, user["sub"], "social.report_generated", {"case_number": cn, "format": format, "flags": len(rows)})
        if format == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            cols = ["flag_id", "category", "score", "review_status", "review_note", "reviewed_by", "platform", "handle", "posted_at", "url", "phrase", "text"]
            w.writerow(cols)
            for r in rows:
                w.writerow([("'" + str(r[c]) if isinstance(r[c], str) and r[c][:1] in "=+-@" else r[c]) for c in cols])
            return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=social_flags_{cn}.csv"})
        return {"case_number": cn, "generated_at": iso_utc(_now()), "generated_by": user["sub"], "counts": {"imports": len(imps), "posts": len(posts_by), "accounts": len(accts), "flags": len(rows)},
                "imports": [{"import_id": i.import_id, "source": i.source, "collected_by": i.collected_by, "collected_at": iso_utc(i.collected_at) or None, "sha256": i.sha256,
                             "legal_basis": i.legal_basis, "posts": i.n_posts, "accounts": i.n_accounts} for i in imps],
                "flags": rows, "limits": LIMITS, "caveat": lex.CAVEAT}

    return router


def _json_seed(raw: str) -> list[dict]:
    try:
        return list((json.loads(raw) or {}).get("seed_entities", []))
    except ValueError:
        return []
