"""Automatic collection: saved searches through official APIs / public feeds (all network access is faked here)."""
import json
import uuid

import pytest

from ai_engine.social import collectors as col
from backend.app.core.config import settings
from backend.app.features import social_collect as SC

BASIS = "Public posts collected under notice ref 12/2026, Sec 94 BNSS"


def _case(client, login):
    cn = f"CASE-COL-{uuid.uuid4().hex[:6].upper()}"
    sup, admin = login("supervisor"), login("admin")
    assert client.post("/cases", json={"case_number": cn, "title": "collect"}, headers=sup).status_code == 200
    client.patch(f"/admin/cases/{cn}/visibility", json={"visibility": "shared"}, headers=admin)
    return cn, sup


def _job(client, cn, h, **kw):
    body = {"provider": "demo", "kind": "hashtag", "query": "DoubleMoney", "legal_basis": BASIS, "max_posts": 50, "every_minutes": 0, "run_now": True, **kw}
    return client.post(f"/social/{cn}/collect/jobs", json=body, headers=h)


class Fake:
    """Stands in for the network: maps a URL prefix to (status, body). Records every call."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, params=None, headers=None, data=None, auth=None):
        self.calls.append((method, url, params or {}, headers or {}))
        for prefix, (code, body) in self.routes.items():
            if url.startswith(prefix):
                return code, body if isinstance(body, str) else json.dumps(body)
        return 404, "{}"


# ------------------------------------------------------------------------------------------ service
def test_providers_listing_reports_what_is_configured(client, login):
    r = client.get("/social/collect/providers", headers=login("supervisor")).json()
    by = {p["id"]: p for p in r["providers"]}
    assert by["demo"]["configured"] and by["mastodon"]["configured"] and by["rss"]["configured"]
    assert not by["x"]["configured"] and by["x"]["missing"] == ["X_BEARER_TOKEN"]
    assert "scraped" in r["notice"]


def test_demo_collection_imports_posts_and_records_authority(client, login):
    cn, sup = _case(client, login)
    r = _job(client, cn, sup)
    assert r.status_code == 200, r.text
    assert r.json()["result"]["posts_added"] >= 3 and r.json()["job"] is None        # one-off: nothing saved
    imps = client.get(f"/social/{cn}/imports", headers=sup).json()["imports"]
    assert imps[0]["source_type"] == "collected" and "[synthetic]" in imps[0]["source"] and "notice ref 12/2026" in imps[0]["legal_basis"]
    assert client.get(f"/social/{cn}/posts", headers=sup).json()["total"] >= 3
    actions = [e["action"] for e in client.get("/audit", headers=login("admin")).json()["events"]]
    assert "social.collected" in actions and "social.collect_job_created" in actions


def test_saved_search_reruns_without_duplicating(client, login):
    cn, sup = _case(client, login)
    j = _job(client, cn, sup, every_minutes=60).json()["job"]
    assert j["every_minutes"] == 60 and j["total_added"] >= 3 and j["last_status"].startswith("OK")
    r = client.post(f"/social/{cn}/collect/jobs/{j['id']}/run", headers=sup).json()
    assert r["result"]["posts_added"] == 0 and r["result"]["duplicates_skipped"] >= 3
    assert client.post(f"/social/{cn}/collect/jobs/{j['id']}/toggle", headers=sup).json()["job"]["active"] is False
    assert client.delete(f"/social/{cn}/collect/jobs/{j['id']}", headers=sup).status_code == 200
    assert client.get(f"/social/{cn}/collect/jobs", headers=sup).json()["jobs"] == []


def test_rules_are_enforced(client, login):
    cn, sup = _case(client, login)
    assert _job(client, cn, sup, provider="x", kind="user", query="someone").status_code == 409          # no key configured
    assert _job(client, cn, sup, provider="rss", kind="user").status_code == 400                          # rss only reads feeds
    assert _job(client, cn, sup, legal_basis="just because").status_code == 400                        # authority needed (3+ words)
    assert _job(client, cn, sup, legal_basis="short").status_code == 422
    assert _job(client, cn, sup, every_minutes=2).status_code == 400                                     # not more often than the minimum
    assert _job(client, cn, login("investigator")).status_code == 403                                     # verify-only role cannot collect
    assert _job(client, cn, login("auditor")).status_code == 403
    assert client.get(f"/social/{cn}/collect/jobs", headers=login("investigator")).status_code in (200, 403)


def test_due_searches_run_once_per_interval(client, login):
    cn, sup = _case(client, login)
    j = _job(client, cn, sup, every_minutes=60, run_now=False).json()["job"]
    assert j["last_status"] == "Not run yet"
    from backend.app import features as F
    real = F.CTX
    assert SC.run_due(real) == 1
    assert SC.run_due(real) == 0                                                                          # not due again yet


def test_watchlist_pass_uses_active_terms(client, login):
    cn, sup = _case(client, login)
    assert client.post(f"/social/{cn}/intel/watch", json={"kind": "hashtag", "value": "DoubleMoney"}, headers=sup).status_code == 200
    assert client.post(f"/social/{cn}/intel/watch", json={"kind": "phone", "value": "+91 90000 11223"}, headers=sup).status_code == 200
    r = client.post(f"/social/{cn}/collect/watchlist", json={"provider": "demo", "legal_basis": BASIS}, headers=sup)
    assert r.status_code == 200 and [x["kind"] for x in r.json()["results"]] == ["hashtag"] and r.json()["posts_added"] >= 3
    cn2, sup2 = _case(client, login)
    assert client.post(f"/social/{cn2}/collect/watchlist", json={"provider": "demo", "legal_basis": BASIS}, headers=sup2).status_code == 400


# ------------------------------------------------------------------------------------------ providers (faked network)
CFG = {"x_bearer_token": "tok", "youtube_api_key": "k", "reddit_client_id": "id", "reddit_client_secret": "sec", "mastodon_instance": "masto.test", "mastodon_token": ""}


def test_x_user_and_search():
    f = Fake({"https://api.x.com/2/users/by/username/": (200, {"data": {"id": "77", "username": "rahul", "name": "Rahul", "description": "bio", "public_metrics": {"followers_count": 9, "following_count": 3}}}),
              "https://api.x.com/2/users/77/tweets": (200, {"data": [{"id": "105", "text": "hello @friend", "created_at": "2026-05-01T10:00:00Z", "public_metrics": {"like_count": 2, "retweet_count": 1, "reply_count": 0},
                                                                  "entities": {"mentions": [{"username": "friend"}]}}, {"id": "101", "text": "older", "created_at": "2026-05-01T09:00:00Z"}]}),
              "https://api.x.com/2/tweets/search/recent": (200, {"data": [{"id": "9", "text": "#scam now", "author_id": "77", "created_at": "2026-05-02T10:00:00Z"}],
                                                                  "includes": {"users": [{"id": "77", "username": "rahul", "name": "Rahul"}]}})})
    recs, cursor = col.fetch("x", "user", "@Rahul", 50, "", CFG, f)
    assert cursor == "105" and recs[0]["handle"] == "rahul" and recs[0]["url"].endswith("/rahul/status/105") and recs[0]["mentions"] == ["friend"]
    assert f.calls[0][3]["Authorization"] == "Bearer tok"
    recs, _ = col.fetch("x", "hashtag", "scam", 10, "", CFG, f)
    assert recs[0]["handle"] == "rahul" and f.calls[-1][2]["query"].startswith("#scam")
    from ai_engine.social import ingest
    assert ingest.normalise_record(recs[0], "UTC")["handle"] == "rahul"


def test_x_errors_are_readable():
    with pytest.raises(col.CollectError, match="check the API key"):
        col.fetch("x", "keyword", "abc", 10, "", CFG, Fake({"https://api.x.com": (401, "{}")}))
    with pytest.raises(col.CollectError, match="rate-limiting"):
        col.fetch("x", "keyword", "abc", 10, "", CFG, Fake({"https://api.x.com": (429, "{}")}))
    with pytest.raises(col.CollectError, match="not set up"):
        col.fetch("x", "keyword", "abc", 10, "", {}, Fake({}))


def test_mastodon_reddit_youtube_rss():
    m = Fake({"https://masto.test/api/v1/timelines/tag/": (200, [{"id": "12", "content": "<p>hi <b>there</b></p>", "created_at": "2026-05-01T10:00:00Z", "url": "https://masto.test/@a/12",
                                                                   "account": {"acct": "a", "display_name": "A", "note": "<p>bio</p>"}, "favourites_count": 3, "mentions": [{"acct": "b"}]}])})
    recs, cur = col.fetch("mastodon", "hashtag", "#scam", 10, "", CFG, m)
    assert recs[0]["text"] == "hi there" and recs[0]["bio"] == "bio" and cur == "12"
    with pytest.raises(col.CollectError, match="needs an access token"):
        col.fetch("mastodon", "keyword", "abc", 10, "", CFG, m)

    r = Fake({"https://www.reddit.com/api/v1/access_token": (200, {"access_token": "t"}),
              "https://oauth.reddit.com/search": (200, {"data": {"children": [{"data": {"author": "u1", "name": "t3_a", "permalink": "/r/x/a/", "created_utc": 1780000000, "title": "Title", "selftext": "body",
                                                                                          "subreddit": "x", "score": 5, "num_comments": 2}}]}})})
    recs, cur = col.fetch("reddit", "keyword", "abc", 10, "", CFG, r)
    assert recs[0]["handle"] == "u1" and "Title" in recs[0]["text"] and cur

    y = Fake({"https://www.googleapis.com/youtube/v3/channels": (200, {"items": [{"id": "UC1"}]}),
              "https://www.googleapis.com/youtube/v3/search": (200, {"items": [{"id": {"videoId": "v1"}, "snippet": {"channelTitle": "Chan", "channelId": "UC1", "title": "T", "description": "D",
                                                                                                                 "publishedAt": "2026-05-01T00:00:00Z"}}]})})
    recs, cur = col.fetch("youtube", "user", "chan", 10, "", CFG, y)
    assert recs[0]["url"].endswith("v1") and cur == "2026-05-01T00:00:00Z"
    assert y.calls[1][2]["channelId"] == "UC1"

    feed = "<?xml version='1.0'?><rss><channel><item><title>News</title><link>https://n.test/1</link><description>&lt;p&gt;Body&lt;/p&gt;</description><pubDate>Fri, 01 May 2026 10:00:00 GMT</pubDate></item></channel></rss>"
    recs, _ = col.fetch("rss", "feed", "https://n.test/feed", 10, "", CFG, Fake({"https://n.test/feed": (200, feed)}))
    assert recs[0]["text"].startswith("News\nBody") and recs[0]["timestamp"].startswith("2026-05-01T10:00:00")
    evil = "<?xml version='1.0'?><!DOCTYPE x [<!ENTITY a 'b'>]><rss/>"
    with pytest.raises(col.CollectError, match="not allowed"):
        col.fetch("rss", "feed", "https://n.test/feed", 10, "", CFG, Fake({"https://n.test/feed": (200, evil)}))


def test_real_network_layer_refuses_private_addresses():
    for url in ("http://127.0.0.1/feed", "http://localhost/x", "http://10.0.0.5/x", "http://169.254.169.254/latest/meta-data", "ftp://example.com/x"):
        with pytest.raises(col.CollectError):
            col.default_http("GET", url)


def test_collect_once_through_the_service_with_a_fake_network(client, login, monkeypatch):
    cn, sup = _case(client, login)
    monkeypatch.setattr(settings, "mastodon_token", "")
    fake = Fake({"https://mastodon.social/api/v1/timelines/tag/": (200, [{"id": "31", "content": "<p>Send money on UPI now #scam</p>", "created_at": "2026-05-01T10:00:00Z",
                                                                          "url": "https://mastodon.social/@z/31", "account": {"acct": "z", "display_name": "Z"}}])})
    monkeypatch.setattr(SC, "HTTP", fake)
    r = _job(client, cn, sup, provider="mastodon", kind="hashtag", query="scam")
    assert r.status_code == 200 and r.json()["result"]["posts_added"] == 1
    posts = client.get(f"/social/{cn}/posts", headers=sup).json()["posts"]
    assert posts[0]["platform"] == "mastodon" and "UPI" in posts[0]["text"]
    monkeypatch.setattr(SC, "HTTP", Fake({}))                                                          # server returns 404 for everything
    r = _job(client, cn, sup, provider="mastodon", kind="user", query="nobody")
    assert r.status_code == 502 and "Not found" in r.json()["detail"]
    assert client.get(f"/social/{cn}/collect/jobs", headers=sup).json()["jobs"] == []                  # failed one-off is not kept
