#!/usr/bin/env python3
"""
TRIDENT Geo-Intelligence Service
Aggregates ACLED, GDELT, and open RSS feeds
Runs on port 5003
"""
import os, time, datetime, requests, xml.etree.ElementTree as ET
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

ACLED_EMAIL    = os.environ.get("ACLED_EMAIL", "")
ACLED_PASSWORD = os.environ.get("ACLED_PASSWORD", "")
ACLED_BASE     = "https://acleddata.com/api"

# Session for cookie-based ACLED auth (new system post-Sep 2025)
ACLED_SESSION  = None
ACLED_SESSION_TS = 0

CACHE = {}
CACHE_TTL = 900  # 15 min

def get_cached(key):
    if key in CACHE and time.time() - CACHE[key]["ts"] < CACHE_TTL:
        return CACHE[key]["data"]
    return None

def set_cached(key, data):
    CACHE[key] = {"data": data, "ts": time.time()}

def get_acled_session():
    global ACLED_SESSION, ACLED_SESSION_TS
    if ACLED_SESSION and time.time() - ACLED_SESSION_TS < 82800:
        return ACLED_SESSION
    if not ACLED_EMAIL or not ACLED_PASSWORD:
        return None
    try:
        s = requests.Session()
        # Cookie-based login (new ACLED auth system)
        res = s.post(
            "https://acleddata.com/wp-login.php",
            data={
                "log": ACLED_EMAIL,
                "pwd": ACLED_PASSWORD,
                "wp-submit": "Log+In",
                "redirect_to": "https://acleddata.com/api/acled/read?limit=1",
                "testcookie": "1"
            },
            headers={"User-Agent": "TRIDENT/1.0"},
            timeout=15,
            allow_redirects=True
        )
        if res.status_code == 200 and "wordpress_logged_in" in str(s.cookies):
            ACLED_SESSION = s
            ACLED_SESSION_TS = time.time()
            return s
        # Fallback: try the old token endpoint just in case
        res2 = s.post(
            "https://acleddata.com/api/authenticate/",
            json={"email": ACLED_EMAIL, "password": ACLED_PASSWORD},
            timeout=10
        )
        if res2.status_code == 200:
            ACLED_SESSION = s
            ACLED_SESSION_TS = time.time()
            return s
    except Exception as e:
        print(f"ACLED auth error: {e}")
    return None

def acled_get(endpoint, params=None):
    s = get_acled_session()
    if not s:
        return None
    try:
        r = s.get(f"{ACLED_BASE}{endpoint}", params=params, timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"ACLED request error: {e}")
    return None

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "TRIDENT Geo-Intelligence", "acled_configured": bool(ACLED_EMAIL and ACLED_PASSWORD)})

@app.route("/acled/events")
def acled_events():
    cached = get_cached("acled_events")
    if cached:
        return jsonify(cached)
    data = acled_get("/acled/read", {
        "limit": 100,
        "fields": "event_id_cnty|event_date|event_type|sub_event_type|actor1|actor2|country|admin1|location|latitude|longitude|fatalities|notes|source",
        "date_range_start": (datetime.datetime.utcnow() - datetime.timedelta(days=30)).strftime("%Y-%m-%d"),
        "date_range_end": datetime.datetime.utcnow().strftime("%Y-%m-%d"),
    })
    if not data:
        return jsonify({"error": "ACLED not configured or auth failed", "events": [], "count": 0}), 200
    events = data.get("data", [])
    result = {"events": events, "count": len(events), "source": "ACLED"}
    set_cached("acled_events", result)
    return jsonify(result)

@app.route("/acled/summary")
def acled_summary():
    cached = get_cached("acled_summary")
    if cached:
        return jsonify(cached)
    data = acled_get("/acled/read", {
        "limit": 500,
        "fields": "event_type|country|fatalities",
        "date_range_start": (datetime.datetime.utcnow() - datetime.timedelta(days=7)).strftime("%Y-%m-%d"),
        "date_range_end": datetime.datetime.utcnow().strftime("%Y-%m-%d"),
    })
    if not data:
        return jsonify({"error": "ACLED not configured or auth failed", "summary": []}), 200
    events = data.get("data", [])
    by_country = {}
    for e in events:
        c = e.get("country", "Unknown")
        if c not in by_country:
            by_country[c] = {"country": c, "events": 0, "fatalities": 0, "types": {}}
        by_country[c]["events"] += 1
        by_country[c]["fatalities"] += int(e.get("fatalities", 0) or 0)
        etype = e.get("event_type", "Unknown")
        by_country[c]["types"][etype] = by_country[c]["types"].get(etype, 0) + 1
    summary = sorted(by_country.values(), key=lambda x: x["events"], reverse=True)[:30]
    result = {"summary": summary, "total_events": len(events), "source": "ACLED", "period_days": 7}
    set_cached("acled_summary", result)
    return jsonify(result)

@app.route("/threat-summary")
def threat_summary():
    data = acled_get("/acled/read", {
        "limit": 200,
        "fields": "event_type|country|fatalities|latitude|longitude|event_date",
        "date_range_start": (datetime.datetime.utcnow() - datetime.timedelta(days=7)).strftime("%Y-%m-%d"),
        "date_range_end": datetime.datetime.utcnow().strftime("%Y-%m-%d"),
    })
    acled_data = {}
    if data:
        events = data.get("data", [])
        total_fatalities = sum(int(e.get("fatalities", 0) or 0) for e in events)
        hotspots = {}
        for e in events:
            c = e.get("country", "Unknown")
            hotspots[c] = hotspots.get(c, 0) + 1
        top_hotspots = sorted(hotspots.items(), key=lambda x: x[1], reverse=True)[:5]
        acled_data = {
            "total_events_7d": len(events),
            "total_fatalities_7d": total_fatalities,
            "top_hotspots": [{"country": c, "events": n} for c, n in top_hotspots],
            "event_points": [{"lat": float(e.get("latitude", 0)), "lon": float(e.get("longitude", 0)), "type": e.get("event_type", ""), "country": e.get("country", ""), "fatalities": int(e.get("fatalities", 0) or 0)} for e in events if e.get("latitude") and e.get("longitude")]
        }
    else:
        acled_data = {"error": "ACLED not configured", "total_events_7d": 0, "total_fatalities_7d": 0, "top_hotspots": [], "event_points": []}
    return jsonify({"acled": acled_data, "timestamp": datetime.datetime.utcnow().isoformat(), "source": "ACLED + Open RSS"})

def parse_rss(feeds, limit=5):
    articles = []
    for source_name, feed_url in feeds:
        try:
            res = requests.get(feed_url, timeout=8, headers={"User-Agent": "TRIDENT/1.0"})
            root = ET.fromstring(res.content)
            items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
            for item in items[:limit]:
                title = item.findtext("title") or item.findtext("{http://www.w3.org/2005/Atom}title") or ""
                link  = item.findtext("link") or ""
                pub   = item.findtext("pubDate") or item.findtext("{http://www.w3.org/2005/Atom}published") or ""
                desc  = item.findtext("description") or item.findtext("{http://www.w3.org/2005/Atom}summary") or ""
                if title:
                    articles.append({"title": title.strip(), "link": link.strip() if isinstance(link, str) else "", "published": pub.strip(), "description": desc.strip()[:300] if desc else "", "source": source_name})
        except:
            pass
    return articles

@app.route("/news/military")
def military_news():
    cached = get_cached("military_news")
    if cached:
        return jsonify(cached)
    feeds = [
        ("Defense News",    "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml"),
        ("Breaking Defense","https://breakingdefense.com/feed/"),
        ("War on the Rocks","https://warontherocks.com/feed/"),
        ("Military Times",  "https://www.militarytimes.com/rss/"),
        ("Reuters World",   "https://feeds.reuters.com/Reuters/worldNews"),
        ("AP World",        "https://feeds.apnews.com/apf-worldnews"),
    ]
    articles = parse_rss(feeds)
    result = {"articles": articles[:50], "sources": [f[0] for f in feeds]}
    set_cached("military_news", result)
    return jsonify(result)

@app.route("/news/geopolitical")
def geopolitical_news():
    cached = get_cached("geo_news")
    if cached:
        return jsonify(cached)
    feeds = [
        ("Foreign Policy",  "https://foreignpolicy.com/feed/"),
        ("BBC World",       "http://feeds.bbci.co.uk/news/world/rss.xml"),
        ("Al Jazeera",      "https://www.aljazeera.com/xml/rss/all.xml"),
        ("AP World",        "https://feeds.apnews.com/apf-worldnews"),
        ("Reuters Politics","https://feeds.reuters.com/Reuters/PoliticsNews"),
        ("The Guardian",    "https://www.theguardian.com/world/rss"),
    ]
    articles = parse_rss(feeds)
    result = {"articles": articles[:50], "sources": [f[0] for f in feeds]}
    set_cached("geo_news", result)
    return jsonify(result)

if __name__ == "__main__":
    print("[ TRIDENT ] Geo-Intelligence Service starting on port 5003")
    app.run(host="127.0.0.1", port=5003, debug=False)
