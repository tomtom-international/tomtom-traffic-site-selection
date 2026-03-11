"""
Route Configuration — single source of truth for the analysis pipeline.

Change ORIGIN, DESTINATION, and the label fields here to re-target the
entire pipeline (travel time → site selection → enrichment → map) to a
different route.  Then run:

    ./run_analysis.sh

or, if you only want to regenerate the map from existing data:

    python plot_site_map.py
"""

# ── Route endpoints ────────────────────────────────────────────────────────────
ORIGIN = {
    "latitude":  33.8454,
    "longitude": -116.5416,
    "label":     "Origin",
}

DESTINATION = {
    "latitude":  33.8019,
    "longitude": -116.5206,
    "label":     "Destination",
}

# ── Human-readable labels used in map popups / panel text ─────────────────────
ROUTE_NAME     = "Sample Route"
AREA_NAME      = "Sample Area"               # used in layer names, titles, etc.
TIMEZONE       = "America/Los_Angeles"
DISTANCE_UNIT  = "MILES"                     # "MILES" or "KILOMETERS"

# ── Derived helpers (computed; do not edit) ────────────────────────────────────
MAP_CENTER_LAT = (ORIGIN["latitude"]  + DESTINATION["latitude"])  / 2
MAP_CENTER_LON = (ORIGIN["longitude"] + DESTINATION["longitude"]) / 2

def parking_search_points(extra_radius_deg: float = 0.008) -> list:
    """
    Return a list of (lat, lon) sampling points that together give broad
    parking-POI coverage across the route corridor.
    """
    clat, clon = MAP_CENTER_LAT, MAP_CENTER_LON
    return [
        (clat,                     clon                    ),  # centre
        (ORIGIN["latitude"],       ORIGIN["longitude"]     ),  # route start
        (DESTINATION["latitude"],  DESTINATION["longitude"]),  # route end
        (clat - extra_radius_deg,  clon                    ),  # south
        (clat + extra_radius_deg,  clon                    ),  # north
    ]
