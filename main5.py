"""
CIN Hackathon Resource Connector — Flask (single-file app)

Serves a static index.html and proxies the Hackathon API with simple caching.
Also exposes /config so the static JS can read runtime values (e.g., base_url).

Setup
1) Python 3.10+
2) pip install -U flask requests cachetools
3) python app.py
4) Open http://127.0.0.1:5000

Config (env vars)
- HACKATHON_BASE_URL (default: http://hackathon.churchitnetwork.com)

Note: This app reads from public endpoints (GET only).
"""
from __future__ import annotations
import os
from functools import wraps
from typing import Any, Dict, List, Tuple

import requests
from cachetools import TTLCache
from flask import (
    Flask,
    Response,
    jsonify,
    request,
    send_from_directory,
)

# ---------------------------
# Config & Globals
# ---------------------------
BASE_URL = os.getenv("HACKATHON_BASE_URL", "http://hackathon.churchitnetwork.com")
API_PREFIX = "/swagger/v1"  # given by the OAS link (not used directly)

# Basic cache: up to 256 entries, TTL 10 minutes
cache = TTLCache(maxsize=256, ttl=600)

# IMPORTANT: point Flask at the ./static directory
app = Flask(__name__, static_folder="static", static_url_path="/static")


# ---------------------------
# Utility: cached GET to upstream API
# ---------------------------
def cached_get(path: str, params: Dict[str, Any] | None = None) -> Tuple[int, Dict[str, Any] | List[Any] | str]:
    """GET request to upstream, with simple cache and robust error handling.

    Returns (status_code, parsed_json|text)
    """
    url = f"{BASE_URL}{path}"
    key = (url, tuple(sorted((params or {}).items())))
    if key in cache:
        return 200, cache[key]
    try:
        r = requests.get(url, params=params, timeout=15)
    except requests.RequestException as e:
        return 502, {"error": "Upstream request failed", "detail": str(e), "url": url}

    # Try JSON first; fall back to text
    try:
        data = r.json()
    except ValueError:
        data = r.text

    if r.ok:
        cache[key] = data
        return r.status_code, data
    else:
        return r.status_code, data


def proxy_endpoint(upstream_path: str):
    """Decorator to build a simple GET proxy route that maps to an upstream path."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(**kwargs):
            params = dict(request.args)  # pass through query params
            status, data = cached_get(upstream_path.format(**kwargs), params=params)
            if isinstance(data, (dict, list)):
                return jsonify(data), status
            return Response(data, status=status, mimetype="application/json")
        return wrapper
    return decorator


# ---------------------------
# Proxy routes (GET only)
# ---------------------------
# Core collections
@app.get("/api/Resource")
@proxy_endpoint("/api/Resource")
def proxy_resource():
    pass

@app.get("/api/Resource/<id>")
@proxy_endpoint("/api/Resource/{id}")
def proxy_resource_by_id(id):
    pass

@app.get("/api/ResourceType")
@proxy_endpoint("/api/ResourceType")
def proxy_resource_type():
    pass

@app.get("/api/ResourceType/<id>")
@proxy_endpoint("/api/ResourceType/{id}")
def proxy_resource_type_by_id(id):
    pass

# Census data families (representative endpoints)
@app.get("/api/CensusPovertyData")
@proxy_endpoint("/api/CensusPovertyData")
def proxy_census_poverty():
    pass

@app.get("/api/CensusFoodAssistanceData")
@proxy_endpoint("/api/CensusFoodAssistanceData")
def proxy_census_food_assistance():
    pass

@app.get("/api/CensusFoodAssistanceByMaritalStatus")
@proxy_endpoint("/api/CensusFoodAssistanceByMaritalStatus")
def proxy_census_food_by_marital():
    pass

@app.get("/api/CensusFoodAssistanceByPovertyStatus")
@proxy_endpoint("/api/CensusFoodAssistanceByPovertyStatus")
def proxy_census_food_by_poverty():
    pass

@app.get("/api/CensusPovertyByLevelOfEducation")
@proxy_endpoint("/api/CensusPovertyByLevelOfEducation")
def proxy_census_poverty_by_edu():
    pass

# Generic passthrough if you want to expand without adding explicit routes
@app.get("/api/passthrough/<path:rest>")
def proxy_passthrough(rest: str):
    status, data = cached_get(f"/{rest}", params=dict(request.args))
    if isinstance(data, (dict, list)):
        return jsonify(data), status
    return Response(data, status=status, mimetype="application/json")


# ---------------------------
# Helpers to summarize records for the frontend
# ---------------------------
PREFERRED_RESOURCE_KEYS = [
    "name", "title", "organization", "category", "type", "resourceType", "phone", "email", "website",
    "address", "city", "state", "zip", "description", "notes",
]
LAT_KEYS = ["latitude", "Latitude", "lat", "Lat", "y"]
LON_KEYS = ["longitude", "Longitude", "lon", "Lon", "lng", "Lng", "x"]

def _first(d: Dict[str, Any], keys: List[str]):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None

def _to_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return float(v)
    except Exception:
        return None

def summarize_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    title = rec.get("name") or rec.get("title") or rec.get("organization") or rec.get("ResourceName")
    website = rec.get("website") or rec.get("url") or rec.get("link")
    email = rec.get("email") or rec.get("contactEmail")
    phone = rec.get("phone") or rec.get("contactPhone")
    address = rec.get("address") or rec.get("Address")
    city = rec.get("city") or rec.get("City")
    state = rec.get("state") or rec.get("State")
    zip_code = rec.get("zip") or rec.get("Zip") or rec.get("postalCode")
    description = rec.get("description") or rec.get("Details") or rec.get("notes")
    lat = _to_float(_first(rec, LAT_KEYS))
    lon = _to_float(_first(rec, LON_KEYS))

    extras = {}
    preferred_lower = {p.lower() for p in PREFERRED_RESOURCE_KEYS} | {k.lower() for k in LAT_KEYS + LON_KEYS}
    for k, v in rec.items():
        if k.lower() in preferred_lower:
            continue
        if isinstance(v, (str, int, float)) and 0 < len(str(v)) < 200:
            extras[k] = v
        if len(extras) >= 5:
            break

    return {
        "title": title or "Untitled Resource",
        "website": website,
        "email": email,
        "phone": phone,
        "address": ", ".join([p for p in [address, city, state, zip_code] if p]),
        "description": description,
        "extras": extras,
        "latitude": lat,
        "longitude": lon,
    }


# ---------------------------
# JSON helpers for the static frontend
# ---------------------------
@app.post("/summarize")
def summarize_endpoint():
    """Summarize arbitrary resource records to a stable card format (includes lat/lon)."""
    try:
        payload = request.get_json(force=True) or {}
        items = payload.get("items", [])
        out = [summarize_record(x) for x in items if isinstance(x, dict)]
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.get("/config")
def config():
    """Runtime config for static frontend (replace former {{ base_url }} usage)."""
    return jsonify({
        "base_url": BASE_URL
    })


# ---------------------------
# Static file routes
# ---------------------------
@app.get("/")
def static_index():
    # Serves ./static/index.html
    return send_from_directory(app.static_folder, "index.html")


# ---------------------------
# Entry
# ---------------------------
if __name__ == "__main__":
    app.run(debug=True)
