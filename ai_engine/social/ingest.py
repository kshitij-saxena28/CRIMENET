"""Parsing of officer-supplied social-media material (JSON, CSV, pasted text, manual items) into one normalised record shape.

Parsing never fetches anything: it works on text already held (typed, pasted, uploaded, or returned by collectors.py). Column names from common export
formats are accepted through the alias table below.
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from ai_engine.social.timeutil import parse_ts

PLATFORM_ALIASES = {"twitter": "x", "x.com": "x", "tweet": "x", "fb": "facebook", "insta": "instagram", "ig": "instagram", "tg": "telegram", "wa": "whatsapp",
                    "yt": "youtube", "you tube": "youtube", "sharechat": "sharechat", "koo": "koo", "": "unknown"}
FIELD_ALIASES = {
    "platform": ("platform", "network", "site", "source_platform"),
    "handle": ("handle", "username", "user_name", "screen_name", "user", "author", "account", "author_handle", "profile"),
    "display_name": ("display_name", "name", "full_name", "author_name", "displayname"),
    "post_id": ("post_id", "id", "tweet_id", "status_id", "message_id", "postid"),
    "url": ("url", "link", "post_url", "permalink"),
    "timestamp": ("timestamp", "created_at", "date", "time", "posted_at", "datetime", "created", "published"),
    "text": ("text", "content", "message", "body", "caption", "full_text", "post", "tweet"),
    "likes": ("likes", "like_count", "favorite_count", "favourites", "reactions"),
    "shares": ("shares", "retweets", "retweet_count", "reposts", "share_count", "forwards"),
    "replies": ("replies", "reply_count", "comments", "comment_count"),
    "mentions": ("mentions", "user_mentions", "tagged"),
    "media": ("media", "media_urls", "images", "attachments", "photos"),
    "location": ("location", "place", "geo", "geo_location", "city"),
    "bio": ("bio", "description", "about", "profile_description", "biography"),
    "followers": ("followers", "followers_count", "follower_count"),
    "following": ("following", "friends_count", "following_count"),
    "profile_url": ("profile_url", "profile_link", "account_url"),
    "reply_to": ("reply_to", "in_reply_to", "in_reply_to_screen_name", "in_reply_to_user", "replying_to"),
    "repost_of": ("repost_of", "retweeted_from", "retweeted", "quoted", "shared_from", "original_author"),
}
_LOOKUP = {a: canon for canon, names in FIELD_ALIASES.items() for a in names}
MAX_RECORDS = 5000


def norm_platform(v: Any) -> str:
    s = re.sub(r"\s+", " ", str(v or "").strip().lower())
    return PLATFORM_ALIASES.get(s, s or "unknown")[:30]


def norm_handle(v: Any) -> str:
    s = str(v or "").strip()
    m = re.search(r"(?i)(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com|instagram\.com|facebook\.com|t\.me|youtube\.com/@?|tiktok\.com/@)/?@?([\w.\-]+)", s)
    if m:
        s = m.group(1)
    return s.lstrip("@").strip().lower()[:60]


def to_int(v: Any):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().lower().replace(",", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmb]?)", s)
    if not m:
        return None
    return int(float(m.group(1)) * {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[m.group(2)])


def _as_list(v: Any) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            out.append(str(x.get("username") or x.get("screen_name") or x.get("url") or x) if isinstance(x, dict) else str(x))
        return [x.strip() for x in out if x and x.strip()]
    return [x.strip() for x in re.split(r"[;,|\n]+", str(v)) if x.strip()]


def normalise_record(raw: dict, assume_tz: str = "IST", defaults: dict | None = None) -> dict | None:
    """One raw dict -> canonical record, or None when it carries neither post text nor account information."""
    d = defaults or {}
    r: dict[str, Any] = {}
    for k, v in raw.items():
        canon = _LOOKUP.get(re.sub(r"[\s\-]+", "_", str(k).strip().lower()))
        if canon and canon not in r and v not in (None, ""):
            r[canon] = v
    if isinstance(r.get("handle"), dict):  # e.g. {"user": {"screen_name": "abc"}}
        u = r["handle"]
        r["handle"] = u.get("screen_name") or u.get("username") or u.get("handle") or ""
    rec = {
        "platform": norm_platform(r.get("platform") or d.get("platform")),
        "handle": norm_handle(r.get("handle") or d.get("handle")),
        "display_name": str(r.get("display_name") or "").strip()[:120],
        "post_id": str(r.get("post_id") or "").strip()[:80],
        "url": str(r.get("url") or "").strip()[:500],
        "ts": parse_ts(r.get("timestamp"), assume_tz),
        "text": str(r.get("text") or "").strip()[:20000],
        "likes": to_int(r.get("likes")), "shares": to_int(r.get("shares")), "replies": to_int(r.get("replies")),
        "mentions": [norm_handle(x) for x in _as_list(r.get("mentions")) if norm_handle(x)],
        "media": _as_list(r.get("media"))[:20],
        "location": str(r.get("location") or "").strip()[:200],
        "bio": str(r.get("bio") or "").strip()[:2000],
        "followers": to_int(r.get("followers")), "following": to_int(r.get("following")),
        "profile_url": str(r.get("profile_url") or "").strip()[:500],
        "reply_to": norm_handle(r.get("reply_to")), "repost_of": norm_handle(r.get("repost_of")),
    }
    rec["raw_timestamp"] = str(r.get("timestamp") or "")[:60]
    text = rec["text"]
    m = re.match(r"(?i)^RT\s+@([\w.]+):?\s", text)
    if m and not rec["repost_of"]:
        rec["repost_of"] = m.group(1).lower()
    rec["is_post"] = bool(text)
    rec["is_account"] = bool(rec["bio"] or rec["followers"] is not None or rec["following"] is not None or rec["profile_url"] or rec["display_name"])
    if not rec["handle"] and not (rec["is_post"] and rec["platform"]):
        return None
    if not (rec["is_post"] or rec["is_account"]):
        return None
    return rec


_CHAT = re.compile(r"^\[?(\d{1,4}[-/]\d{1,2}[-/]\d{1,4}[ T,]+\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?(?:\s?(?:Z|[+-]\d{2}:?\d{2}))?)\]?\s*[-–]?\s*@?([\w.]{2,40}):\s+(.+)$")


def parse_content(content: str, assume_tz: str = "IST", defaults: dict | None = None) -> tuple[list[dict], list[str], str]:
    """Return (records, warnings, detected_kind). Kind is json, csv, or text."""
    s = (content or "").strip("﻿ \r\n\t")
    warns: list[str] = []
    if not s:
        return [], ["Nothing to import: the content is empty."], "empty"
    rows: list[dict] = []
    kind = "text"
    if s[0] in "[{":
        try:
            data = json.loads(s)
        except ValueError as exc:
            return [], [f"This looks like JSON but could not be parsed: {exc}"], "json"
        kind = "json"
        if isinstance(data, dict):
            for key in ("posts", "items", "data", "tweets", "messages", "records", "results"):
                if isinstance(data.get(key), list):
                    accounts = data.get("accounts") if isinstance(data.get("accounts"), list) else []
                    rows = [x for x in accounts if isinstance(x, dict)] + [x for x in data[key] if isinstance(x, dict)]
                    break
            else:
                if isinstance(data.get("accounts"), list):
                    rows = [x for x in data["accounts"] if isinstance(x, dict)]
                else:
                    rows = [data]
        elif isinstance(data, list):
            rows = [x for x in data if isinstance(x, dict)]
    else:
        first = s.splitlines()[0].lower()
        delim = max((",", ";", "\t", "|"), key=first.count)
        if first.count(delim) >= 1 and any(_LOOKUP.get(re.sub(r"[\s\-]+", "_", c.strip().lower())) for c in first.split(delim)):
            kind = "csv"
            reader = csv.DictReader(io.StringIO(s), delimiter=delim)
            try:
                rows = [dict(x) for x in reader]
            except csv.Error as exc:
                return [], [f"The CSV could not be read: {exc}"], "csv"
    if kind == "text":
        chat = [_CHAT.match(line.strip()) for line in s.splitlines() if line.strip()]
        if chat and sum(1 for c in chat if c) >= max(1, len([1 for c in chat if c is not None]) // 1) and all(c for c in chat):
            rows = [{"timestamp": c.group(1), "handle": c.group(2), "text": c.group(3)} for c in chat if c]
            kind = "text (chat lines)"
        else:
            blocks = [b.strip() for b in re.split(r"\n\s*\n|\n-{3,}\n", s) if b.strip()]
            rows = [{"text": b} for b in blocks]
            if not (defaults or {}).get("handle"):
                warns.append("Pasted text has no account name: posts are stored under the handle 'unknown'. Enter a handle and platform to attribute them.")
    if len(rows) > MAX_RECORDS:
        warns.append(f"Only the first {MAX_RECORDS} of {len(rows)} records were imported.")
        rows = rows[:MAX_RECORDS]
    out, skipped = [], 0
    for raw in rows:
        rec = normalise_record(raw, assume_tz, defaults)
        if rec:
            if not rec["handle"]:
                rec["handle"] = "unknown"
            out.append(rec)
        else:
            skipped += 1
    if skipped:
        warns.append(f"{skipped} row(s) had neither post text nor account details and were skipped.")
    return out, warns, kind
