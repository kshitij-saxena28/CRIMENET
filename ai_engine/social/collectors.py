"""Collectors: fetch public posts through OFFICIAL platform APIs or public feeds, using credentials your unit holds.

What this is
    * A search by account (``user``), ``keyword``, ``hashtag`` or ``feed`` URL against one provider, returning raw post dicts that the normal
      import pipeline (ai_engine/social/ingest.py -> features/social.run_import) then reads, deduplicates and flags.

What this is not
    * No scraping, no logging in as anyone, no bypassing access controls, no fake accounts. A provider is only usable when its official
      API key is configured by the administrator (or when it is an open public API / feed that needs none).
    * Nothing runs by itself unless an officer created a saved search with a recorded legal basis.

Providers: x, youtube, reddit, mastodon, rss, demo (offline simulated data from the bundled synthetic sample).
Network access goes through ``http`` (a callable, replaced by a fake in tests) and refuses local / private addresses (SSRF guard).
"""
from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlparse

MAX_BYTES = 2_000_000
TIMEOUT = 15
UA = "DARK-CRIMENET/5.3 (investigation tool; official-API collector)"
ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "demo_dataset" / "social" / "sample_posts.json"


class CollectError(Exception):
    """A problem the officer can act on (bad key, no network, unknown account). Message is safe to show."""


@dataclass
class Provider:
    id: str
    label: str
    kinds: tuple
    env: tuple = ()          # settings keys that must be non-empty; () = works without credentials
    help: str = ""
    synthetic: bool = False
    needs: list = field(default_factory=list)


PROVIDERS: dict[str, Provider] = {p.id: p for p in (
    Provider("x", "X (Twitter) official API", ("user", "keyword", "hashtag"), ("x_bearer_token",),
             "Needs an X API bearer token (developer account). Recent-search and user-timeline endpoints."),
    Provider("youtube", "YouTube Data API", ("user", "keyword", "hashtag"), ("youtube_api_key",),
             "Needs a YouTube Data API key. Finds public videos by channel handle or search words."),
    Provider("reddit", "Reddit official API", ("user", "keyword"), ("reddit_client_id", "reddit_client_secret"),
             "Needs a Reddit app client id and secret. Public submissions by user or by search."),
    Provider("mastodon", "Mastodon (open public API)", ("user", "hashtag", "keyword"), (),
             "No key for accounts and hashtags on most servers; keyword search on many servers needs a token. Use user@server for another server."),
    Provider("rss", "Public RSS / Atom feed", ("feed",), (),
             "Any public feed address: news sites, blogs, forums, public channel bridges. No key."),
    Provider("demo", "Offline demonstration (synthetic)", ("user", "keyword", "hashtag"), (),
             "Simulated results from the bundled synthetic sample. Contacts no network. For practice only.", synthetic=True),
)}
KIND_LABEL = {"user": "Account / handle", "keyword": "Keyword or phrase", "hashtag": "Hashtag", "feed": "Feed address (URL)"}


def status(cfg: dict) -> list[dict]:
    out = []
    for p in PROVIDERS.values():
        missing = [k.upper() for k in p.env if not cfg.get(k)]
        out.append({"id": p.id, "label": p.label, "kinds": list(p.kinds), "configured": not missing, "missing": missing, "help": p.help, "synthetic": p.synthetic})
    return out


# --------------------------------------------------------------------------------------------- network (with SSRF guard)
def assert_public_host(host: str) -> None:
    if not host or host.lower() in ("localhost",) or host.endswith((".local", ".internal", ".localhost")):
        raise CollectError("That address is not allowed: only public internet hosts can be contacted.")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise CollectError(f"Could not find {host}. Check the address, or this server may be offline.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise CollectError("That address is not allowed: only public internet hosts can be contacted.")


def default_http(method: str, url: str, *, params=None, headers=None, data=None, auth=None):
    """-> (status_code, body_text). Never follows redirects to another host; reads at most MAX_BYTES."""
    import requests
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        raise CollectError("Only http and https addresses can be used.")
    assert_public_host(u.hostname or "")
    try:
        r = requests.request(method, url, params=params, headers={"User-Agent": UA, **(headers or {})}, data=data, auth=auth, timeout=TIMEOUT,
                             allow_redirects=False, stream=True)
        body = r.raw.read(MAX_BYTES + 1, decode_content=True)
        if len(body) > MAX_BYTES:
            raise CollectError("The response was too large to read safely.")
        if 300 <= r.status_code < 400 and r.headers.get("Location"):
            nxt = urlparse(requests.compat.urljoin(url, r.headers["Location"]))
            if nxt.hostname != u.hostname or nxt.scheme not in ("http", "https"):
                raise CollectError("The address redirected to a different site; enter the final address.")
            return default_http(method, nxt.geturl(), params=None, headers=headers, data=data, auth=auth)
        return r.status_code, body.decode(r.encoding or "utf-8", "replace")
    except CollectError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise CollectError("Could not reach the service. Check the internet connection of this server (it may be offline).") from exc


def _json(http, method, url, **kw):
    code, body = http(method, url, **kw)
    if code in (401, 403):
        raise CollectError("The service refused the request: check the API key and its permissions.")
    if code == 404:
        raise CollectError("Not found: check the account, hashtag or address.")
    if code == 429:
        raise CollectError("The service is rate-limiting this key. Try again later or reduce how often the search runs.")
    if code >= 400:
        raise CollectError(f"The service returned an error ({code}).")
    try:
        return json.loads(body)
    except ValueError:
        raise CollectError("The service returned something that is not the expected data.")


def _strip_html(s: str) -> str:
    s = re.sub(r"(?i)<br\s*/?>|</p>", "\n", s or "")
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _clean_handle(q: str) -> str:
    q = q.strip()
    m = re.search(r"(?:x|twitter|instagram|youtube|reddit)\.com/(?:u/|user/|@)?@?([\w.\-]+)", q)
    return (m.group(1) if m else q).lstrip("@").strip()


# --------------------------------------------------------------------------------------------- providers
def _x(cfg, kind, query, limit, since, http):
    h = {"Authorization": f"Bearer {cfg['x_bearer_token']}"}
    base = "https://api.x.com/2"
    tf = "created_at,public_metrics,entities,author_id,in_reply_to_user_id,referenced_tweets"
    uf = "username,name,description,public_metrics,location"
    n = str(max(10, min(100, limit)))
    if kind == "user":
        handle = _clean_handle(query)
        u = _json(http, "GET", f"{base}/users/by/username/{quote(handle)}", params={"user.fields": uf}, headers=h).get("data")
        if not u:
            raise CollectError("No such account on X.")
        p = {"max_results": n, "tweet.fields": tf}
        if since:
            p["since_id"] = since
        d = _json(http, "GET", f"{base}/users/{u['id']}/tweets", params=p, headers=h)
        tweets, users = d.get("data", []), {u["id"]: u}
        for t in tweets:  # the timeline endpoint does not repeat the author on each post
            t.setdefault("author_id", u["id"])
    else:
        q = query.strip()
        if kind == "hashtag":
            q = "#" + q.lstrip("#")
        p = {"query": q + " -is:retweet" if "is:retweet" not in q else q, "max_results": n, "tweet.fields": tf, "expansions": "author_id", "user.fields": uf}
        if since:
            p["since_id"] = since
        d = _json(http, "GET", f"{base}/tweets/search/recent", params=p, headers=h)
        tweets = d.get("data", [])
        users = {x["id"]: x for x in (d.get("includes") or {}).get("users", [])}
    out = []
    for t in tweets[:limit]:
        u = users.get(t.get("author_id")) or {}
        m = t.get("public_metrics") or {}
        pm = u.get("public_metrics") or {}
        handle = u.get("username") or ""
        out.append({"platform": "x", "handle": handle, "display_name": u.get("name", ""), "bio": u.get("description", ""), "location": u.get("location", ""),
                    "followers": pm.get("followers_count"), "following": pm.get("following_count"),
                    "profile_url": f"https://x.com/{handle}" if handle else "",
                    "post_id": t["id"], "url": f"https://x.com/{handle}/status/{t['id']}" if handle else "", "timestamp": t.get("created_at", ""),
                    "text": t.get("text", ""), "likes": m.get("like_count"), "shares": m.get("retweet_count"), "replies": m.get("reply_count"),
                    "mentions": [x.get("username") for x in (t.get("entities") or {}).get("mentions", []) if x.get("username")]})
    ids = [int(t["id"]) for t in tweets if str(t.get("id", "")).isdigit()]
    return out, str(max(ids)) if ids else since


def _youtube(cfg, kind, query, limit, since, http):
    key = cfg["youtube_api_key"]
    base = "https://www.googleapis.com/youtube/v3"
    p = {"part": "snippet", "type": "video", "order": "date", "maxResults": str(max(5, min(50, limit))), "key": key}
    if kind == "user":
        handle = "@" + _clean_handle(query)
        ch = _json(http, "GET", f"{base}/channels", params={"part": "id,snippet,statistics", "forHandle": handle, "key": key}).get("items") or []
        if not ch:
            raise CollectError("No such YouTube channel.")
        p["channelId"] = ch[0]["id"]
    else:
        p["q"] = ("#" + query.lstrip("#")) if kind == "hashtag" else query
    if since:
        p["publishedAfter"] = since
    items = _json(http, "GET", f"{base}/search", params=p).get("items", [])
    out, newest = [], since or ""
    for it in items[:limit]:
        s = it.get("snippet") or {}
        vid = (it.get("id") or {}).get("videoId", "")
        out.append({"platform": "youtube", "handle": s.get("channelTitle", "") or s.get("channelId", ""), "display_name": s.get("channelTitle", ""),
                    "post_id": vid, "url": f"https://www.youtube.com/watch?v={vid}" if vid else "", "timestamp": s.get("publishedAt", ""),
                    "text": (s.get("title", "") + "\n" + s.get("description", "")).strip(),
                    "profile_url": f"https://www.youtube.com/channel/{s.get('channelId')}" if s.get("channelId") else ""})
        newest = max(newest, s.get("publishedAt", ""))
    return out, newest


def _reddit(cfg, kind, query, limit, since, http):
    code, body = http("POST", "https://www.reddit.com/api/v1/access_token", data={"grant_type": "client_credentials"},
                      auth=(cfg["reddit_client_id"], cfg["reddit_client_secret"]), headers={"User-Agent": cfg.get("reddit_user_agent") or UA})
    if code >= 400:
        raise CollectError("Reddit refused the client id and secret.")
    try:
        token = json.loads(body)["access_token"]
    except (ValueError, KeyError):
        raise CollectError("Reddit did not return a token.")
    h = {"Authorization": f"bearer {token}", "User-Agent": cfg.get("reddit_user_agent") or UA}
    lim = str(max(5, min(100, limit)))
    if kind == "user":
        d = _json(http, "GET", f"https://oauth.reddit.com/user/{quote(_clean_handle(query))}/submitted", params={"limit": lim, "sort": "new"}, headers=h)
    else:
        d = _json(http, "GET", "https://oauth.reddit.com/search", params={"q": query, "limit": lim, "sort": "new", "type": "link"}, headers=h)
    out, newest = [], float(since or 0)
    for c in (d.get("data") or {}).get("children", [])[:limit]:
        x = c.get("data") or {}
        ts = float(x.get("created_utc") or 0)
        if since and ts <= float(since):
            continue
        out.append({"platform": "reddit", "handle": x.get("author", ""), "post_id": x.get("name", ""), "url": "https://www.reddit.com" + x.get("permalink", ""),
                    "timestamp": datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else "",
                    "text": (x.get("title", "") + "\n" + (x.get("selftext") or "") + f"\n[r/{x.get('subreddit', '')}]").strip(),
                    "likes": x.get("score"), "replies": x.get("num_comments")})
        newest = max(newest, ts)
    return out, str(newest) if newest else since


def _mastodon(cfg, kind, query, limit, since, http):
    inst = cfg.get("mastodon_instance") or "mastodon.social"
    q = query.strip().lstrip("@")
    if kind == "user" and "@" in q:
        q, inst = q.split("@", 1)
    h = {"Authorization": f"Bearer {cfg['mastodon_token']}"} if cfg.get("mastodon_token") else {}
    base = f"https://{inst.strip('/')}/api"
    lim = str(max(5, min(40, limit)))
    p = {"limit": lim}
    if since:
        p["since_id"] = since
    if kind == "user":
        acc = _json(http, "GET", f"{base}/v1/accounts/lookup", params={"acct": q}, headers=h)
        sts = _json(http, "GET", f"{base}/v1/accounts/{acc['id']}/statuses", params={**p, "exclude_reblogs": "true"}, headers=h)
    elif kind == "hashtag":
        sts = _json(http, "GET", f"{base}/v1/timelines/tag/{quote(q.lstrip('#'))}", params=p, headers=h)
    else:
        if not h:
            raise CollectError("Keyword search on Mastodon servers needs an access token. Use a hashtag, or ask the administrator to set MASTODON_TOKEN.")
        r = _json(http, "GET", f"{base}/v2/search", params={"q": q, "type": "statuses", "limit": lim}, headers=h)
        sts = r.get("statuses", [])
    out = []
    for s in sts[:limit]:
        a = s.get("account") or {}
        out.append({"platform": "mastodon", "handle": a.get("acct", ""), "display_name": a.get("display_name", ""), "bio": _strip_html(a.get("note", "")),
                    "followers": a.get("followers_count"), "following": a.get("following_count"), "profile_url": a.get("url", ""),
                    "post_id": s.get("id", ""), "url": s.get("url", ""), "timestamp": s.get("created_at", ""), "text": _strip_html(s.get("content", "")),
                    "likes": s.get("favourites_count"), "shares": s.get("reblogs_count"), "replies": s.get("replies_count"),
                    "mentions": [m.get("acct") for m in s.get("mentions", []) if m.get("acct")]})
    ids = [s["id"] for s in sts if str(s.get("id", "")).isdigit()]
    return out, max(ids, key=int) if ids else since


def _rss(cfg, kind, query, limit, since, http):
    url = query.strip()
    if not re.match(r"(?i)^https?://", url):
        raise CollectError("Enter the full feed address starting with http:// or https://")
    code, body = http("GET", url, headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
    if code >= 400:
        raise CollectError(f"The feed could not be read ({code}).")
    if re.search(r"<!DOCTYPE|<!ENTITY", body[:4000], re.I):
        raise CollectError("That feed uses features that are not allowed for safety.")
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise CollectError("That address did not return a readable RSS or Atom feed.")
    host = urlparse(url).hostname or "feed"
    strip = lambda tag: tag.split("}", 1)[-1]  # noqa: E731
    out = []
    for el in root.iter():
        if strip(el.tag) not in ("item", "entry"):
            continue
        f = {strip(c.tag): c for c in el}
        def tx(k):
            return (f[k].text or "").strip() if k in f and f[k] is not None else ""
        link = tx("link") or (f["link"].get("href", "") if "link" in f else "")
        when = tx("pubDate") or tx("published") or tx("updated")
        try:
            when = parsedate_to_datetime(when).isoformat() if when and not re.match(r"\d{4}-\d{2}-\d{2}", when) else when
        except (TypeError, ValueError):
            pass
        summary = _strip_html(tx("description") or tx("summary") or tx("content"))
        out.append({"platform": "web", "handle": (tx("author") or tx("creator") or host), "display_name": host, "post_id": tx("guid") or tx("id") or link,
                    "url": link, "timestamp": when, "text": (tx("title") + "\n" + summary).strip()})
        if len(out) >= limit:
            break
    return out, since


def _demo(cfg, kind, query, limit, since, http):
    try:
        data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CollectError("The bundled sample is missing.")
    q = query.strip().lstrip("@#").lower()
    posts = data.get("posts", [])
    accts = {(a.get("platform"), str(a.get("handle", "")).lower()): a for a in data.get("accounts", [])}
    hit = []
    for p in posts:
        text = str(p.get("text") or p.get("content") or "").lower()
        hd = str(p.get("handle") or p.get("username") or "").lower()
        if (kind == "user" and hd == q) or (kind == "hashtag" and ("#" + q) in text) or (kind == "keyword" and q in text):
            hit.append(p)
    out = []
    for p in hit[:limit]:
        a = accts.get((p.get("platform"), str(p.get("handle") or "").lower()), {})
        out.append({**a, **p, "synthetic": True})
    if not out and kind == "user":
        out = [a for (pl, hd), a in accts.items() if hd == q][:limit]
    return out, since


_FETCH: dict[str, Callable] = {"x": _x, "youtube": _youtube, "reddit": _reddit, "mastodon": _mastodon, "rss": _rss, "demo": _demo}


def fetch(provider: str, kind: str, query: str, limit: int, since: str, cfg: dict, http: Callable | None = None):
    """-> (raw_records, next_cursor). Raises CollectError with an officer-readable message."""
    p = PROVIDERS.get(provider)
    if not p:
        raise CollectError("Unknown provider.")
    if kind not in p.kinds:
        raise CollectError(f"{p.label} cannot search by {KIND_LABEL.get(kind, kind).lower()}.")
    missing = [k.upper() for k in p.env if not cfg.get(k)]
    if missing:
        raise CollectError(f"{p.label} is not set up on this server. The administrator must set {', '.join(missing)}.")
    query = (query or "").strip()
    if not query or len(query) > 300:
        raise CollectError("Enter what to search for (up to 300 characters).")
    return _FETCH[provider](cfg, kind, query, max(1, min(int(limit), 100)), since or "", http or default_http)
