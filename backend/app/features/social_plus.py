"""Social media intelligence, investigator tools (extends features/social.py; analysis never contacts a platform; fetching is confined to features/social_collect.py).

* Watch list        - handles, hashtags, keywords, phones, e-mails, UPI IDs, links and places the unit is looking for; hits are computed over everything imported.
* Priority queue    - which accounts and posts a human should read first, with the exact reasons and points behind each line.
* Look-alike accounts, narrative spread (who used a hashtag / link first, was there a burst), map of places, account profile sheet.
* Evidence sheets   - a printable preservation sheet per post: text, its SHA-256 (re-checked now), where it came from, who imported it and under what authority.
                      Each sheet is watermarked and recorded in the tamper-evident ledger.
* English view      - offline translation of Hindi / Marathi posts (assistive, always shown next to the original).
* Analyst notes     - short notes pinned to an account or post.

Everything is descriptive decision support. Scores order a reading list; they never say a person did something.
"""
from __future__ import annotations

import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint

from ai_engine.social import analysis as an
from ai_engine.social import extract as ex
from ai_engine.social import intel
from ai_engine.social import lexicon as lex
from ai_engine.social.timeutil import fmt_local, iso_utc
from backend.app.db.database import Base
from backend.app.features import social as S

# NOTE: table names start with "sx_" (not "soc_") on purpose: they hold working notes, not evidence, so the ledger's row tracker leaves them alone.
KINDS = ("handle", "hashtag", "keyword", "phone", "email", "upi", "url", "place")
MAX_TERMS = 200


class WatchTerm(Base):
    __tablename__ = "sx_watch_terms"
    __table_args__ = (UniqueConstraint("case_number", "kind", "value", name="uq_sx_watch"),)
    id = Column(Integer, primary_key=True)
    case_number = Column(String(100), index=True)
    kind = Column(String(20))
    value = Column(String(200))
    note = Column(String(300), default="")
    priority = Column(String(10), default="normal")
    active = Column(Boolean, default=True)
    created_by = Column(String(100), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class AnalystNote(Base):
    __tablename__ = "sx_notes"
    id = Column(Integer, primary_key=True)
    case_number = Column(String(100), index=True)
    item_type = Column(String(10))  # account | post
    item_ref = Column(String(40), index=True)
    body = Column(String(1000))
    pinned = Column(Boolean, default=False)
    created_by = Column(String(100), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class WatchIn(BaseModel):
    kind: Literal["handle", "hashtag", "keyword", "phone", "email", "upi", "url", "place"]
    value: str = Field(min_length=2, max_length=200)
    note: str = Field(default="", max_length=300)
    priority: Literal["normal", "high"] = "normal"


class NoteIn(BaseModel):
    item_type: Literal["account", "post"]
    item_ref: str = Field(min_length=1, max_length=40)
    body: str = Field(min_length=2, max_length=1000)
    pinned: bool = False


class SheetIn(BaseModel):
    post_uids: list[str] = Field(min_length=1, max_length=50)
    format: Literal["html", "json"] = "html"
    purpose: str = Field(default="", max_length=300)


class TranslateIn(BaseModel):
    post_uid: str = Field(min_length=1, max_length=40)


# ------------------------------------------------------------------------------------------------------------------ helpers
def norm_term(kind: str, value: str) -> str:
    v = (value or "").strip()
    if kind == "handle":
        return v.lstrip("@").lower()
    if kind == "hashtag":
        return v.lstrip("#").lower()
    if kind == "phone":
        d = re.sub(r"\D", "", v)
        return d[-10:] if len(d) >= 10 else d
    if kind in ("email", "upi"):
        return v.lower()
    if kind == "url":
        return ex.normalise_url(v)
    return v.lower()


def _term_dict(t: WatchTerm, masked: bool) -> dict:
    val = ex.mask_value(t.kind, t.value) if masked and t.kind in ("phone", "email", "upi") else t.value
    return {"id": t.id, "kind": t.kind, "value": val, "note": t.note, "priority": t.priority, "active": bool(t.active), "created_by": t.created_by,
            "created_at": iso_utc(t.created_at)}


def _snippet(text: str, needle: str, width: int = 70) -> str:
    text = text or ""
    i = text.lower().find((needle or "").lower())
    if i < 0:
        return text[: width * 2]
    a, b = max(0, i - width), min(len(text), i + len(needle) + width)
    return ("…" if a else "") + text[a:b] + ("…" if b < len(text) else "")


def compute_hits(db, cn: str, terms: list[WatchTerm], masked: bool = False) -> list[dict]:
    """Every place a watch-list term appears in the imported posts and profiles."""
    terms = [t for t in terms if t.active]
    if not terms:
        return []
    hits = []
    accounts = db.query(S.SocialAccount).filter_by(case_number=cn).all()
    posts = db.query(S.SocialPost).filter_by(case_number=cn).all()

    def check(t, where, text, extracted, handle, account_key, uid, own_handle=None, extra_handles=()):
        val, kind = t.value, t.kind
        ok, shown = False, val
        if kind == "handle":
            ok = own_handle == val or val in {h.lower() for h in (extracted.get("mentions") or [])} or val in {h.lower() for h in extra_handles if h}
        elif kind == "hashtag":
            ok = val in {h.lstrip("#").lower() for h in (extracted.get("hashtags") or [])}
        elif kind == "keyword":
            ok = re.search(r"(?<!\w)" + re.escape(val) + r"(?!\w)", (text or "").lower()) is not None
        elif kind == "phone":
            ok = any(re.sub(r"\D", "", p).endswith(val) for p in extracted.get("phones") or [])
        elif kind == "email":
            ok = val in {e.lower() for e in extracted.get("emails") or []}
        elif kind == "upi":
            ok = val in {u.lower() for u in extracted.get("upi_ids") or []}
        elif kind == "url":
            ok = any(u.startswith(val) for u in S._url_keys(extracted))
        elif kind == "place":
            ok = val in {p["name"].lower() for p in extracted.get("places") or []}
        if ok:
            hits.append({"term_id": t.id, "term": ex.mask_value(kind, val) if masked and kind in ("phone", "email", "upi") else val, "kind": kind, "priority": t.priority,
                         "where": where, "account": account_key, "handle": handle, "post_uid": uid,
                         "snippet": (ex.mask_text(_snippet(text, val)) if masked else _snippet(text, val))})

    for a in accounts:
        e = S._jl(a.extracted_json, {})
        for t in terms:
            check(t, "profile", f"{a.display_name} {a.bio}", e, a.handle, f"{a.platform}:{a.handle}", None, own_handle=a.handle.lower())
    for p in posts:
        e = S._jl(p.extracted_json, {})
        for t in terms:
            check(t, "post", p.text, e, p.handle, f"{p.platform}:{p.handle}", p.post_uid, own_handle=p.handle.lower(), extra_handles=(p.reply_to.lower(), p.repost_of.lower()))
    order = {"high": 0, "normal": 1}
    hits.sort(key=lambda h: (order.get(h["priority"], 1), h["kind"], h["account"]))
    return hits[:2000]


def _inputs(db, cn):
    """Posts (with reach), accounts (with ids) and flags in the plain shapes the analysis functions expect."""
    posts, accounts_an = S._analysis_inputs(db, cn)
    likes: dict = {}
    for row in db.query(S.SocialPost).filter_by(case_number=cn).all():
        likes[row.post_uid] = (row.likes or 0, row.shares or 0)
    for p in posts:
        p["likes"], p["shares"] = likes.get(p["uid"], (0, 0))
    accs = []
    for a in db.query(S.SocialAccount).filter_by(case_number=cn).all():
        accs.append({"id": a.id, "key": f"{a.platform}:{a.handle}", "platform": a.platform, "handle": a.handle, "display_name": a.display_name, "bio": a.bio,
                     "followers": a.followers, "location_text": a.location_text})
    flags = []
    key_of_id = {a["id"]: a["key"] for a in accs}
    for f in db.query(S.SocialFlag).filter_by(case_number=cn).all():
        d = {"level": f.level, "review_status": f.review_status, "category": f.category, "label": lex.CATEGORIES.get(f.category, {}).get("label", f.category)}
        if f.item_type == "post":
            d["post_uid"] = f.item_ref
        else:
            try:
                d["account_key"] = key_of_id.get(int(f.item_ref))
            except ValueError:
                pass
        flags.append(d)
    return posts, accounts_an, accs, flags


def build(ctx):
    router = APIRouter(prefix="/social", tags=["social"])
    perm = ctx.require_perm

    def case_of(db, user, case):
        return ctx.scope(db, user, case)

    # ------------------------------------------------------------------ watch list
    @router.get("/{case}/intel/watch")
    def watch_list(case: str, db=Depends(ctx.get_db), user=Depends(perm("read"))):
        cn = case_of(db, user, case)
        masked = S._masked(user)
        rows = db.query(WatchTerm).filter_by(case_number=cn).order_by(WatchTerm.id.asc()).all()
        return {"case_number": cn, "kinds": list(KINDS), "terms": [_term_dict(t, masked) for t in rows], "max_terms": MAX_TERMS,
                "explain": "Things the unit is looking for: an account, a hashtag, a phrase, a phone number, a UPI ID, a link or a place. Terms are checked against "
                           "everything imported into this case, including material imported before the term was added. The tool never searches outside what you import."}

    @router.post("/{case}/intel/watch")
    def watch_add(case: str, x: WatchIn, db=Depends(ctx.get_db), user=Depends(perm("write"))):
        cn = case_of(db, user, case)
        val = norm_term(x.kind, x.value)
        if len(val) < 2:
            raise HTTPException(400, "That value is too short to watch for")
        if x.kind == "phone" and len(val) < 10:
            raise HTTPException(400, "A phone number needs at least 10 digits")
        if x.kind == "keyword" and len(val) < 3:
            raise HTTPException(400, "A keyword needs at least 3 characters")
        if db.query(WatchTerm).filter_by(case_number=cn).count() >= MAX_TERMS:
            raise HTTPException(400, f"A case can hold at most {MAX_TERMS} watch-list terms")
        if db.query(WatchTerm).filter_by(case_number=cn, kind=x.kind, value=val).first():
            raise HTTPException(409, "That term is already on the watch list")
        t = WatchTerm(case_number=cn, kind=x.kind, value=val, note=x.note.strip(), priority=x.priority, created_by=user["sub"])
        db.add(t)
        db.commit()
        ctx.audit(db, user["sub"], "social.watch_added", {"case_number": cn, "kind": x.kind, "priority": x.priority, "term_id": t.id})
        hits = compute_hits(db, cn, [t])
        return {"term": _term_dict(t, False), "hits_now": len(hits), "message": f"Added. It matches {len(hits)} item(s) already imported."}

    @router.delete("/{case}/intel/watch/{term_id}")
    def watch_remove(case: str, term_id: int, db=Depends(ctx.get_db), user=Depends(perm("write"))):
        cn = case_of(db, user, case)
        t = db.query(WatchTerm).filter_by(case_number=cn, id=term_id).first()
        if not t:
            raise HTTPException(404, "Term not found")
        db.delete(t)
        db.commit()
        ctx.audit(db, user["sub"], "social.watch_removed", {"case_number": cn, "kind": t.kind, "term_id": term_id})
        return {"removed": term_id}

    @router.get("/{case}/intel/watch/hits")
    def watch_hits(case: str, db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn = case_of(db, user, case)
        terms = db.query(WatchTerm).filter_by(case_number=cn).all()
        hits = compute_hits(db, cn, terms, S._masked(user))
        by_term = Counter(h["term_id"] for h in hits)
        return {"case_number": cn, "hits": hits, "terms": len(terms), "by_term": dict(by_term),
                "headline": (f"{len(hits)} match(es) for {len(by_term)} of {len(terms)} watch-list term(s)." if terms else "The watch list is empty."),
                "explain": "A match means the term appears in the imported material. A keyword can appear in a quote, a joke or a news post; open the item and read it."}

    # ------------------------------------------------------------------ priority queue
    @router.get("/{case}/intel/triage")
    def triage(case: str, db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn = case_of(db, user, case)
        posts, accounts_an, accs, flags = _inputs(db, cn)
        terms = db.query(WatchTerm).filter_by(case_number=cn).all()
        hits = compute_hits(db, cn, terms)
        res = intel.triage(accs, posts, flags, hits, an.coordinated(posts), an.attribution(accounts_an))
        by_uid = {p.post_uid: p for p in db.query(S.SocialPost).filter_by(case_number=cn).all()}
        masked = S._masked(user)
        for it in res["posts"]:
            p = by_uid.get(it["post_uid"])
            if p:
                it["text"] = ex.mask_text(p.text) if masked else p.text
                it["posted_at"] = iso_utc(p.posted_at) or None
                it["handle"], it["platform"] = p.handle, p.platform
        return {"case_number": cn, **res}

    # ------------------------------------------------------------------ look-alikes and narratives
    @router.get("/{case}/intel/lookalikes")
    def lookalikes(case: str, db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn = case_of(db, user, case)
        _, _, accs, _ = _inputs(db, cn)
        return {"case_number": cn, **intel.lookalike_accounts(accs)}

    @router.get("/{case}/intel/narratives")
    def narratives(case: str, db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn = case_of(db, user, case)
        posts, _, _, _ = _inputs(db, cn)
        return {"case_number": cn, **intel.narratives(posts)}

    # ------------------------------------------------------------------ places
    @router.get("/{case}/intel/places")
    def places(case: str, db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn = case_of(db, user, case)
        agg: dict[str, dict] = {}

        def add(pl, account, uid, ts, source):
            if pl.get("lat") is None or pl.get("lon") is None:
                return
            e = agg.setdefault(pl["name"], {"name": pl["name"], "state": pl.get("state", ""), "lat": pl["lat"], "lon": pl["lon"], "posts": 0, "profiles": 0,
                                            "accounts": set(), "first": None, "last": None, "sample_post": uid})
            e["posts" if source == "post" else "profiles"] += 1
            e["accounts"].add(account)
            if ts:
                e["first"] = min(e["first"], ts) if e["first"] else ts
                e["last"] = max(e["last"], ts) if e["last"] else ts
        for p in db.query(S.SocialPost).filter_by(case_number=cn).all():
            seen = set()
            for pl in S._jl(p.extracted_json, {}).get("places", []):
                seen.add(pl["name"]); add(pl, f"{p.platform}:{p.handle}", p.post_uid, p.posted_at, "post")
            if p.location_text:
                for pl in ex.find_places(p.location_text):
                    if pl["name"] not in seen:
                        add(pl, f"{p.platform}:{p.handle}", p.post_uid, p.posted_at, "post")
        for a in db.query(S.SocialAccount).filter_by(case_number=cn).all():
            for pl in ex.find_places(f"{a.location_text} {a.bio}"):
                add(pl, f"{a.platform}:{a.handle}", None, None, "profile")
        out = sorted(({**e, "accounts": sorted(e["accounts"]), "first": iso_utc(e["first"]) or None, "last": iso_utc(e["last"]) or None} for e in agg.values()),
                     key=lambda e: -(e["posts"] + e["profiles"]))
        return {"case_number": cn, "places": out[:200], "headline": (f"{len(out)} place(s) named in posts and profiles." if out else "No known place is named in the imported material."),
                "explain": "Places are found by matching words against a built-in list of Indian cities, districts and localities, so a named place is not where the "
                           "person is. 'Delhi' in a sentence may be a place someone is talking about, a joke or a scam pitch. Coordinates are approximate."}

    # ------------------------------------------------------------------ account profile sheet
    @router.get("/{case}/intel/account/{account_id}")
    def dossier(case: str, account_id: int, db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn = case_of(db, user, case)
        a = db.query(S.SocialAccount).filter_by(case_number=cn, id=account_id).first()
        if not a:
            raise HTTPException(404, "Account not found")
        masked = S._masked(user)
        key = f"{a.platform}:{a.handle}"
        posts, accounts_an, accs, flags = _inputs(db, cn)
        mine = [p for p in posts if p["account"] == key]
        act = an._activity(mine, "IST") if mine else {}
        dated = sorted(p["ts"] for p in mine if p.get("ts"))
        gaps = [(b - c).total_seconds() / 3600 for c, b in zip(dated, dated[1:])]
        tags = Counter(t for p in mine for t in p.get("hashtags") or [])
        ments = Counter(m for p in mine for m in p.get("mentions") or [])
        net = an.network(posts)
        inter = [e for e in net["edges"] if e["source"] == key or e["target"] == key]
        e = S._jl(a.extracted_json, {})
        ids = [{"kind": k, "value": ex.mask_value(k, v) if masked else v} for k, v in ex.identifiers(e)]
        fl = db.query(S.SocialFlag).filter_by(case_number=cn).all()
        post_uids = {p["uid"] for p in mine}
        mine_flags = [f for f in fl if (f.item_type == "post" and f.item_ref in post_uids) or (f.item_type == "account" and f.item_ref == str(a.id))]
        hits = [h for h in compute_hits(db, cn, db.query(WatchTerm).filter_by(case_number=cn).all(), masked) if h["account"] == key]
        look = [p for p in intel.lookalike_accounts(accs)["pairs"] if key in (p["a"], p["b"])]
        attr = [h for h in an.attribution(accounts_an)["hints"] if key in h["accounts"]]
        for h in attr:
            if masked and h["type"] in ("phone", "email", "upi", "wallet"):
                h["value"] = ex.mask_value(h["type"], h["value"])
        links = [{"entity_id": l.entity_id, "match_type": l.match_type, "accepted_by": l.accepted_by, "accepted_at": iso_utc(l.accepted_at)}
                 for l in db.query(S.SocialLink).filter_by(case_number=cn, account_id=a.id).all()]
        notes = [{"id": n.id, "body": n.body, "pinned": bool(n.pinned), "created_by": n.created_by, "created_at": iso_utc(n.created_at)}
                 for n in db.query(AnalystNote).filter_by(case_number=cn, item_type="account", item_ref=str(a.id)).order_by(AnalystNote.pinned.desc(), AnalystNote.id.desc()).all()]
        triage = next((i for i in intel.triage(accs, posts, flags, compute_hits(db, cn, db.query(WatchTerm).filter_by(case_number=cn).all()),
                                               an.coordinated(posts), an.attribution(accounts_an), top=500)["accounts"] if i["account"] == key), None)
        eng = [(p["likes"] + p["shares"]) for p in mine]
        return {"case_number": cn, "account": S._account_dict(a, masked), "identifiers": ids, "posts": len(mine),
                "first_post": iso_utc(dated[0]) if dated else None, "last_post": iso_utc(dated[-1]) if dated else None,
                "cadence": {"median_gap_hours": round(median(gaps), 1) if gaps else None, "longest_silence_hours": round(max(gaps), 1) if gaps else None,
                            "posts_per_active_day": round(len(dated) / max(1, len({d.date() for d in dated})), 1) if dated else None},
                "activity": {"headline": act.get("headline", ""), "hours": act.get("hours", []), "heatmap": act.get("heatmap", [])},
                "top_hashtags": [{"tag": t, "count": c} for t, c in tags.most_common(8)], "top_mentions": [{"handle": m, "count": c} for m, c in ments.most_common(8)],
                "interactions": sorted(inter, key=lambda i: -i["weight"])[:15], "engagement": {"total": sum(eng), "average_per_post": round(sum(eng) / len(eng), 1) if eng else 0},
                "flags": {"total": len(mine_flags), "unreviewed": sum(1 for f in mine_flags if f.review_status == "unreviewed"),
                          "relevant": sum(1 for f in mine_flags if f.review_status == "relevant"), "false_positive": sum(1 for f in mine_flags if f.review_status == "false_positive")},
                "watch_hits": hits[:30], "lookalikes": look, "attribution": attr, "linked_entities": links, "notes": notes, "priority": triage,
                "caveat": "A profile sheet collects what was imported about one account. It describes activity; it does not identify the person behind it."}

    # ------------------------------------------------------------------ notes
    @router.get("/{case}/intel/notes")
    def notes_list(case: str, item_type: str = "", item_ref: str = "", db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn = case_of(db, user, case)
        q = db.query(AnalystNote).filter_by(case_number=cn)
        if item_type:
            q = q.filter_by(item_type=item_type)
        if item_ref:
            q = q.filter_by(item_ref=item_ref)
        return {"notes": [{"id": n.id, "item_type": n.item_type, "item_ref": n.item_ref, "body": n.body, "pinned": bool(n.pinned), "created_by": n.created_by,
                           "created_at": iso_utc(n.created_at)} for n in q.order_by(AnalystNote.pinned.desc(), AnalystNote.id.desc()).limit(200).all()]}

    @router.post("/{case}/intel/notes")
    def note_add(case: str, x: NoteIn, db=Depends(ctx.get_db), user=Depends(perm("note"))):
        cn = case_of(db, user, case)
        if x.item_type == "account":
            ok = x.item_ref.isdigit() and db.query(S.SocialAccount).filter_by(case_number=cn, id=int(x.item_ref)).first()
        else:
            ok = db.query(S.SocialPost).filter_by(case_number=cn, post_uid=x.item_ref).first()
        if not ok:
            raise HTTPException(404, "That account or post is not in this case")
        n = AnalystNote(case_number=cn, item_type=x.item_type, item_ref=x.item_ref, body=x.body.strip(), pinned=x.pinned, created_by=user["sub"])
        db.add(n)
        db.commit()
        ctx.audit(db, user["sub"], "social.note_added", {"case_number": cn, "item_type": x.item_type, "note_id": n.id})
        return {"id": n.id}

    @router.delete("/{case}/intel/notes/{note_id}")
    def note_del(case: str, note_id: int, db=Depends(ctx.get_db), user=Depends(perm("note"))):
        cn = case_of(db, user, case)
        n = db.query(AnalystNote).filter_by(case_number=cn, id=note_id).first()
        if not n:
            raise HTTPException(404, "Note not found")
        if n.created_by != user["sub"] and user["role"] != "admin":
            raise HTTPException(403, "Only the author or an administrator can delete a note")
        db.delete(n)
        db.commit()
        ctx.audit(db, user["sub"], "social.note_removed", {"case_number": cn, "note_id": note_id})
        return {"removed": note_id}

    # ------------------------------------------------------------------ English view
    @router.post("/{case}/intel/translate")
    def translate(case: str, x: TranslateIn, db=Depends(ctx.get_db), user=Depends(perm("analyze"))):
        cn = case_of(db, user, case)
        p = db.query(S.SocialPost).filter_by(case_number=cn, post_uid=x.post_uid).first()
        if not p:
            raise HTTPException(404, "Post not found")
        from ai_engine.nlp.translation import translate_text_to_english
        code = (p.lang or "").lower()
        dev = re.search(r"[ऀ-ॿ]", p.text or "") is not None
        if not dev and (code in ("en", "eng", "english", "") or not re.search(r"\b(hai|hain|nahi|kya|kaise|paisa|bhai|karo|hoga)\b", p.text or "", re.I)):
            return {"post_uid": p.post_uid, "translated": False, "text": p.text if not S._masked(user) else ex.mask_text(p.text), "note": "This post appears to be in English already."}
        if dev and code in ("en", "eng", "english", ""):
            code = "hi"
        r = translate_text_to_english(p.text, code or "auto")
        text = ex.mask_text(r["text"]) if S._masked(user) else r["text"]
        return {"post_uid": p.post_uid, "translated": True, "text": text, "engine": r.get("engine"), "confidence": r.get("confidence"), "warning": r.get("warning", ""),
                "note": "Machine translation is assistive. Names, slang and legal terms may be wrong: read the original."}

    # ------------------------------------------------------------------ evidence preservation sheet
    @router.post("/{case}/intel/sheet")
    def sheet(case: str, x: SheetIn, db=Depends(ctx.get_db), user=Depends(perm("report"))):
        cn = case_of(db, user, case)
        uids = list(dict.fromkeys(x.post_uids))
        rows = db.query(S.SocialPost).filter(S.SocialPost.case_number == cn, S.SocialPost.post_uid.in_(uids)).all()
        if not rows:
            raise HTTPException(404, "None of those posts are in this case")
        imports = {i.import_id: i for i in db.query(S.SocialImport).filter_by(case_number=cn).all()}
        flags = defaultdict(list)
        for f in db.query(S.SocialFlag).filter_by(case_number=cn, item_type="post").all():
            flags[f.item_ref].append(f)
        items = []
        for p in sorted(rows, key=lambda r: (r.posted_at is None, r.posted_at, r.id)):
            imp = imports.get(p.import_id)
            recomputed = S._sha((p.text or "").encode())
            items.append({
                "post_uid": p.post_uid, "platform": p.platform, "handle": p.handle, "post_id": p.post_id, "url": p.url, "posted_at_utc": iso_utc(p.posted_at) or None,
                "posted_at_ist": fmt_local(p.posted_at, "IST") or None, "text": p.text, "text_sha256_recorded": p.text_sha256, "text_sha256_now": recomputed,
                "text_unchanged_since_import": recomputed == p.text_sha256, "media": S._jl(p.media_json, []), "language": p.lang,
                "likes": p.likes, "shares": p.shares, "replies": p.replies,
                "flags": [{"category": f.category, "phrase": f.phrase, "review_status": f.review_status, "reviewed_by": f.reviewed_by} for f in flags.get(p.post_uid, [])],
                "source_import": ({"import_id": imp.import_id, "source": imp.source, "source_type": imp.source_type, "collected_by": imp.collected_by,
                                   "collected_at": iso_utc(imp.collected_at) or None, "imported_at": iso_utc(imp.imported_at), "legal_basis": imp.legal_basis,
                                   "raw_import_sha256": imp.sha256, "assumed_timezone": imp.assumed_timezone} if imp else None)})
        body = {"kind": "social_evidence_sheet", "case_number": cn, "generated_by": user["sub"], "generated_at": iso_utc(datetime.now(timezone.utc).replace(tzinfo=None)),
                "purpose": x.purpose.strip(), "items": items,
                "statement": ("This sheet lists social-media material that was IMPORTED into the case by the officer named against each item. The system did not visit, "
                              "download from or contact any platform. The text fingerprint (SHA-256) was taken when the material was imported and has been recomputed "
                              "now: 'unchanged' means the stored text still matches it. The sheet describes how the material entered the case; it does not by itself "
                              "establish that the post is genuine or who wrote it. A certificate under section 63 of the Bharatiya Sakshya Adhiniyam, if needed, is "
                              "prepared separately by the person responsible for the source device or account.")}
        stamped = ctx.stamp(db, user, cn, "social_evidence_sheet", "json", body)
        ctx.audit(db, user["sub"], "social.sheet_generated", {"case_number": cn, "posts": len(items), "format": x.format})
        if x.format == "json":
            return stamped
        return Response(render_sheet_html(stamped), media_type="text/html; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="evidence_sheet_{re.sub(r"[^A-Za-z0-9_.-]", "_", cn)}.html"', "Cache-Control": "no-store"})

    return router


# ------------------------------------------------------------------------------------------------------------------ printable sheet
def render_sheet_html(doc: dict) -> bytes:
    e = html.escape

    def row(k, v):
        return f"<tr><th>{e(k)}</th><td>{e('' if v is None else str(v))}</td></tr>"
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Evidence preservation sheet - {e(doc['case_number'])}</title>"
             "<style>body{font:14px/1.5 Georgia,serif;max-width:820px;margin:2rem auto;padding:0 1rem;color:#111}h1{font-size:1.4rem;margin:0}"
             "h2{font-size:1.05rem;border-bottom:1px solid #999;padding-bottom:.2rem;margin-top:1.6rem}table{border-collapse:collapse;width:100%;margin:.4rem 0}"
             "th{text-align:left;width:32%;vertical-align:top;padding:.2rem .5rem .2rem 0;color:#333;font-weight:600}td{padding:.2rem 0;word-break:break-word}"
             ".text{white-space:pre-wrap;border:1px solid #bbb;padding:.6rem;background:#fafafa}.ok{color:#165c2f}.bad{color:#a11}.stmt{font-size:.85rem;color:#333}"
             ".wm{margin-top:2rem;border-top:1px solid #999;padding-top:.5rem;font-size:.8rem;color:#444}@media print{body{margin:0}}</style></head><body>",
             "<h1>DARK CRIMENET - Evidence preservation sheet</h1>",
             f"<p>Case <b>{e(doc['case_number'])}</b> &middot; prepared by {e(doc['generated_by'])} &middot; {e(str(doc['generated_at']))}"
             + (f" &middot; purpose: {e(doc['purpose'])}" if doc.get("purpose") else "") + "</p>",
             f"<p class='stmt'>{e(doc['statement'])}</p>"]
    for i, it in enumerate(doc["items"], 1):
        ok = it["text_unchanged_since_import"]
        parts.append(f"<h2>Item {i}: {e(it['platform'])} @{e(it['handle'])}</h2><table>")
        for k, v in (("Reference", it["post_uid"]), ("Post ID", it["post_id"]), ("Link", it["url"]), ("Posted (UTC)", it["posted_at_utc"]), ("Posted (IST)", it["posted_at_ist"]),
                     ("Language", it["language"]), ("Likes / shares / replies", f"{it['likes']} / {it['shares']} / {it['replies']}"),
                     ("Attached media (as exported)", ", ".join(map(str, it["media"])) or "none listed")):
            parts.append(row(k, v))
        parts.append("</table><div class='text'>" + e(it["text"] or "") + "</div><table>")
        parts.append(row("Text SHA-256 at import", it["text_sha256_recorded"]))
        parts.append(row("Text SHA-256 now", it["text_sha256_now"]))
        parts.append(f"<tr><th>Result</th><td class='{'ok' if ok else 'bad'}'>{'Unchanged since import' if ok else 'DIFFERENT from the value recorded at import'}</td></tr>")
        src = it.get("source_import")
        if src:
            for k, v in (("Import reference", src["import_id"]), ("Source given", src["source"]), ("Source type", src["source_type"]), ("Imported by", src["collected_by"]),
                         ("Collected at", src["collected_at"]), ("Imported at", src["imported_at"]), ("Legal basis recorded", src["legal_basis"]),
                         ("SHA-256 of the whole import", src["raw_import_sha256"])):
                parts.append(row(k, v))
        for f in it["flags"]:
            parts.append(row("Wording flag", f"{f['category']} - {f['review_status']}" + (f" (by {f['reviewed_by']})" if f["reviewed_by"] else "")))
        parts.append("</table>")
    wm = (doc.get("watermark") or {}).get("text", "")
    parts.append(f"<div class='wm'>{e(wm)}<br>Recorded in the DARK CRIMENET tamper-evident ledger. Compare the reference above with the ledger to confirm this sheet was issued by the system.</div></body></html>")
    return "".join(parts).encode("utf-8")
