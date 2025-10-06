"""
CIN Hackathon Resource Connector — Flask (single-file app)

A small Python web app that helps people find resources and view related census insights
using the Church IT Network Hackathon API.

✅ Features
- Resource directory with search + filter by Resource Type
- Census insights side panel (poverty & food assistance slices)
- Server-side proxy to the public API (handles CORS and centralizes errors)
- Minimal, modern UI via Tailwind CDN (no build step)
- Simple in-memory caching to keep the app snappy
- NEW: Interactive map on the home page showing resource locations (Leaflet)

🛠️ Setup
1) Python 3.10+
2) pip install -U flask requests cachetools
3) python app.py
4) Open http://127.0.0.1:5000

🔧 Config (env vars)
- HACKATHON_BASE_URL (default: http://hackathon.churchitnetwork.com)

Note: This app *reads* from the public endpoints listed in the prompt (GET only).
If the upstream schema changes, the UI gracefully falls back to showing raw JSON.
"""
from __future__ import annotations
import json
import os
from functools import wraps
from typing import Any, Dict, List, Tuple

import requests
from cachetools import TTLCache
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template_string,
    request,
    url_for,
)

# ---------------------------
# Config & Globals
# ---------------------------
BASE_URL = os.getenv("HACKATHON_BASE_URL", "http://hackathon.churchitnetwork.com")
API_PREFIX = "/swagger/v1"  # given by the OAS link in the prompt (not used directly)

# Basic cache: up to 256 entries, TTL 10 minutes
cache = TTLCache(maxsize=256, ttl=600)

app = Flask(__name__)


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
            # allow query params to flow through
            params = dict(request.args)
            # Path params are included in upstream path formatting
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

# Census data families (a few representative endpoints; add more as needed)
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
# Helpers to render resource cards nicely even if schema varies
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
    # find probable title
    title = rec.get("name") or rec.get("title") or rec.get("organization") or rec.get("ResourceName")
    # contact-ish fields
    website = rec.get("website") or rec.get("url") or rec.get("link")
    email = rec.get("email") or rec.get("contactEmail")
    phone = rec.get("phone") or rec.get("contactPhone")
    address = rec.get("address") or rec.get("Address")
    city = rec.get("city") or rec.get("City")
    state = rec.get("state") or rec.get("State")
    zip_code = rec.get("zip") or rec.get("Zip") or rec.get("postalCode")
    description = rec.get("description") or rec.get("Details") or rec.get("notes")

    # coordinates (schema-agnostic)
    lat = _to_float(_first(rec, LAT_KEYS))
    lon = _to_float(_first(rec, LON_KEYS))

    # Extras (up to a few) for unknown schemas
    extras = {}
    preferred_lower = {p.lower() for p in PREFERRED_RESOURCE_KEYS} | {k.lower() for k in LAT_KEYS+LON_KEYS}
    for k, v in rec.items():
        if k.lower() in preferred_lower:
            continue
        # keep small primitives only
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
# Web UI (now with a map)
# ---------------------------
INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Community Resources & Census Insights</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <!-- Leaflet CSS -->
  <link
    rel="stylesheet"
    href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
    crossorigin=""
  />
  <style>
    #map { height: 420px; }
  </style>
</head>
<body class="bg-slate-50 text-slate-900">
  <header class="sticky top-0 z-10 bg-white/80 backdrop-blur border-b">
    <div class="max-w-6xl mx-auto px-4 py-3 flex items-center gap-4">
      <div class="text-xl font-bold">Community Resources</div>
      <div class="ml-auto text-sm text-slate-500">Powered by CIN Hackathon API</div>
    </div>
  </header>

  <main class="max-w-6xl mx-auto px-4 py-6 grid gap-6 md:grid-cols-3">
    <!-- Left: Filters & Search -->
    <section class="md:col-span-1">
      <div class="bg-white rounded-2xl shadow p-4 space-y-4">
        <h2 class="font-semibold text-lg">Find Help</h2>
        <label class="block text-sm">Search</label>
        <input id="q" type="text" placeholder="food, housing, counseling…" class="w-full border rounded-xl px-3 py-2" />

        <label class="block text-sm mt-3">Resource Type</label>
        <select id="type" class="w-full border rounded-xl px-3 py-2"></select>

        <button id="searchBtn" class="mt-4 w-full bg-blue-600 hover:bg-blue-700 text-white rounded-xl px-3 py-2">Search</button>

        <p class="text-xs text-slate-500">Tip: choose a type to filter, or leave it to see everything.</p>
      </div>

      <div class="bg-white rounded-2xl shadow p-4 mt-6">
        <h3 class="font-semibold">Census Insights</h3>
        <p class="text-sm text-slate-600">Quick look at poverty/assistance to tailor outreach.</p>
        <div id="census" class="mt-3 space-y-2 text-sm"></div>
      </div>
    </section>

    <!-- Right: Map + Results -->
    <section class="md:col-span-2 space-y-4">
      <div class="bg-white rounded-2xl shadow">
        <div id="map" class="rounded-2xl"></div>
      </div>
      <div id="results" class="grid gap-4"></div>
    </section>
  </main>

  <footer class="max-w-6xl mx-auto px-4 pb-10 text-xs text-slate-500">
    Data source: <a class="underline" href="{{ base_url }}" target="_blank" rel="noreferrer">{{ base_url }}</a>
  </footer>

  <!-- Leaflet JS -->
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script>
async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`Request failed ${r.status}`);
  return await r.json();
}

function safeText(v) { return (v === null || v === undefined) ? '' : String(v); }

function resourceCard(rec) {
  const s = rec; // summarized by backend (includes lat/lon)
  const extras = s.extras || {};
  const extrasHtml = Object.entries(extras).map(([k,v]) => `<div class="flex justify-between"><span class="text-slate-500">${k}</span><span>${safeText(v)}</span></div>`).join("");
  return `
    <article class="bg-white rounded-2xl shadow p-4">
      <div class="flex items-start justify-between gap-4">
        <h3 class="font-semibold text-lg">${safeText(s.title)}</h3>
        ${s.website ? `<a class="text-blue-600 underline" href="${s.website}" target="_blank" rel="noreferrer">Website</a>` : ''}
      </div>
      ${s.description ? `<p class="mt-1 text-slate-700">${safeText(s.description)}</p>` : ''}
      <div class="mt-2 text-sm space-y-1">
        ${s.phone ? `<div>📞 ${safeText(s.phone)}</div>`:''}
        ${s.email ? `<div>✉️ <a class="underline" href="mailto:${s.email}">${safeText(s.email)}</a></div>`:''}
        ${s.address ? `<div>📍 ${safeText(s.address)}</div>`:''}
      </div>
      ${extrasHtml ? `<div class="mt-3 text-sm border-t pt-2 space-y-1">${extrasHtml}</div>` : ''}
    </article>`;
}

let map, markersLayer;
function ensureMap(){
  if (map) return map;
  map = L.map('map');
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
  map.setView([39.5, -98.35], 4); // sensible US default; will fit to markers
  markersLayer = L.layerGroup().addTo(map);
  return map;
}

function setMarkers(items){
  ensureMap();
  markersLayer.clearLayers();
  const bounds = [];
  items.forEach(s => {
    if (typeof s.latitude === 'number' && typeof s.longitude === 'number') {
      const m = L.marker([s.latitude, s.longitude]).bindPopup(`<b>${safeText(s.title)}</b><br/>${safeText(s.address)}${s.website?`<br/><a href='${s.website}' target='_blank' rel='noopener'>website</a>`:''}`);
      markersLayer.addLayer(m);
      bounds.push([s.latitude, s.longitude]);
    }
  });
  if (bounds.length){ map.fitBounds(bounds, { padding: [20,20] }); }
}

async function populateTypes() {
  try {
    const types = await fetchJSON('/api/ResourceType');
    const sel = document.getElementById('type');
    sel.innerHTML = '<option value="">All types</option>' + (types || []).map(t => `<option value="${t.id ?? t.Id ?? t.resourceTypeId ?? ''}">${safeText(t.name ?? t.Name ?? t.title ?? 'Type')}</option>`).join('');
  } catch(e) {
    console.error(e);
    document.getElementById('type').innerHTML = '<option>Types unavailable</option>'
  }
}

async function loadResources() {
  const q = document.getElementById('q').value.trim().toLowerCase();
  const typeId = document.getElementById('type').value;
  const res = await fetchJSON('/api/Resource');
  let items = Array.isArray(res) ? res : [];
  // client-side filter (schema-agnostic)
  if (typeId) {
    items = items.filter(r => {
      const v = r.resourceTypeId ?? r.typeId ?? r.ResourceTypeId;
      return String(v || '') === String(typeId);
    });
  }
  if (q) {
    items = items.filter(r => JSON.stringify(r).toLowerCase().includes(q));
  }

  // summarize each for safe display (includes lat/lon for the map)
  const summarized = await (await fetch('/summarize', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({items})})).json();
  const html = summarized.map(resourceCard).join('');
  document.getElementById('results').innerHTML = html || '<div class="text-slate-500">No results</div>';

  // map markers
  setMarkers(summarized);
}

async function loadCensus() {
  const box = document.getElementById('census');
  try {
    const [poverty, food] = await Promise.all([
      fetchJSON('/api/CensusPovertyData'),
      fetchJSON('/api/CensusFoodAssistanceData')
    ]);
    function pickNum(obj) {
      if (!obj) return null;
      for (const [k,v] of Object.entries(obj)) {
        if (typeof v === 'number') return v;
        if (typeof v === 'string' && v.match(/^[0-9.,%]+$/)) return v;
      }
      return null;
    }
    const p = Array.isArray(poverty) ? pickNum(poverty[0]) : pickNum(poverty);
    const f = Array.isArray(food) ? pickNum(food[0]) : pickNum(food);
    box.innerHTML = `
      <div class="flex items-center justify-between"><span class="text-slate-500">Poverty (sample)</span><span class="font-semibold">${p ?? '—'}</span></div>
      <div class="flex items-center justify-between"><span class="text-slate-500">Food Assistance (sample)</span><span class="font-semibold">${f ?? '—'}</span></div>
      <a class="text-blue-600 underline text-xs" href="${encodeURI('/explore')}">Explore detailed breakdowns →</a>
    `;
  } catch (e) {
    box.innerHTML = '<div class="text-slate-500">Census data unavailable.</div>'
  }
}

// Events
window.addEventListener('DOMContentLoaded', async () => {
  ensureMap();
  await populateTypes();
  await Promise.all([loadResources(), loadCensus()]);
  document.getElementById('searchBtn').addEventListener('click', loadResources);
});
</script>
</body>
</html>
"""


@app.post("/summarize")
def summarize_endpoint():
    """Summarize arbitrary resource records to a stable card format (now includes lat/lon)."""
    try:
        payload = request.get_json(force=True) or {}
        items = payload.get("items", [])
        out = [summarize_record(x) for x in items if isinstance(x, dict)]
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.get("/")
def index():
    return render_template_string(INDEX_HTML, base_url=BASE_URL)


# ---------------------------
# Optional: a simple explorer for the census slices
# ---------------------------
EXPLORE_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Census Explorer</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-50 text-slate-900">
  <main class="max-w-5xl mx-auto p-6 space-y-6">
    <a href="/" class="text-blue-600 underline">← Back</a>
    <h1 class="text-2xl font-bold">Census Explorer</h1>

    <div class="grid md:grid-cols-2 gap-4">
      <section class="bg-white rounded-2xl shadow p-4">
        <h2 class="font-semibold">Food Assistance by Marital Status</h2>
        <pre id="foodMarital" class="text-xs bg-slate-50 border rounded p-2 overflow-auto"></pre>
      </section>
      <section class="bg-white rounded-2xl shadow p-4">
        <h2 class="font-semibold">Food Assistance by Poverty Status</h2>
        <pre id="foodPoverty" class="text-xs bg-slate-50 border rounded p-2 overflow-auto"></pre>
      </section>
      <section class="bg-white rounded-2xl shadow p-4 md:col-span-2">
        <h2 class="font-semibold">Poverty by Education Level</h2>
        <pre id="povertyEdu" class="text-xs bg-slate-50 border rounded p-2 overflow-auto"></pre>
      </section>
    </div>
  </main>
<script>
async function fetchJSON(url){ const r = await fetch(url); if(!r.ok) throw new Error(r.status); return r.json(); }
async function load(){
  try {
    const [a,b,c] = await Promise.all([
      fetchJSON('/api/CensusFoodAssistanceByMaritalStatus'),
      fetchJSON('/api/CensusFoodAssistanceByPovertyStatus'),
      fetchJSON('/api/CensusPovertyByLevelOfEducation'),
    ]);
    document.getElementById('foodMarital').textContent = JSON.stringify(a, null, 2);
    document.getElementById('foodPoverty').textContent = JSON.stringify(b, null, 2);
    document.getElementById('povertyEdu').textContent   = JSON.stringify(c, null, 2);
  } catch(e) {
    console.error(e);
  }
}
load();
</script>
</body>
</html>
"""


@app.get("/explore")
def explore():
    return render_template_string(EXPLORE_HTML)


# ---------------------------
# Entry
# ---------------------------
if __name__ == "__main__":
    app.run(debug=True)
