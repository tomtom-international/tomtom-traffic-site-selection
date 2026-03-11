# Site Selection Analysis Tool

Automated workflow for analyzing travel times and selecting optimal sites along a route using ArcGIS (primary) with TomTom fallbacks.

## Prerequisites

- Python 3.10+
- API keys for **ArcGIS** and (optionally) **TomTom**
- Sample AADT traffic data (see [Sample Data](#sample-data) below)

## Quick Start

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure API keys

All scripts read keys from a `.env` file in the project root (git-ignored, never committed).

Create or edit `.env` and replace the placeholders with your real keys:

```bash
# ArcGIS — get a key at https://developers.arcgis.com
ARCGIS_API_KEY=REPLACE_WITH_REAL_ARCGIS_KEY

# TomTom (optional fallback) — get keys at https://developer.tomtom.com
TOMTOM_TRAFFIC_API_KEY=REPLACE_WITH_REAL_TOMTOM_TRAFFIC_KEY
TOMTOM_PLACES_API_KEY=REPLACE_WITH_REAL_TOMTOM_PLACES_KEY

# Optional: set to "true" to embed TomTom tile-layer URLs (with your key)
# into the generated HTML map. Default is "false" for security.
EMBED_EXTERNAL_TILE_KEYS=false
```

| Variable | Used by | Purpose |
|---|---|---|
| `ARCGIS_API_KEY` | `core/enhanced_data.py`, `core/travel_time.py`, `visualization/plot_site_map.py` | Primary provider for geocoding, routing, and POI search |
| `TOMTOM_TRAFFIC_API_KEY` | `core/travel_time.py` | TomTom Route Analysis / traffic stats (fallback) |
| `TOMTOM_PLACES_API_KEY` | `core/enhanced_data.py`, `visualization/plot_site_map.py` | TomTom POI/category search and optional map tiles |
| `EMBED_EXTERNAL_TILE_KEYS` | `visualization/plot_site_map.py` | When `true`, TomTom tile URLs are written into the exported HTML map. Keep `false` to avoid leaking keys. |

> **Important:** Never commit real API keys. If you accidentally do, rotate them immediately in the provider dashboard.

### 3. Configure your route

Edit [`config/route_config.py`](config/route_config.py) to set origin, destination, area name, and timezone. The default ships with sample coordinates — replace them with your target route.

### 4. Add sample data

Place AADT traffic data files under `sample_data/` in the project root. This folder is git-ignored (files are too large for version control).

```
sample_data/
└── <country>/
    └── <region>/
        ├── <version>_cvg_aadt.json        ← coverage metadata
        └── <version>_shp_aadt/            ← shapefile bundle
            ├── aadt.dbf
            ├── aadt.shp
            ├── aadt.shx
            └── aadt.prj
```

Data is available from your TomTom or Esri data subscription. Only the JSON coverage metadata files are required for basic operation; the shapefiles enable AADT traffic volume scoring.

### 5. Run the pipeline

```bash
./run_analysis.sh
```

This will:
1. Calculate travel times for the configured route (ArcGIS first, TomTom fallback)
2. Run site selection analysis
3. Generate an interactive map served at `http://localhost:8080`

Or run each step manually:

```bash
python -m core.travel_time
python -m core.site_selection
python -m visualization.plot_site_map
```

## Project Structure

```
├── config/
│   └── route_config.py          # Route origin, destination, labels
├── core/
│   ├── travel_time.py           # Travel time calculation (ArcGIS/TomTom)
│   ├── site_selection.py        # Site scoring and ranking
│   └── enhanced_data.py         # POI search and enrichment APIs
├── data/
│   ├── match_route_to_aadt.py   # Match route segments to AADT records
│   ├── match_route_aadt_optimized.py  # Optimized AADT matching
│   └── apply_nearest_neighbor_aadt.py # Fill gaps via nearest neighbor
├── visualization/
│   └── plot_site_map.py         # Interactive map generation (folium)
├── scripts/                     # Utilities, debug, and test scripts
├── sample_data/                 # AADT data files (git-ignored)
├── run_analysis.sh              # Full pipeline runner
├── requirements.txt
├── .env                         # API keys (git-ignored)
└── README.md
```

## Places API Enhancements

Enhanced with comprehensive POI (Point of Interest) analysis:

- **Foot Traffic Scoring** — Measures pedestrian flow potential
- **Commercial Viability Scores** — Business-type specific ratings (retail, restaurant, office, cafe)
- **POI Category Analysis** — Analyzes 7 category groups (transit, parking, food, retail, business, entertainment, financial)
- **Competition Analysis** — Evaluates nearby similar businesses

## Output Files

| File | Description |
|------|-------------|
| `latest_travel_time.json` | Current travel time analysis |
| `latest_site_selection.json` | Current site selection results |
| `site_selection_map.html` | Interactive map (served via localhost:8080) |
| `travel_time_results_XXXXX.json` | Archived travel time results by job ID |
| `comprehensive_site_selection_results.json` | Archived site selection |

## Data Provenance

Provider attribution is explicit in both outputs and UI:

- `latest_travel_time.json` includes `provider_metadata` with:
	- `provider` (ArcGIS or TomTom)
	- `strategy` (`ArcGIS first, TomTom fallback`)
	- `fallback_from` (when TomTom is used as fallback)
	- route/time context and run timestamp
- `latest_site_selection.json` and `comprehensive_site_selection_results.json` include `data_provenance` with:
	- `run.travel_provider` and `run.travel_provider_metadata`
	- `metric_sources` mapping each scoring block to its data source
- `site_selection_map.html` shows a **🧾 Data Provenance** panel (top-right) with:
	- active travel provider for the run
	- fallback status (if any)
	- per-metric source mapping

## Analysis Features

### 1. Multi-Site Comparison
Compares candidate locations based on accessibility scores, traffic reliability, and average speeds.

### 2. Optimal Location Finder
Identifies the best single point along the route using:
- Accessibility (30%) · Reliability (25%) · Traffic flow (25%) · Visibility (20%)

### 3. Accessibility Mapping
Creates accessibility zones: 🟢 Excellent (≥ 90) · 🟡 Good (70–89) · 🟠 Moderate (50–69) · 🔴 Poor (< 50)

### 4. Business Type Recommendations
Evaluates locations for: 🏪 Retail · 🍽️ Restaurants · 🏢 Offices

## Interactive Map Layers

Toggle layers in the map control:
- **📍 Route Path** — The travel route
- **🚩 Origin & Destination** — Start/end points
- **🏆 Multi-Site Comparison** — Top 5 recommended sites
- **⭐ Optimal Location** — Single best location
- **🏪 Best Retail / Restaurant / Office** — Business-specific recommendations

## Developer Instructions

### Architecture

The core flow is three steps:

1. **Travel time**: [`core/travel_time.py`](core/travel_time.py) (ArcGIS first)
2. **Site scoring**: [`core/site_selection.py`](core/site_selection.py)
3. **Visualization**: [`visualization/plot_site_map.py`](visualization/plot_site_map.py)

Optional POI enrichment runs via [`core/enhanced_data.py`](core/enhanced_data.py).

### API Usage Rules

- Use ArcGIS endpoints first where available.
- Use TomTom only when ArcGIS lacks equivalent support.
- Keep API calls in one place per module; pass parsed results into scoring functions.
- Always set timeouts on requests (currently 10 s).
- Respect rate limits by limiting enrichment to top sites only.

### Data Contracts (Do Not Break)

- `latest_travel_time.json` must stay compatible with `core/site_selection.py`.
- `latest_site_selection.json` must stay compatible with `visualization/plot_site_map.py`.
- If you add new keys, add them as optional fields and preserve existing ones.

### Testing

Run the full workflow before sharing changes:

```bash
python -m core.travel_time
python -m core.site_selection
python -m visualization.plot_site_map
```

Verify that all map layers render and toggle correctly in the browser.

### Secrets and Keys

- Do not commit API keys into version control.
- Read keys from environment variables (`.env` + `python-dotenv`).

## License

This project is licensed under the Apache License 2.0 — see the [LICENSE](LICENSE) file for details.
