"""TDX Bus - Multi-City Bus Real-Time ETA Dashboard.

Flask backend that proxies TDX Bus API calls with OAuth2 authentication.
Supports multi-city route search, daily route/stop caching, and batch
ETA queries for group-based dashboards.
"""

import json
import time
import logging
import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from flask import Flask, jsonify, render_template, request
from dotenv import dotenv_values

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
ENV_FILE = Path(r"e:\Git\GitHub\Kevinhua\KVH\.env")
TOKEN_FILE = APP_DIR / "tdx_token_cache.json"
CACHE_DIR = APP_DIR / "tdx_bus_cache"
CACHE_DIR.mkdir(exist_ok=True)

TOKEN_URL = (
    "https://tdx.transportdata.tw/auth/realms/TDXConnect"
    "/protocol/openid-connect/token"
)
API_BASE = "https://tdx.transportdata.tw/api/basic"

# All TDX-supported cities
CITIES = {
    "Taipei": "Taipei",
    "NewTaipei": "NewTaipei",
    "Taoyuan": "Taoyuan",
    "Taichung": "Taichung",
    "Tainan": "Tainan",
    "Kaohsiung": "Kaohsiung",
    "Keelung": "Keelung",
    "Hsinchu": "Hsinchu",
    "HsinchuCounty": "HsinchuCounty",
    "MiaoliCounty": "MiaoliCounty",
    "ChanghuaCounty": "ChanghuaCounty",
    "NantouCounty": "NantouCounty",
    "YunlinCounty": "YunlinCounty",
    "ChiayiCounty": "ChiayiCounty",
    "Chiayi": "Chiayi",
    "PingtungCounty": "PingtungCounty",
    "YilanCounty": "YilanCounty",
    "HualienCounty": "HualienCounty",
    "TaitungCounty": "TaitungCounty",
    "KinmenCounty": "KinmenCounty",
    "PenghuCounty": "PenghuCounty",
    "LienchiangCounty": "LienchiangCounty",
}

CITY_NAMES_ZH = {
    "Taipei": "\u81fa\u5317\u5e02",
    "NewTaipei": "\u65b0\u5317\u5e02",
    "Taoyuan": "\u6843\u5712\u5e02",
    "Taichung": "\u81fa\u4e2d\u5e02",
    "Tainan": "\u81fa\u5357\u5e02",
    "Kaohsiung": "\u9ad8\u96c4\u5e02",
    "Keelung": "\u57fa\u9686\u5e02",
    "Hsinchu": "\u65b0\u7af9\u5e02",
    "HsinchuCounty": "\u65b0\u7af9\u7e23",
    "MiaoliCounty": "\u82d7\u6817\u7e23",
    "ChanghuaCounty": "\u5f70\u5316\u7e23",
    "NantouCounty": "\u5357\u6295\u7e23",
    "YunlinCounty": "\u96f2\u6797\u7e23",
    "ChiayiCounty": "\u5609\u7fa9\u7e23",
    "Chiayi": "\u5609\u7fa9\u5e02",
    "PingtungCounty": "\u5c4f\u6771\u7e23",
    "YilanCounty": "\u5b9c\u862d\u7e23",
    "HualienCounty": "\u82b1\u84ee\u7e23",
    "TaitungCounty": "\u81fa\u6771\u7e23",
    "KinmenCounty": "\u91d1\u9580\u7e23",
    "PenghuCounty": "\u6f8e\u6e56\u7e23",
    "LienchiangCounty": "\u9023\u6c5f\u7e23",
}

DEFAULT_CITIES = ["Taipei", "NewTaipei"]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("TDX_Bus")

# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------

CLIENT_ID = ""
CLIENT_SECRET = ""


def _load_credentials():
    """Load TDX credentials from .env file or environment variables."""
    global CLIENT_ID, CLIENT_SECRET

    # Try .env file first
    if ENV_FILE.exists():
        env = dotenv_values(ENV_FILE)
        CLIENT_ID = env.get("TDX_CLIENT_ID", "").strip().strip('"')
        CLIENT_SECRET = env.get("TDX_CLIENT_SECRET", "").strip().strip('"')
        if CLIENT_ID and CLIENT_SECRET:
            log.info("Credentials loaded from %s", ENV_FILE)
            return

    # Fallback to OS environment variables
    import os
    CLIENT_ID = os.environ.get("TDX_CLIENT_ID", "").strip()
    CLIENT_SECRET = os.environ.get("TDX_CLIENT_SECRET", "").strip()
    if CLIENT_ID and CLIENT_SECRET:
        log.info("Credentials loaded from environment variables")
    else:
        log.warning("No TDX credentials found! API calls will fail.")


# ---------------------------------------------------------------------------
# Token management (reuses TDX_Billboard pattern)
# ---------------------------------------------------------------------------

def _load_token():
    """Load cached token if still valid (with 30s buffer)."""
    if not TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        if time.time() < data.get("expires_at", 0) - 30:
            return data["access_token"]
    except (json.JSONDecodeError, KeyError, OSError):
        pass
    return None


def _fetch_token():
    """Request a new OAuth2 token from TDX."""
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError("TDX credentials not configured")

    resp = requests.post(
        TOKEN_URL,
        headers={"content-type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=15,
    )
    resp.raise_for_status()
    j = resp.json()

    cache = {
        "access_token": j["access_token"],
        "expires_at": time.time() + int(j.get("expires_in", 1800)),
    }

    # Atomic write
    tmp = TOKEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache), encoding="utf-8")
    tmp.replace(TOKEN_FILE)

    log.info("New TDX token acquired (expires in %ss)", j.get("expires_in", "?"))
    return cache["access_token"]


def _get_token():
    """Get a valid token (cache-first, fetch if expired)."""
    token = _load_token()
    if token:
        return token
    return _fetch_token()


def _api_get(path, params=None):
    """GET from TDX API with Bearer token. Auto-retry on 401."""
    url = f"{API_BASE}{path}"
    if params is None:
        params = {}
    params["$format"] = "JSON"

    token = _get_token()
    headers = {"Authorization": f"Bearer {token}"}

    resp = requests.get(url, headers=headers, params=params, timeout=20)

    # On 401, clear cache and retry once
    if resp.status_code == 401:
        log.warning("401 from TDX, refreshing token...")
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()
        token = _fetch_token()
        headers["Authorization"] = f"Bearer {token}"
        resp = requests.get(url, headers=headers, params=params, timeout=20)

    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Daily cache (TDX_Billboard pattern)
# ---------------------------------------------------------------------------

def _today_str():
    """Return today's date as YYYY-MM-DD."""
    return datetime.date.today().isoformat()


def _cache_read(filename):
    """Read a daily cache file. Returns data if valid for today, else None."""
    fpath = CACHE_DIR / filename
    if not fpath.exists():
        return None
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
        if data.get("CachedDate") == _today_str():
            return data.get("Data")
    except (json.JSONDecodeError, KeyError, OSError):
        pass
    return None


def _cache_write(filename, data):
    """Write data to a daily cache file."""
    fpath = CACHE_DIR / filename
    payload = {"CachedDate": _today_str(), "Data": data}
    tmp = fpath.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(fpath)


# ---------------------------------------------------------------------------
# Multi-city route fetching
# ---------------------------------------------------------------------------

def _fetch_routes_for_city(city):
    """Fetch all routes for a city (with daily cache)."""
    cache_file = f"{city}_routes.json"
    cached = _cache_read(cache_file)
    if cached is not None:
        return cached

    path = f"/v2/Bus/Route/City/{city}"
    params = {"$select": "RouteUID,RouteID,RouteName,DepartureStopNameZh,DestinationStopNameZh"}
    try:
        data = _api_get(path, params)
    except Exception as e:
        log.error("Failed to fetch routes for %s: %s", city, e)
        return []

    routes = []
    for r in data:
        routes.append({
            "RouteUID": r.get("RouteUID", ""),
            "RouteID": r.get("RouteID", ""),
            "RouteName": r.get("RouteName", {}).get("Zh_tw", ""),
            "RouteNameEn": r.get("RouteName", {}).get("En", ""),
            "Departure": r.get("DepartureStopNameZh", ""),
            "Destination": r.get("DestinationStopNameZh", ""),
        })
    _cache_write(cache_file, routes)
    log.info("Cached %d routes for %s", len(routes), city)
    return routes


def _search_routes_multi_city(query, cities):
    """Search routes across multiple cities in parallel."""
    q_lower = query.lower()
    results = []

    def _search_city(city):
        all_routes = _fetch_routes_for_city(city)
        matched = [
            r for r in all_routes
            if q_lower in r["RouteName"].lower()
            or q_lower in r.get("RouteNameEn", "").lower()
        ]
        matched.sort(key=lambda x: x["RouteName"])
        return city, matched[:20]

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_search_city, c): c for c in cities}
        for fut in as_completed(futures):
            try:
                city, matched = fut.result()
                if matched:
                    results.append({
                        "City": city,
                        "CityNameZH": CITY_NAMES_ZH.get(city, city),
                        "Routes": matched,
                    })
            except Exception as e:
                log.error("Search error for %s: %s", futures[fut], e)

    # Sort by city order (keep default cities first)
    city_order = {c: i for i, c in enumerate(cities)}
    results.sort(key=lambda x: city_order.get(x["City"], 999))
    return results


# ---------------------------------------------------------------------------
# Stop sequence (with daily cache)
# ---------------------------------------------------------------------------

def _fetch_stops(city, route_name):
    """Fetch stop sequence for a route with daily cache."""
    safe_name = route_name.replace("/", "_").replace("\\", "_")
    cache_file = f"{city}_{safe_name}_stops.json"
    cached = _cache_read(cache_file)
    if cached is not None:
        return cached

    path = f"/v2/Bus/DisplayStopOfRoute/City/{city}/{route_name}"
    try:
        data = _api_get(path)
    except Exception as e:
        log.error("Failed to fetch stops for %s/%s: %s", city, route_name, e)
        return []

    result = []
    for direction_data in data:
        stops = []
        for s in direction_data.get("Stops", []):
            stops.append({
                "StopUID": s.get("StopUID", ""),
                "StopName": s.get("StopName", {}).get("Zh_tw", ""),
                "StopNameEn": s.get("StopName", {}).get("En", ""),
                "StopSequence": s.get("StopSequence", 0),
            })
        result.append({
            "Direction": direction_data.get("Direction", 0),
            "RouteName": direction_data.get("RouteName", {}).get("Zh_tw", ""),
            "Stops": stops,
        })

    _cache_write(cache_file, result)
    return result


# ---------------------------------------------------------------------------
# Batch ETA fetch
# ---------------------------------------------------------------------------

def _fetch_eta_for_route(city, route_name):
    """Fetch ETA for all stops of a route in a city."""
    path = f"/v2/Bus/EstimatedTimeOfArrival/City/{city}/{route_name}"
    try:
        data = _api_get(path)
    except Exception as e:
        log.error("ETA fetch error %s/%s: %s", city, route_name, e)
        return []

    result = []
    for item in data:
        result.append({
            "StopUID": item.get("StopUID", ""),
            "StopName": item.get("StopName", {}).get("Zh_tw", ""),
            "Direction": item.get("Direction", -1),
            "EstimateTime": item.get("EstimateTime"),
            "StopStatus": item.get("StopStatus", -1),
            "NextBusTime": item.get("NextBusTime"),
            "IsLastBus": item.get("IsLastBus"),
        })
    return result


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.route("/")
def index():
    """Serve the main page."""
    return render_template("index.html")


@app.route("/api/cities")
def api_cities():
    """Return list of available cities."""
    result = [
        {"City": k, "CityNameZH": CITY_NAMES_ZH.get(k, k)}
        for k in CITIES
    ]
    return jsonify(result)


@app.route("/api/search")
def api_search():
    """Search routes across multiple cities.

    Query params:
      q       - route name query (required)
      cities  - comma-separated city codes (default: Taipei,NewTaipei)
    """
    try:
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify([])

        cities_param = request.args.get("cities", "").strip()
        if cities_param:
            cities = [c.strip() for c in cities_param.split(",") if c.strip() in CITIES]
        else:
            cities = list(DEFAULT_CITIES)

        if not cities:
            cities = list(DEFAULT_CITIES)

        results = _search_routes_multi_city(q, cities)
        return jsonify(results)
    except Exception as e:
        log.error("api_search error: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/route/<city>/<route_name>/stops")
def api_stops(city, route_name):
    """Get stop sequence for a route in a city (cached daily)."""
    if city not in CITIES:
        return jsonify({"error": "Invalid city"}), 400
    try:
        result = _fetch_stops(city, route_name)
        return jsonify(result)
    except Exception as e:
        log.error("api_stops error: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/route/<city>/<route_name>/eta")
def api_eta(city, route_name):
    """Get ETA for a route in a city (always live)."""
    if city not in CITIES:
        return jsonify({"error": "Invalid city"}), 400
    try:
        result = _fetch_eta_for_route(city, route_name)
        return jsonify(result)
    except Exception as e:
        log.error("api_eta error: %s", e)
        return jsonify({"error": str(e)}), 500


def _estimate_2nd_bus(eta_list, stops_data, direction, target_stop_uid, first_eta):
    """Estimate 2nd bus arrival from earlier stops on the route.

    When the 1st bus is close (< 15 min), it has likely passed stops 3-5
    stations back. Those earlier stops' ETAs then represent the NEXT bus.
    We add estimated inter-stop travel time to get 2nd bus arrival at target.
    """
    # Find stop sequence for this direction
    stop_seq = None
    for d in stops_data:
        if d.get("Direction") == direction:
            stop_seq = d.get("Stops", [])
            break
    if not stop_seq:
        return None

    # Sort by StopSequence
    stop_seq = sorted(stop_seq, key=lambda s: s.get("StopSequence", 0))

    # Find target stop index
    target_idx = None
    for i, s in enumerate(stop_seq):
        if s["StopUID"] == target_stop_uid:
            target_idx = i
            break
    if target_idx is None:
        return None

    # Build ETA lookup by StopUID for this direction
    eta_by_uid = {}
    for e in eta_list:
        if e["Direction"] == direction:
            uid = e["StopUID"]
            # Keep only the first (nearest bus) per stop
            if uid not in eta_by_uid:
                eta_by_uid[uid] = e.get("EstimateTime")

    # Look at stops 3-5 positions earlier
    best_eta2 = None
    for offset in range(3, 6):
        earlier_idx = target_idx - offset
        if earlier_idx < 0:
            break
        earlier_uid = stop_seq[earlier_idx]["StopUID"]
        earlier_eta = eta_by_uid.get(earlier_uid)
        if earlier_eta is None:
            continue

        # If earlier stop's ETA > first bus ETA at target, it's likely a 2nd bus
        if earlier_eta > first_eta:
            # Estimate travel time from earlier stop to target:
            # Use average ~90 sec per stop as heuristic
            travel_time = offset * 90
            estimated_arrival = earlier_eta + travel_time
            if best_eta2 is None or estimated_arrival < best_eta2:
                best_eta2 = estimated_arrival

    return best_eta2


@app.route("/api/batch_eta", methods=["POST"])
def api_batch_eta():
    """Batch ETA fetch for multiple stops across routes/cities.

    Request body: [{"city":"Taipei","routeName":"905","direction":0,"stopUID":"..."}]
    Returns: [{"city","routeName","direction","stopUID","stopName","EstimateTime","StopStatus","IsLastBus"}]
    """
    try:
        items = request.get_json(force=True)
        if not isinstance(items, list):
            return jsonify({"error": "Expected JSON array"}), 400

        # Group by (city, routeName) to deduplicate API calls
        route_groups = {}
        for item in items:
            key = (item.get("city", ""), item.get("routeName", ""))
            if key[0] not in CITIES:
                continue
            if key not in route_groups:
                route_groups[key] = []
            route_groups[key].append(item)

        # Fetch ETA for each unique (city, route) in parallel
        eta_cache = {}

        def _fetch_group(key):
            city, route_name = key
            return key, _fetch_eta_for_route(city, route_name)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_fetch_group, k): k for k in route_groups}
            for fut in as_completed(futures):
                try:
                    key, eta_list = fut.result()
                    eta_cache[key] = eta_list
                except Exception as e:
                    log.error("batch_eta fetch error: %s", e)

        # Pre-fetch stop sequences for estimating 2nd bus
        stops_cache = {}
        for key in route_groups:
            city_k, route_k = key
            try:
                stops_cache[key] = _fetch_stops(city_k, route_k)
            except Exception:
                stops_cache[key] = []

        # Match requested stops to ETA data
        results = []
        for item in items:
            city = item.get("city", "")
            route_name = item.get("routeName", "")
            direction = item.get("direction")
            stop_uid = item.get("stopUID", "")
            key = (city, route_name)

            eta_list = eta_cache.get(key, [])
            # Collect all matching records (multiple buses may approach)
            matches = [e for e in eta_list
                       if e["StopUID"] == stop_uid and e["Direction"] == direction]
            # Sort by EstimateTime ascending (None last)
            matches.sort(key=lambda x: x.get("EstimateTime") if x.get("EstimateTime") is not None else 999999)

            eta2 = None
            if matches:
                match = matches[0]
                # Direct 2nd bus from API (multiple plates)
                if len(matches) > 1:
                    eta2 = matches[1].get("EstimateTime")

                # Estimate 2nd bus from earlier stops when 1st bus < 15 min
                first_eta = match.get("EstimateTime")
                if eta2 is None and first_eta is not None and first_eta <= 900:
                    eta2 = _estimate_2nd_bus(
                        eta_list, stops_cache.get(key, []),
                        direction, stop_uid, first_eta
                    )

                results.append({
                    "city": city,
                    "routeName": route_name,
                    "direction": direction,
                    "stopUID": stop_uid,
                    "stopName": match.get("StopName", ""),
                    "EstimateTime": match.get("EstimateTime"),
                    "StopStatus": match.get("StopStatus", -1),
                    "IsLastBus": match.get("IsLastBus"),
                    "NextBusTime": match.get("NextBusTime"),
                    "EstimateTime2": eta2,
                })
            else:
                results.append({
                    "city": city,
                    "routeName": route_name,
                    "direction": direction,
                    "stopUID": stop_uid,
                    "stopName": item.get("stopName", ""),
                    "EstimateTime": None,
                    "StopStatus": -1,
                    "IsLastBus": None,
                    "NextBusTime": None,
                    "EstimateTime2": None,
                })

        return jsonify(results)
    except Exception as e:
        log.error("api_batch_eta error: %s", e)
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _load_credentials()
    log.info("Starting TDX Bus server on http://localhost:5000")
    app.run(host="127.0.0.1", port=5000, debug=True)
