# Codebase Overview

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
└── .env                         # API keys (git-ignored)
```

## Core Workflow (3 steps)

| Step | Module | Run command | Purpose |
|------|--------|-------------|---------|
| 1 | `core/travel_time.py` | `python -m core.travel_time` | Solve route with ArcGIS first; fallback to TomTom Traffic Stats; save travel time JSON |
| 2 | `core/site_selection.py` | `python -m core.site_selection` | Score candidate sites using accessibility, reliability, and speed; rank and output top picks |
| 3 | `visualization/plot_site_map.py` | `python -m visualization.plot_site_map` | Generate interactive Folium map with toggleable layers, served at localhost:8080 |

## Enrichment (optional)

| Module | Purpose |
|--------|---------|
| `core/enhanced_data.py` | ArcGIS-first POI enrichment with TomTom fallback + category support |

## Provider APIs Used

| Provider | API | Endpoint | Used For |
|----------|-----|----------|----------|
| ArcGIS (primary) | Route Solve | `/arcgis/rest/services/World/Route/NAServer/Route_World/solve` | Primary travel time and route geometry |
| ArcGIS (primary) | Geocoding Candidates | `/arcgis/rest/services/World/GeocodeServer/findAddressCandidates` | Query-based POI counts |
| TomTom (fallback) | Traffic Stats - Route Analysis | `/traffic/trafficstats/routeanalysis/1` | Fallback travel times |
| TomTom (fallback) | Traffic Stats - Status | `/traffic/trafficstats/status/1` | Fallback job polling |
| TomTom (fallback) | Traffic Flow Segment Data | `/traffic/services/4/flowSegmentData` | Real-time speed/congestion when used |
| TomTom (fallback) | POI Search / Nearby Search | `/search/2/poiSearch`, `/search/2/nearbySearch` | Fallback POI lookup |
| TomTom (fallback) | Category Search | `/search/2/categorySearch` | Category-specific POIs (parking, etc.) |

## Scoring Formulas

### Overall Score (used for ranking)
```
overall = 0.4 × accessibility + 0.3 × reliability + 0.3 × min(100, 2 × avg_speed)
```

### Accessibility Score (0–100)
```
score = 100 − delay_penalty + speed_bonus + road_class_bonus

delay_penalty = min(30, (time_ratio − 1) × 20)
time_ratio = avg_travel_time / expected_time
expected_time = distance / speed_limit

speed_bonus:
  +10 if avg_speed ≥ 40
  +5  if avg_speed ≥ 30
  −10 if avg_speed < 15

road_class_bonus = (8 − frc) × 2
```

### Reliability Score (0–100)
```
score = 100 − speed_variability_penalty − time_variability_penalty

speed_variability_penalty = min(40, cv_speed × 100)
time_variability_penalty = min(40, cv_time × 50)

cv_speed = σ_speed / avg_speed
cv_time = σ_time / median_time
```

### Commercial Viability Scores
Weighted blend of POI counts + congestion factor for:
- Retail viability
- Restaurant viability
- Office viability
- Cafe viability

## Map Layers (9 total)
1. 📍 Route Path
2. 🚩 Origin & Destination
3. 🏆 Multi-Site Comparison (top 5)
4. ⭐ Optimal Location
5. 🏪 Best Retail Location
6. 🍽️ Best Restaurant Location
7. 🅿️ All Area Parking
8. 🅿️ Parking Near Top 5 Sites (≤200m)
9. ⭐ Parking Near Best Locations (optimal/retail/restaurant/office)

## Data Files
| File | Description |
|------|-------------|
| `latest_travel_time.json` | Current travel time analysis |
| `latest_site_selection.json` | Current site ranking |
| `site_selection_map.html` | Interactive map (served via localhost:8080) |
| `comprehensive_site_selection_results.json` | Archived site selection |

## Supporting Files
- `README.md` — Quick start, config, developer instructions
- `run_analysis.sh` — One-command full workflow
- `requirements.txt` — Python dependencies

## Keys and Environment
- All keys are read from `.env` via `python-dotenv` (never committed).
- `ARCGIS_API_KEY` is used first across supported flows.
- `TOMTOM_TRAFFIC_API_KEY` and `TOMTOM_PLACES_API_KEY` provide fallback and category endpoints.
- `EMBED_EXTERNAL_TILE_KEYS` controls whether TomTom tile keys are embedded in generated HTML (default: `false`).
