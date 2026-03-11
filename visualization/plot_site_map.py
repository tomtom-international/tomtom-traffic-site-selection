"""
Map Visualization for Site Selection Results
Plot recommended sites on an interactive map
"""

import os
import json
import sys
import glob
import re
import webbrowser
import folium
from dotenv import load_dotenv
from core.enhanced_data import (
    search_pois_arcgis,
    search_pois_arcgis_places_first,
    search_category_pois,
    get_tomtom_api_health_report,
    reset_tomtom_api_health,
)
from core.site_selection import load_aadt_data, calculate_traffic_exposure_score

# Load environment variables from .env file
load_dotenv()

# ArcGIS key (used by API-backed services if needed; map tiles below are public)
ARCGIS_API_KEY = os.getenv("ARCGIS_API_KEY", "MOCK_ARCGIS_API_KEY")
# TomTom map key retained for fallback tile layers
TOMTOM_MAP_API_KEY = os.getenv("TOMTOM_PLACES_API_KEY")
# Security default: do not write key-bearing tile URLs into generated HTML unless explicitly enabled.
EMBED_EXTERNAL_TILE_KEYS = os.getenv("EMBED_EXTERNAL_TILE_KEYS", "false").strip().lower() in {"1", "true", "yes", "on"}

# Route configuration — edit route_config.py to change origin/destination
from config.route_config import (
    ORIGIN as ROUTE_ORIGIN,
    DESTINATION as ROUTE_DESTINATION,
    ROUTE_NAME as ROUTE_DISPLAY_NAME,
    AREA_NAME as ROUTE_AREA_NAME,
    MAP_CENTER_LAT,
    MAP_CENTER_LON,
    parking_search_points,
)


def load_results(filepath: str) -> dict:
    """Load the comprehensive site selection results"""
    with open(filepath, 'r') as f:
        return json.load(f)


def load_travel_time_data(filepath: str) -> dict:
    """Load original travel time data for route geometry"""
    with open(filepath, 'r') as f:
        return json.load(f)


def extract_route_coordinates(travel_data: dict) -> list:
    """Extract route coordinates from travel-time JSON."""
    route_coords = []
    for route in travel_data.get("routes", []):
        for segment in route.get("segmentResults", []):
            for point in segment.get("shape", []):
                if "latitude" in point and "longitude" in point:
                    route_coords.append([point["latitude"], point["longitude"]])

    deduped_coords = []
    for point in route_coords:
        if not deduped_coords or deduped_coords[-1] != point:
            deduped_coords.append(point)
    return deduped_coords


def load_latest_result_file(pattern: str):
    """Load the latest JSON file that matches the given pattern."""
    base_dir = os.path.dirname(os.path.dirname(__file__))
    matches = glob.glob(os.path.join(base_dir, pattern))
    if not matches:
        return None, None

    latest = max(matches, key=os.path.getmtime)
    with open(latest, 'r') as file:
        return json.load(file), os.path.basename(latest)


def create_site_selection_map(results: dict, travel_data: dict) -> folium.Map:
    """Create an interactive map with all site selection recommendations"""
    reset_tomtom_api_health()
    
    # Center map on route midpoint (driven by route_config.py)
    center_lat = MAP_CENTER_LAT
    center_lon = MAP_CENTER_LON

    # Create base map with ArcGIS tiles as default
    arcgis_street_url = "https://services.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}"
    
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=14,
        tiles=None  # We'll add tiles manually
    )
    
    # Add ArcGIS tile layers
    arcgis_street_layer = folium.TileLayer(
        tiles=arcgis_street_url,
        attr='© Esri',
        name='🗺️ ArcGIS Street',
        overlay=False,
        control=True,
        show=True
    )
    arcgis_street_layer.add_to(m)
    
    arcgis_light_gray_layer = folium.TileLayer(
        tiles="https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        attr='© Esri',
        name='🧭 ArcGIS Light Gray',
        overlay=False,
        control=True,
        show=False
    )
    arcgis_light_gray_layer.add_to(m)
    
    arcgis_satellite_layer = folium.TileLayer(
        tiles="https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr='© Esri',
        name='🛰️ ArcGIS Satellite',
        overlay=False,
        control=True,
        show=False
    )
    arcgis_satellite_layer.add_to(m)

    arcgis_basemap_layer_vars = [
        arcgis_street_layer.get_name(),
        arcgis_light_gray_layer.get_name(),
        arcgis_satellite_layer.get_name(),
    ]
    tomtom_basemap_layer_vars = []

    # Optional TomTom tile layers only when explicit key embedding is enabled.
    if TOMTOM_MAP_API_KEY and EMBED_EXTERNAL_TILE_KEYS:
        tomtom_basic_layer = folium.TileLayer(
            tiles=f"https://api.tomtom.com/map/1/tile/basic/main/{{z}}/{{x}}/{{y}}.png?key={TOMTOM_MAP_API_KEY}",
            attr='© TomTom',
            name='🗺️ TomTom Basic',
            overlay=False,
            control=True,
            show=False
        )
        tomtom_basic_layer.add_to(m)

        tomtom_night_layer = folium.TileLayer(
            tiles=f"https://api.tomtom.com/map/1/tile/basic/night/{{z}}/{{x}}/{{y}}.png?key={TOMTOM_MAP_API_KEY}",
            attr='© TomTom',
            name='🌙 TomTom Night',
            overlay=False,
            control=True,
            show=False
        )
        tomtom_night_layer.add_to(m)

        tomtom_hybrid_layer = folium.TileLayer(
            tiles=f"https://api.tomtom.com/map/1/tile/hybrid/main/{{z}}/{{x}}/{{y}}.png?key={TOMTOM_MAP_API_KEY}",
            attr='© TomTom',
            name='🛰️ TomTom Hybrid',
            overlay=False,
            control=True,
            show=False
        )
        tomtom_hybrid_layer.add_to(m)

        tomtom_basemap_layer_vars = [
            tomtom_basic_layer.get_name(),
            tomtom_night_layer.get_name(),
            tomtom_hybrid_layer.get_name(),
        ]
    elif TOMTOM_MAP_API_KEY and not EMBED_EXTERNAL_TILE_KEYS:
        print("TomTom tile layers skipped for export safety (set EMBED_EXTERNAL_TILE_KEYS=true to include them).")
    
    # ========================================
    # Layer 1: Route Path
    # ========================================
    route_group = folium.FeatureGroup(name="📍 Route Path", show=True)
    
    route_coords = extract_route_coordinates(travel_data)

    if route_coords:
        folium.PolyLine(
            route_coords,
            weight=4,
            color='#3388ff',
            opacity=0.8,
            popup=f"Route: {ROUTE_DISPLAY_NAME}"
        ).add_to(route_group)
    
    route_group.add_to(m)

    # ========================================
    # Layer 1b: Route Comparison (ArcGIS vs TomTom) - DISABLED
    # ========================================
    # Comparison layer removed per user request - no red lines/markers
    comparison_group = folium.FeatureGroup(name="🆚 Route Comparison (ArcGIS vs TomTom)", show=False)

    esri_data, esri_file = load_latest_result_file("travel_time_results_arcgis_*.json")
    tomtom_data, tomtom_file = load_latest_result_file("travel_time_results_[0-9]*.json")

    esri_coords = extract_route_coordinates(esri_data) if esri_data else []
    tomtom_coords = extract_route_coordinates(tomtom_data) if tomtom_data else []

    def nearest_route_coord(lat: float, lon: float, route_points: list):
        if not route_points:
            return lat, lon
        nearest = min(route_points, key=lambda point: (point[0] - lat) ** 2 + (point[1] - lon) ** 2)
        return nearest[0], nearest[1]

    def segment_anchor_from_name(route_dataset: dict, segment_name: str):
        if not route_dataset or not segment_name:
            return None
        match = re.search(r"segment\s+(\d+)", str(segment_name), flags=re.IGNORECASE)
        if not match:
            return None
        segment_index = int(match.group(1)) - 1
        routes = route_dataset.get("routes", [])
        if not routes:
            return None
        segments = routes[0].get("segmentResults", [])
        if segment_index < 0 or segment_index >= len(segments):
            return None
        shape = segments[segment_index].get("shape", [])
        if not shape:
            return None
        mid_point = shape[len(shape) // 2]
        latitude = mid_point.get("latitude")
        longitude = mid_point.get("longitude")
        if latitude is None or longitude is None:
            return None
        return float(latitude), float(longitude)

    def tomtom_route_anchor_for_site(site_name: str, fallback_lat: float, fallback_lon: float):
        anchor = segment_anchor_from_name(tomtom_data, site_name)
        if anchor:
            return anchor
        if tomtom_coords:
            return nearest_route_coord(fallback_lat, fallback_lon, tomtom_coords)
        return fallback_lat, fallback_lon

    def build_segment_frc_index(route_dataset: dict):
        """Build per-segment FRC metadata index with dynamic coverage fallback."""
        if not route_dataset:
            return []
        routes = route_dataset.get("routes", [])
        if not routes:
            return []
        segments = routes[0].get("segmentResults", [])
        if not segments:
            return []

        # Dynamic normalization fallback when normalizedSampleSize is absent/zero.
        sample_values = []
        for seg in segments:
            tr = (seg.get("segmentTimeResults") or [{}])[0]
            sample_values.append(float(tr.get("sampleSize", 0) or 0))
        max_sample = max(sample_values) if sample_values else 0.0

        index = []
        for seg in segments:
            shape = seg.get("shape", [])
            if not shape:
                continue
            mid = shape[len(shape) // 2]
            lat = mid.get("latitude")
            lon = mid.get("longitude")
            if lat is None or lon is None:
                continue

            tr = (seg.get("segmentTimeResults") or [{}])[0]
            normalized = tr.get("normalizedSampleSize", None)
            if normalized is not None and float(normalized) > 0:
                coverage = float(normalized) * 100.0
            else:
                sample_size = float(tr.get("sampleSize", 0) or 0)
                coverage = (sample_size / max_sample * 100.0) if max_sample > 0 else 0.0

            index.append({
                "lat": float(lat),
                "lon": float(lon),
                "frc": int(seg.get("frc", 8) or 8),
                "frc_coverage": max(0.0, min(100.0, coverage)),
            })

        return index

    # Prefer TomTom segment metadata in TomTom mode; fall back to current route dataset.
    frc_segment_index = build_segment_frc_index(tomtom_data if tomtom_data else travel_data)
    tomtom_frc_label = "TomTom FRC Coverage" if tomtom_data else "FRC Coverage"

    def enrich_site_with_frc(site: dict):
        """Fill missing/zero FRC metadata from nearest route segment dynamically."""
        if not frc_segment_index:
            return site
        lat = site.get("latitude", 0)
        lon = site.get("longitude", 0)
        nearest = min(
            frc_segment_index,
            key=lambda s: (s["lat"] - lat) ** 2 + (s["lon"] - lon) ** 2
        )
        updated = dict(site)
        if not updated.get("primary_frc") or int(updated.get("primary_frc", 8) or 8) == 8:
            updated["primary_frc"] = nearest.get("frc", 8)
        if float(updated.get("frc_coverage", 0) or 0) <= 0:
            updated["frc_coverage"] = nearest.get("frc_coverage", 0)
        return updated

    def snap_sites_to_route(site_list: list, route_points: list):
        if not route_points:
            return site_list
        snapped_sites = []
        for site in site_list:
            latitude = site.get("latitude", 0)
            longitude = site.get("longitude", 0)
            snapped_lat, snapped_lon = nearest_route_coord(latitude, longitude, route_points)
            updated_site = dict(site)
            updated_site["latitude"] = snapped_lat
            updated_site["longitude"] = snapped_lon
            snapped_sites.append(updated_site)
        return snapped_sites

    # TomTom route shown as solid blue line matching ArcGIS style
    if tomtom_coords:
        folium.PolyLine(
            tomtom_coords,
            weight=4,
            color='#3388ff',
            opacity=0.8,
            popup=f"TomTom Route ({tomtom_file})"
        ).add_to(comparison_group)

    if tomtom_coords:
        folium.CircleMarker(
            location=tomtom_coords[0],
            radius=5,
            color="#2196f3",
            fill=True,
            fill_color="#2196f3",
            fill_opacity=0.9,
            tooltip="TomTom start"
        ).add_to(comparison_group)

    comparison_group.add_to(m)
    
    # ========================================
    # Layer 2: Origin and Destination
    # ========================================
    od_group = folium.FeatureGroup(name="🚩 Origin & Destination", show=True)
    
    # Origin marker
    folium.Marker(
        location=[ROUTE_ORIGIN["latitude"], ROUTE_ORIGIN["longitude"]],
        popup=folium.Popup(f"<b>{ROUTE_ORIGIN['label']}</b><br>Route Origin", max_width=200),
        icon=folium.Icon(color='green', icon='circle', prefix='fa')
    ).add_to(od_group)

    # Destination marker (changed from red to blue)
    folium.Marker(
        location=[ROUTE_DESTINATION["latitude"], ROUTE_DESTINATION["longitude"]],
        popup=folium.Popup(f"<b>{ROUTE_DESTINATION['label']}</b><br>Route Destination", max_width=200),
        icon=folium.Icon(color='blue', icon='flag', prefix='fa')
    ).add_to(od_group)
    
    od_group.add_to(m)
    
    # ========================================
    # Layer 3: Multi-Site Comparison (ArcGIS vs TomTom-enhanced)
    # ========================================
    multi_site_arcgis_group = folium.FeatureGroup(name="🏆 Multi-Site Comparison (ArcGIS only)", show=True)
    multi_site_enhanced_group = folium.FeatureGroup(name="🏆 Multi-Site (ArcGIS + TomTom)", show=False)

    all_top_sites = results.get("multi_site_comparison", {}).get("top_sites", [])
    base_sites = all_top_sites[:5]
    enriched_sites = results.get("multi_site_comparison", {}).get("top_sites_enriched", [])[:5]
    retail_sites = results.get("retail_analysis", {}).get("all_sites", [])
    has_enhanced_results = bool(enriched_sites or retail_sites or tomtom_coords)

    # Build TomTom-mode marker candidates.
    # Prefer explicit top_sites_enriched; if unavailable, derive from retail/business ranking
    # so ArcGIS vs TomTom modes still show different numbered site outcomes.
    if enriched_sites:
        enhanced_marker_sites = enriched_sites
    else:
        def _nearest_top_site(lat, lon):
            """Return the closest entry in all_top_sites by coordinate proximity."""
            if not all_top_sites:
                return {}
            return min(
                all_top_sites,
                key=lambda s: (s.get("latitude", 0) - lat) ** 2 + (s.get("longitude", 0) - lon) ** 2
            )

        ranked_retail_sites = sorted(
            [s for s in retail_sites if s.get("name", "Unknown") != "Unknown"],  # Filter out Unknown sites
            key=lambda site: site.get("commercial_score", 0),
            reverse=True,
        )[:5]
        
        # If we don't have enough valid retail sites, fall back to top_sites
        if len(ranked_retail_sites) < 5:
            enhanced_marker_sites = all_top_sites[:5]
        else:
            enhanced_marker_sites = []
            for index, retail_site in enumerate(ranked_retail_sites, 1):
                anchored_lat, anchored_lon = tomtom_route_anchor_for_site(
                    retail_site.get("name", ""),
                    retail_site.get("latitude", 0),
                    retail_site.get("longitude", 0),
                )
                # Use retail site's own FRC data, not from nearest match
                enhanced_marker_sites.append({
                    "rank": index,
                    "name": retail_site.get("name", f"Business Site {index}"),
                    "latitude": anchored_lat,
                    "longitude": anchored_lon,
                    "overall_score": retail_site.get("commercial_score", 0),
                    "accessibility_score": retail_site.get("commercial_score", 0),  # Use commercial score as proxy
                    "reliability_score": retail_site.get("commercial_score", 0) * 0.7,  # Approximate
                    "frc_coverage": retail_site.get("frc_coverage", 0),  # Use retail site's own FRC coverage
                    "primary_frc": retail_site.get("primary_frc", 8),  # Use retail site's own FRC
                })
    if enriched_sites:
        # Re-rank by TomTom enriched business score so TomTom mode shows genuinely
        # different (TomTom-ranked) sites rather than the same ArcGIS order.
        def _tomtom_business_score(site):
            ed = site.get("enriched", {})
            ft = ed.get("foot_traffic_score", 0) or 0
            ad = ed.get("amenity_density_score", 0) or 0
            return (ft + ad) / 2 if (ft or ad) else site.get("overall_score", 0)

        enhanced_marker_sites = sorted(enhanced_marker_sites, key=_tomtom_business_score, reverse=True)
        enhanced_marker_sites = [
            {
                **site,
                "rank": idx,
                "overall_score": _tomtom_business_score(site),
                "latitude": tomtom_route_anchor_for_site(
                    site.get("name", ""),
                    site.get("latitude", 0),
                    site.get("longitude", 0),
                )[0],
                "longitude": tomtom_route_anchor_for_site(
                    site.get("name", ""),
                    site.get("latitude", 0),
                    site.get("longitude", 0),
                )[1],
            }
            for idx, site in enumerate(enhanced_marker_sites, 1)
        ]

    # Ensure TomTom marker popups have dynamic FRC metadata from nearest route segment.
    enhanced_marker_sites = [enrich_site_with_frc(site) for site in enhanced_marker_sites]

    has_enhanced_site_markers = bool(enhanced_marker_sites)
    tomtom_top_site = enhanced_marker_sites[0] if has_enhanced_site_markers else None

    def add_multi_site_markers(site_list, target_group, allow_enriched_popup=False, rank_mode_label="ArcGIS rank", show_aadt=False):
        for site in site_list:
            rank = site.get("rank", 0)
            name = site.get("name", "Unknown")
            lat = site.get("latitude", 0)
            lon = site.get("longitude", 0)
            score = site.get("overall_score", 0)
            access = site.get("accessibility_score", 0)
            reliab = site.get("reliability_score", 0)
            frc_cov = site.get("frc_coverage", 0)
            primary_frc = site.get("primary_frc", 8)
            avg_speed = site.get("avg_speed", 0)

            if frc_cov >= 80:
                cov_badge = f'<span style="background:#4caf50;color:white;padding:2px 6px;border-radius:3px;font-size:10px;">🟢 {frc_cov:.0f}% FRC{primary_frc}</span>'
            elif frc_cov >= 50:
                cov_badge = f'<span style="background:#ff9800;color:white;padding:2px 6px;border-radius:3px;font-size:10px;">🟡 {frc_cov:.0f}% FRC{primary_frc}</span>'
            else:
                cov_badge = f'<span style="background:#f44336;color:white;padding:2px 6px;border-radius:3px;font-size:10px;">🔴 {frc_cov:.0f}% FRC{primary_frc}</span>'

            enriched = site.get("enriched", {}) if allow_enriched_popup else {}
            has_enriched = bool(enriched)

            if has_enriched:
                poi_counts = enriched.get("poi_counts", {})
                foot_traffic = enriched.get("foot_traffic_score", 0)
                viability = enriched.get("viability_scores", {})

                popup_html = f"""
                <div style="font-family: Arial; width: 280px;">
                    <h4 style="margin: 0; color: #1a73e8;">#{rank} {name}</h4>
                    <hr style="margin: 5px 0;">
                    <b>Overall Score:</b> {score:.1f}/100<br>
                    <b>Accessibility:</b> {access:.1f}/100<br>
                    <b>Reliability:</b> {reliab:.1f}/100<br>
                    <b>{tomtom_frc_label}:</b> {cov_badge}<br>
                    <br>
                    <div style="background: #f0f7ff; padding: 6px; border-radius: 3px; margin-bottom: 6px;">
                        <b style="color: #1a73e8;">👥 Foot Traffic: {foot_traffic:.0f}/100</b>
                    </div>
                    <b>📍 Nearby POIs:</b><br>
                    <span style="font-size: 11px;">
                    🛍️ Retail: {poi_counts.get('retail', 0)} |
                    🍽️ Food: {poi_counts.get('food_service', 0)}<br>
                    🅿️ Parking: {poi_counts.get('parking', 0)} |
                    🚇 Transit: {poi_counts.get('transit', 0)}
                    </span>
                    <br><br>
                    <b>💼 Business Viability:</b><br>
                    <span style="font-size: 11px;">
                    Retail: {viability.get('retail_viability', 0):.0f} |
                    Restaurant: {viability.get('restaurant_viability', 0):.0f}<br>
                    Office: {viability.get('office_viability', 0):.0f} |
                    Cafe: {viability.get('cafe_viability', 0):.0f}
                    </span>
                    <br><br>
                    <i style="color: #666; font-size: 10px;">Enhanced with TomTom Places API</i><br>
                    <i style="color: #1a73e8; font-size: 10px;">{rank_mode_label}</i>
                </div>
                """
            elif allow_enriched_popup:
                # TomTom mode but no enriched POI data — show FRC from TomTom traffic file
                popup_html = f"""
                <div style="font-family: Arial; width: 220px;">
                    <h4 style="margin: 0; color: #1a73e8;">#{rank} {name}</h4>
                    <hr style="margin: 5px 0;">
                    <b>Overall Score:</b> {score:.1f}/100<br>
                    <b>Accessibility:</b> {access:.1f}/100<br>
                    <b>Reliability:</b> {reliab:.1f}/100<br>
                    <b>{tomtom_frc_label}:</b> {cov_badge}<br>
                    <br>
                    <i style="color: #666;">Multi-Site Comparison Analysis</i><br>
                    <i style="color: #1a73e8; font-size: 10px;">{rank_mode_label}</i>
                </div>
                """
            else:
                # ArcGIS-only mode — no TomTom data shown
                avg_aadt_val = site.get("avg_aadt", 0)
                exposure_val = site.get("traffic_exposure_score", 0)
                if show_aadt and avg_aadt_val:
                    popup_html = f"""
                <div style="font-family: Arial; width: 240px;">
                    <h4 style="margin: 0; color: #1a73e8;">#{rank} {name}</h4>
                    <hr style="margin: 5px 0;">
                    <b>Overall Score:</b> {score:.1f}/100<br>
                    <b>Accessibility:</b> {access:.1f}/100<br>
                    <b>Reliability:</b> {reliab:.1f}/100<br>
                    <b>{tomtom_frc_label}:</b> {cov_badge}<br>
                    <br>
                    <div style="background: #fff3e0; padding: 6px; border-radius: 3px; margin-bottom: 6px;">
                        <b style="color: #e65100;">🚗 AADT: {avg_aadt_val:,.0f} veh/day</b><br>
                        <span style="font-size: 11px;">Traffic Exposure: {exposure_val:.1f}/100</span>
                    </div>
                    <i style="color: #666; font-size: 10px;">Formula: Access 30% + Exposure 25% + Reliability 25% + Speed 20%</i><br>
                    <i style="color: #1a73e8; font-size: 10px;">{rank_mode_label}</i>
                </div>
                """
                else:
                    popup_html = f"""
                <div style="font-family: Arial; width: 220px;">
                    <h4 style="margin: 0; color: #1a73e8;">#{rank} {name}</h4>
                    <hr style="margin: 5px 0;">
                    <b>Overall Score:</b> {score:.1f}/100<br>
                    <b>Accessibility:</b> {access:.1f}/100<br>
                    <b>Reliability:</b> {reliab:.1f}/100<br>
                    <b>Avg Speed:</b> {avg_speed:.1f} km/h<br>
                    <br>
                    <i style="color: #666;">ArcGIS Route API analysis</i><br>
                    <i style="color: #1a73e8; font-size: 10px;">{rank_mode_label}</i>
                </div>
                """

            colors = ['darkblue', 'blue', 'cadetblue', 'lightblue', 'lightblue']
            marker_color = colors[min(max(rank - 1, 0), len(colors) - 1)]

            folium.Marker(
                location=[lat, lon],
                popup=folium.Popup(popup_html, max_width=320),
                icon=folium.DivIcon(
                    html=f'''<div style="
                        background-color: {marker_color};
                        color: white;
                        border-radius: 50%;
                        width: 28px;
                        height: 28px;
                        text-align: center;
                        line-height: 28px;
                        font-weight: bold;
                        font-size: 14px;
                        border: 2px solid white;
                        box-shadow: 0 2px 5px rgba(0,0,0,0.3);
                    ">{rank}</div>''',
                    icon_size=(28, 28),
                    icon_anchor=(14, 14)
                )
            ).add_to(target_group)

    add_multi_site_markers(
        base_sites,
        multi_site_arcgis_group,
        allow_enriched_popup=False,
        rank_mode_label="ArcGIS rank"
    )
    if has_enhanced_site_markers:
        add_multi_site_markers(
            enhanced_marker_sites,
            multi_site_enhanced_group,
            allow_enriched_popup=True,
            rank_mode_label="TomTom business rank"
        )

    # Show enhanced markers by default if we have enhanced data, otherwise show ArcGIS
    # In TomTom mode with retail analysis, show the TomTom-ranked sites by default
    if has_enhanced_site_markers and retail_sites:
        multi_site_arcgis_group.show = False
        multi_site_enhanced_group.show = True
    else:
        multi_site_arcgis_group.show = True
        multi_site_enhanced_group.show = False

    multi_site_arcgis_group.add_to(m)
    multi_site_enhanced_group.add_to(m)

    # ========================================
    # Layer 3b: AADT-enhanced site markers (TomTom + AADT toggle)
    # ========================================
    multi_site_aadt_group = folium.FeatureGroup(name="🏆 Multi-Site (TomTom + AADT)", show=False)
    aadt_marker_sites = []
    aadt_map = load_aadt_data()
    has_aadt_data = bool(aadt_map)

    # Load AADT segment coordinates for proximity matching
    aadt_segment_coords = []
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "aadt_results.json")) as f:
            aadt_file_data = json.load(f)
        for seg in aadt_file_data.get("matched_segments", []):
            aadt_segment_coords.append({
                "lat": seg["lat"], "lon": seg["lon"],
                "aadt": seg["aadt_match"]["aadt"],
            })
        for seg in aadt_file_data.get("nearest_neighbor_matches", []):
            aadt_segment_coords.append({
                "lat": seg["lat"], "lon": seg["lon"],
                "aadt": seg["aadt_nearest_neighbor"]["aadt"],
            })
        route_avg_aadt = aadt_file_data.get(
            "route_average_aadt_with_nearest_neighbor",
            aadt_file_data.get("route_average_aadt", 0),
        )
    except Exception:
        route_avg_aadt = 0

    if has_aadt_data and base_sites:
        # Re-score sites using the AADT-enhanced formula
        # accessibility 30%, traffic exposure (AADT) 25%, reliability 25%, speed 20%
        def _nearest_aadt(lat, lon):
            """Find AADT value from nearest segment by coordinates."""
            if not aadt_segment_coords:
                return route_avg_aadt
            nearest = min(
                aadt_segment_coords,
                key=lambda s: (s["lat"] - lat) ** 2 + (s["lon"] - lon) ** 2,
            )
            return nearest["aadt"]

        aadt_scored = []
        for site in all_top_sites:
            lat = site.get("latitude", 0)
            lon = site.get("longitude", 0)
            avg_aadt = _nearest_aadt(lat, lon)

            exposure = calculate_traffic_exposure_score(avg_aadt)
            access = site.get("accessibility_score", 0)
            reliab = site.get("reliability_score", 0)
            avg_speed = site.get("avg_speed", 0)

            aadt_score = (
                access * 0.30 +
                exposure * 0.25 +
                reliab * 0.25 +
                min(100, avg_speed * 2) * 0.20
            )
            aadt_scored.append({
                **site,
                "overall_score": round(aadt_score, 1),
                "avg_aadt": round(avg_aadt, 0),
                "traffic_exposure_score": round(exposure, 1),
            })

        aadt_scored.sort(key=lambda s: s["overall_score"], reverse=True)
        aadt_marker_sites = []
        for idx, site in enumerate(aadt_scored[:5], 1):
            anchored_lat, anchored_lon = tomtom_route_anchor_for_site(
                site.get("name", ""),
                site.get("latitude", 0),
                site.get("longitude", 0),
            )
            aadt_marker_sites.append({
                **site,
                "rank": idx,
                "latitude": anchored_lat,
                "longitude": anchored_lon,
            })

        add_multi_site_markers(
            aadt_marker_sites,
            multi_site_aadt_group,
            allow_enriched_popup=False,
            rank_mode_label="TomTom + AADT rank",
            show_aadt=True
        )

    has_aadt_markers = bool(aadt_marker_sites)
    aadt_top_site = aadt_marker_sites[0] if has_aadt_markers else None
    multi_site_aadt_group.add_to(m)

    # ========================================
    # TomTom enhancement layers bundle (toggle via custom checkbox)
    # ========================================
    tomtom_enhancement_layers = [comparison_group]

    if has_enhanced_site_markers:
        tomtom_enhancement_layers.append(multi_site_enhanced_group)

    # Keep ArcGIS layers available for checkbox switching
    arcgis_only_layers = [route_group]
    if has_enhanced_site_markers:
        arcgis_only_layers.append(multi_site_arcgis_group)

    # ========================================
    # Layer 4: Highlighted Selected Point (ArcGIS vs TomTom-enhanced)
    # ========================================
    optimal_arcgis_group = folium.FeatureGroup(name="⭐ Highlighted Point (ArcGIS)", show=True)
    optimal_enhanced_group = folium.FeatureGroup(name="✨ Highlighted Point (ArcGIS + TomTom)", show=False)

    optimal = results.get("optimal_location")
    if optimal:
        lat = optimal.get("latitude", 0)
        lon = optimal.get("longitude", 0)
        street = optimal.get("street_name", "Unknown")
        score = optimal.get("composite_score", 0)

        popup_html = f"""
        <div style="font-family: Arial; width: 220px;">
            <h4 style="margin: 0; color: #f9a825;">⭐ ARCGIS HIGHLIGHTED POINT</h4>
            <b>{street}</b>
            <hr style="margin: 5px 0;">
            <b>Composite Score:</b> {score:.1f}/100<br>
            <b>Accessibility:</b> {optimal.get('accessibility_score', 0):.1f}/100<br>
            <b>Reliability:</b> {optimal.get('reliability_score', 0):.1f}/100<br>
            <b>Traffic Flow:</b> {optimal.get('traffic_flow_score', 0):.1f}/100<br>
            <b>Visibility:</b> {optimal.get('visibility_score', 0):.1f}/100<br>
            <b>Avg Speed:</b> {optimal.get('avg_speed', 0):.1f} km/h<br>
            <br>
            <i style="color: #666;">ArcGIS-only highlighted route point</i>
        </div>
        """

        folium.Marker(
            location=[lat, lon],
            popup=folium.Popup(popup_html, max_width=280),
            icon=folium.Icon(color='orange', icon='star', prefix='fa')
        ).add_to(optimal_arcgis_group)

    enhanced_highlight = max(
        [s for s in retail_sites if s.get("name", "Unknown") != "Unknown"],
        key=lambda x: x.get("retail_score", 0)
    ) if retail_sites else tomtom_top_site
    if enhanced_highlight:
        elat, elon = tomtom_route_anchor_for_site(
            enhanced_highlight.get("name", ""),
            enhanced_highlight.get("latitude", 0),
            enhanced_highlight.get("longitude", 0),
        )
        ename = enhanced_highlight.get("name", "Unknown")
        retail_score = enhanced_highlight.get('retail_score', enhanced_highlight.get('overall_score', 0))
        commercial_score = enhanced_highlight.get('commercial_score', enhanced_highlight.get('overall_score', 0))
        restaurant_score = enhanced_highlight.get('restaurant_score', 0)
        office_score = enhanced_highlight.get('office_score', 0)
        star_frc_cov = enhanced_highlight.get('frc_coverage', 0)
        star_frc_num = enhanced_highlight.get('primary_frc', 8)
        star_frc_color = '#4caf50' if star_frc_cov >= 80 else ('#ff9800' if star_frc_cov >= 50 else '#f44336')
        star_frc_badge = f'<span style="background:{star_frc_color};color:white;padding:2px 5px;border-radius:3px;font-size:10px;">{star_frc_cov:.0f}% FRC{star_frc_num}</span>'

        enhanced_popup_html = f"""
        <div style="font-family: Arial; width: 230px;">
            <h4 style="margin: 0; color: #f9a825;">⭐ TOMTOM HIGHLIGHTED POINT</h4>
            <b>{ename}</b>
            <hr style="margin: 5px 0;">
            <b>Retail Score:</b> {retail_score:.1f}/100<br>
            <b>Commercial Score:</b> {commercial_score:.1f}/100<br>
            <b>Restaurant Score:</b> {restaurant_score:.1f}/100<br>
            <b>Office Score:</b> {office_score:.1f}/100<br>
            <b>TomTom FRC:</b> {star_frc_badge}<br>
            <br>
            <i style="color: #666;">Enhanced with TomTom Places/Traffic inputs</i>
        </div>
        """

        folium.Marker(
            location=[elat, elon],
            popup=folium.Popup(enhanced_popup_html, max_width=300),
            icon=folium.Icon(color='orange', icon='star', prefix='fa')
        ).add_to(optimal_enhanced_group)

    optimal_arcgis_group.show = False
    optimal_enhanced_group.show = False

    optimal_arcgis_group.add_to(m)
    optimal_enhanced_group.add_to(m)

    # Ensure highlighted-point layers follow ArcGIS/TomTom toggle state
    arcgis_only_layers.append(optimal_arcgis_group)
    if has_enhanced_results:
        tomtom_enhancement_layers.append(optimal_enhanced_group)

    # Keep highlighted point fixed and always visible in ArcGIS mode
    
    # ========================================
    # Layer 5: Retail/Business Recommendations
    # ========================================
    retail_group = folium.FeatureGroup(name="🏪 Best for Retail", show=True)
    restaurant_group = folium.FeatureGroup(name="🍽️ Best for Restaurant", show=True)
    office_group = folium.FeatureGroup(name="🏢 Best for Office", show=True)
    
    # Find best for each category
    best_retail = max(retail_sites, key=lambda x: x.get("retail_score", 0)) if retail_sites else None
    best_restaurant = max(retail_sites, key=lambda x: x.get("restaurant_score", 0)) if retail_sites else None
    best_office = max(retail_sites, key=lambda x: x.get("office_score", 0)) if retail_sites else None
    
    if best_retail:
        frc_num = best_retail.get('primary_frc', 8)
        frc_cov = best_retail.get('frc_coverage', 0)
        if frc_cov >= 80:
            cov_badge = f'<span style="background:#4caf50;color:white;padding:2px 6px;border-radius:3px;font-size:10px;">🟢 {frc_cov:.0f}% FRC{frc_num}</span>'
        elif frc_cov >= 50:
            cov_badge = f'<span style="background:#ff9800;color:white;padding:2px 6px;border-radius:3px;font-size:10px;">🟡 {frc_cov:.0f}% FRC{frc_num}</span>'
        else:
            cov_badge = f'<span style="background:#f44336;color:white;padding:2px 6px;border-radius:3px;font-size:10px;">🔴 {frc_cov:.0f}% FRC{frc_num}</span>'
        popup_html = f"""
        <div style="font-family: Arial; width: 210px;">
            <h4 style="margin: 0; color: #e91e63;">🏪 Best for RETAIL</h4>
            <b>{best_retail['name']}</b>
            <hr style="margin: 5px 0;">
            <b>Retail Score:</b> {best_retail.get('retail_score', 0):.1f}/100<br>
            <b>Commercial Score:</b> {best_retail.get('commercial_score', 0):.1f}/100<br>
            <br>
            <i style="color: #666;">High traffic exposure & visibility</i>
        </div>
        """
        # Apply offset to ensure retail marker is visible (not hidden by other markers)
        retail_lat = best_retail['latitude']
        retail_lon = best_retail['longitude']
        # Offset northeast to avoid overlaps with top sites, optimal, and office markers
        retail_lat += 0.0008  # ~88m north
        retail_lon += 0.0008  # ~70m east
        folium.Marker(
            location=[retail_lat, retail_lon],
            popup=folium.Popup(popup_html, max_width=250),
            icon=folium.Icon(color='pink', icon='shopping-cart', prefix='fa')
        ).add_to(retail_group)
    
    if best_restaurant:
        frc_num = best_restaurant.get('primary_frc', 8)
        frc_cov = best_restaurant.get('frc_coverage', 0)
        if frc_cov >= 80:
            cov_badge = f'<span style="background:#4caf50;color:white;padding:2px 6px;border-radius:3px;font-size:10px;">🟢 {frc_cov:.0f}% FRC{frc_num}</span>'
        elif frc_cov >= 50:
            cov_badge = f'<span style="background:#ff9800;color:white;padding:2px 6px;border-radius:3px;font-size:10px;">🟡 {frc_cov:.0f}% FRC{frc_num}</span>'
        else:
            cov_badge = f'<span style="background:#f44336;color:white;padding:2px 6px;border-radius:3px;font-size:10px;">🔴 {frc_cov:.0f}% FRC{frc_num}</span>'
        popup_html = f"""
        <div style="font-family: Arial; width: 210px;">
            <h4 style="margin: 0; color: #ff5722;">🍽️ Best for RESTAURANT</h4>
            <b>{best_restaurant['name']}</b>
            <hr style="margin: 5px 0;">
            <b>Restaurant Score:</b> {best_restaurant.get('restaurant_score', 0):.1f}/100<br>
            <b>Commercial Score:</b> {best_restaurant.get('commercial_score', 0):.1f}/100<br>
            <br>
            <i style="color: #666;">Great accessibility & parking</i>
        </div>
        """
        # Apply offset to ensure restaurant marker is visible
        restaurant_lat = best_restaurant['latitude']
        restaurant_lon = best_restaurant['longitude']
        # Offset southeast to avoid overlaps
        restaurant_lat -= 0.0008  # ~88m south
        restaurant_lon += 0.0008  # ~70m east
        folium.Marker(
            location=[restaurant_lat, restaurant_lon],
            popup=folium.Popup(popup_html, max_width=250),
            icon=folium.Icon(color='orange', icon='utensils', prefix='fa')
        ).add_to(restaurant_group)
    
    if best_office:
        frc_num = best_office.get('primary_frc', 8)
        frc_cov = best_office.get('frc_coverage', 0)
        if frc_cov >= 80:
            cov_badge = f'<span style="background:#4caf50;color:white;padding:2px 6px;border-radius:3px;font-size:10px;">🟢 {frc_cov:.0f}% FRC{frc_num}</span>'
        elif frc_cov >= 50:
            cov_badge = f'<span style="background:#ff9800;color:white;padding:2px 6px;border-radius:3px;font-size:10px;">🟡 {frc_cov:.0f}% FRC{frc_num}</span>'
        else:
            cov_badge = f'<span style="background:#f44336;color:white;padding:2px 6px;border-radius:3px;font-size:10px;">🔴 {frc_cov:.0f}% FRC{frc_num}</span>'
        popup_html = f"""
        <div style="font-family: Arial; width: 210px;">
            <h4 style="margin: 0; color: #607d8b;">🏢 Best for OFFICE</h4>
            <b>{best_office['name']}</b>
            <hr style="margin: 5px 0;">
            <b>Office Score:</b> {best_office.get('office_score', 0):.1f}/100<br>
            <b>Commercial Score:</b> {best_office.get('commercial_score', 0):.1f}/100<br>
            <br>
            <i style="color: #666;">Reliable commute & accessibility</i>
        </div>
        """
        # Apply offset to ensure office marker is visible (not hidden by other markers)
        office_lat = best_office['latitude']
        office_lon = best_office['longitude']
        # Offset southwest to avoid overlaps with top sites, optimal, and retail markers
        office_lat -= 0.0008  # ~88m south
        office_lon -= 0.0008  # ~70m west
        folium.Marker(
            location=[office_lat, office_lon],
            popup=folium.Popup(popup_html, max_width=250),
            icon=folium.Icon(color='gray', icon='building', prefix='fa')
        ).add_to(office_group)
    
    retail_group.add_to(m)
    restaurant_group.add_to(m)
    office_group.add_to(m)
    
    # ========================================
    # Layer 7: All Parking POIs near route
    # ========================================
    # Visible toggle layer in LayerControl (acts as on/off switch only).
    parking_group = folium.FeatureGroup(name="🅿️ Parkings", show=False)
    # Data layers swapped dynamically by mode when parking toggle is enabled.
    parking_arcgis_data_group = folium.FeatureGroup(name="🅿️ Parkings (ArcGIS Data)", show=False)
    parking_tomtom_data_group = folium.FeatureGroup(name="🅿️ Parkings (ArcGIS + TomTom Data)", show=False)

    # Fetch parking POIs across the route area (large radius to cover the corridor)
    try:
        print(f"  Fetching parking POIs across {ROUTE_AREA_NAME}...")
        arcgis_parking = []
        combined_parking = []
        seen_arcgis = set()
        seen_combined = set()

        # Search from multiple points derived from route_config to cover the corridor
        search_points = parking_search_points()
        
        # 1.2km radius covers the route corridor without pulling in parking from across the city.
        for lat, lon in search_points:
            # ArcGIS parking (always used in ArcGIS mode)
            arcgis_results = search_pois_arcgis_places_first(lat, lon, "parking", radius=1200, limit=500)
            for poi in arcgis_results:
                poi_key = (
                    round(poi.get("latitude", 0), 5),
                    round(poi.get("longitude", 0), 5),
                    str(poi.get("name", "")).strip().lower(),
                )
                if poi_key not in seen_arcgis:
                    seen_arcgis.add(poi_key)
                    arcgis_parking.append({**poi, "source": "ArcGIS"})
                if poi_key not in seen_combined:
                    seen_combined.add(poi_key)
                    combined_parking.append({**poi, "source": "ArcGIS"})

            # TomTom parking enrichments (TomTom mode only; no-op when key missing)
            for category in ["OPEN_PARKING_AREA", "PARKING_GARAGE"]:
                tomtom_results = search_category_pois(lat, lon, category, radius=1200, limit=200)
                for poi in tomtom_results:
                    poi_key = (
                        round(poi.get("latitude", 0), 5),
                        round(poi.get("longitude", 0), 5),
                        str(poi.get("name", "")).strip().lower(),
                    )
                    if poi_key not in seen_combined:
                        seen_combined.add(poi_key)
                        combined_parking.append({**poi, "source": "TomTom"})

        print(f"  Found {len(arcgis_parking)} ArcGIS parking locations")
        print(f"  Found {len(combined_parking)} combined parking locations (ArcGIS + TomTom)")

        def add_parking_markers(target_group, poi_list, show_source=False):
            for poi in poi_list:
                poi_lat = poi.get("latitude", 0)
                poi_lon = poi.get("longitude", 0)
                poi_name = poi.get("name", "Parking")
                poi_category = poi.get("category", "PARKING")
                source = poi.get("source", "ArcGIS")

                if "GARAGE" in poi_category.upper():
                    color = '#1976d2'
                    icon_text = '🅿️G'
                else:
                    color = '#42a5f5'
                    icon_text = '🅿️'

                source_line = f"<b>Source:</b> {source}<br>" if show_source else ""
                popup_html = f"""
                <div style="font-family: Arial; width: 220px;">
                    <h4 style="margin: 0; color: {color};">{icon_text} {poi_name}</h4>
                    <hr style="margin: 5px 0;">
                    {source_line}
                    <b>Type:</b> {str(poi_category).replace('_', ' ').title()}<br>
                    <br>
                    <span style="font-size: 10px; color: #666;">{poi.get('address', 'N/A')}</span>
                </div>
                """

                folium.CircleMarker(
                    location=[poi_lat, poi_lon],
                    radius=5,
                    color=color,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.6,
                    popup=folium.Popup(popup_html, max_width=250),
                    tooltip=poi_name
                ).add_to(target_group)

        add_parking_markers(parking_arcgis_data_group, arcgis_parking, show_source=False)
        add_parking_markers(parking_tomtom_data_group, combined_parking, show_source=True)
            
    except Exception as e:
        print(f"Note: Could not fetch parking POIs: {e}")
    
    parking_group.add_to(m)
    parking_arcgis_data_group.add_to(m)
    parking_tomtom_data_group.add_to(m)
    
    # ========================================
    # Layer 9: Parking Near Special Locations (Optimal, Retail, Restaurant, Office)
    # ========================================
    parking_special_group = folium.FeatureGroup(name="⭐ Parking Near Best Locations", show=False)
    
    special_locations = []
    
    # Optimal location
    optimal = results.get("optimal_location")
    if optimal:
        special_locations.append({
            "name": f"⭐ Optimal: {optimal.get('street_name', 'Unknown')}",
            "lat": optimal.get("latitude", 0),
            "lon": optimal.get("longitude", 0),
            "color": "#f9a825",
            "icon": "⭐"
        })
    
    # Best retail location
    retail_sites = results.get("retail_analysis", {}).get("all_sites", [])
    if retail_sites:
        best_retail = max(retail_sites, key=lambda x: x.get("retail_score", 0))
        special_locations.append({
            "name": f"🏪 Best Retail: {best_retail.get('name', 'Unknown')}",
            "lat": best_retail.get("latitude", 0),
            "lon": best_retail.get("longitude", 0),
            "color": "#e91e63",
            "icon": "🏪"
        })
        
        # Best restaurant location
        best_restaurant = max(retail_sites, key=lambda x: x.get("restaurant_score", 0))
        special_locations.append({
            "name": f"🍽️ Best Restaurant: {best_restaurant.get('name', 'Unknown')}",
            "lat": best_restaurant.get("latitude", 0),
            "lon": best_restaurant.get("longitude", 0),
            "color": "#ff5722",
            "icon": "🍽️"
        })
        
        # Best office location
        best_office = max(retail_sites, key=lambda x: x.get("office_score", 0))
        special_locations.append({
            "name": f"🏢 Best Office: {best_office.get('name', 'Unknown')}",
            "lat": best_office.get("latitude", 0),
            "lon": best_office.get("longitude", 0),
            "color": "#607d8b",
            "icon": "🏢"
        })
    
    try:
        import math
        
        def haversine_distance(lat1, lon1, lat2, lon2):
            """Calculate distance in meters between two points"""
            R = 6371000  # Earth radius in meters
            lat1_rad = math.radians(lat1)
            lat2_rad = math.radians(lat2)
            delta_lat = math.radians(lat2 - lat1)
            delta_lon = math.radians(lon2 - lon1)
            a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
            return R * c
        
        special_parking_count = 0
        
        for location_index, location in enumerate(special_locations, start=1):
            loc_lat = location["lat"]
            loc_lon = location["lon"]
            loc_name = location["name"]
            loc_color = location["color"]
            loc_icon = location["icon"]

            if loc_lat == 0 and loc_lon == 0:
                continue
            
            # Fetch parking around each special location using ArcGIS Geocoding API
            parking_pois = search_pois_arcgis_places_first(loc_lat, loc_lon, "parking", radius=350, limit=200)
            
            # Track parking for this specific location only
            local_seen = set()
            loc_parking_count = 0
            
            for poi in parking_pois:
                poi_lat = poi.get("latitude", 0)
                poi_lon = poi.get("longitude", 0)
                poi_name = poi.get("name", "Parking")
                poi_category = poi.get("category", "PARKING")
                
                # Calculate exact distance
                distance = haversine_distance(loc_lat, loc_lon, poi_lat, poi_lon)
                
                # Only show if within 200m
                if distance <= 200:
                    poi_key = (
                        round(poi_lat, 6),
                        round(poi_lon, 6),
                        str(poi_name).strip().lower(),
                        str(poi_category).strip().lower(),
                    )
                    
                    # Only deduplicate within this specific location's parking
                    if poi_key not in local_seen:
                        local_seen.add(poi_key)
                        special_parking_count += 1
                        loc_parking_count += 1
                        
                        # Size based on distance
                        if distance <= 100:
                            opacity = 0.85
                            radius = 9
                            distance_label = f"🟢 {distance:.0f}m"
                        else:
                            opacity = 0.6
                            radius = 6
                            distance_label = f"{distance:.0f}m"
                        
                        popup_html = f"""
                        <div style="font-family: Arial; width: 240px;">
                            <h4 style="margin: 0; color: {loc_color};">{loc_icon} {poi_name}</h4>
                            <hr style="margin: 5px 0;">
                            <b>Near:</b> {loc_name}<br>
                            <b>Distance:</b> {distance_label}<br>
                            <b>Type:</b> {poi_category.replace('_', ' ').title()}<br>
                            <br>
                            <span style="font-size: 10px; color: #666;">{poi.get('address', 'N/A')}</span>
                        </div>
                        """
                        
                        folium.CircleMarker(
                            location=[poi_lat, poi_lon],
                            radius=radius,
                            color=loc_color,
                            fill=True,
                            fill_color=loc_color,
                            fill_opacity=opacity,
                            weight=2,
                            popup=folium.Popup(popup_html, max_width=280),
                            tooltip=f"{loc_icon} {poi_name} ({distance:.0f}m)"
                        ).add_to(parking_special_group)
            
            folium.CircleMarker(
                location=[loc_lat, loc_lon],
                radius=4,
                color=loc_color,
                fill=True,
                fill_color=loc_color,
                fill_opacity=1.0,
                tooltip=f"{loc_icon} {loc_name}"
            ).add_to(parking_special_group)
            
            print(f"    {loc_name}: {loc_parking_count} parking locations within 200m")
        
        print(f"  Total parking markers for special locations: {special_parking_count}")
        
    except Exception as e:
        print(f"Note: Could not fetch parking near special locations: {e}")
        import traceback
        traceback.print_exc()
    
    parking_special_group.add_to(m)
    tomtom_enhancement_layers.append(parking_special_group)

    api_health = get_tomtom_api_health_report()
    status_counts = api_health.get("status_counts", {})
    if api_health.get("request_attempts", 0) > 0:
        print(
            "  TomTom API health: "
            f"attempts={api_health.get('request_attempts', 0)}, "
            f"success={api_health.get('successful_requests', 0)}, "
            f"failed={api_health.get('failed_requests', 0)}, "
            f"429={api_health.get('rate_limited_responses', 0)}, "
            f"retries={api_health.get('retried_requests', 0)}, "
            f"exceptions={api_health.get('exceptions', 0)}, "
            f"cache_hits={api_health.get('cache_hits', 0)}"
        )
        if status_counts:
            print(f"  TomTom status breakdown: {json.dumps(status_counts, sort_keys=True)}")

    strict_tomtom = os.getenv("STRICT_TOMTOM_ERRORS", "0").lower() in {"1", "true", "yes"}
    if strict_tomtom and (
        api_health.get("failed_requests", 0) > 0
        or api_health.get("exceptions", 0) > 0
    ):
        raise RuntimeError(
            "TomTom API requests had failures. "
            "Set STRICT_TOMTOM_ERRORS=0 to continue in best-effort mode."
        )
    
    # ========================================
    # Add Layer Control
    # ========================================
    # Hide overlays from layer control (retail/restaurant/office always visible)
    overlay_groups_hidden = [
        route_group,
        comparison_group,
        od_group,
        multi_site_arcgis_group,
        multi_site_enhanced_group,
        multi_site_aadt_group,
        optimal_arcgis_group,
        optimal_enhanced_group,
        parking_arcgis_data_group,
        parking_tomtom_data_group,
        parking_special_group,
        retail_group,
        restaurant_group,
        office_group,
    ]
    for overlay_group in overlay_groups_hidden:
        overlay_group.control = False

    folium.LayerControl(collapsed=False).add_to(m)
    
    # ========================================
    # Add Title (Dynamic)
    # ========================================
    route_info = travel_data.get("routes", [{}])[0]
    route_name = route_info.get("routeName", "Route Analysis")
    
    title_html = f'''
    <div style="position: fixed; 
                top: 10px; left: 60px; 
                background-color: rgba(255, 255, 255, 0.95); 
                padding: 8px 15px; 
                border-radius: 5px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.2);
                z-index: 9998;
                font-family: Arial;">
        <h3 style="margin: 0; color: #1a73e8; font-size: 15px;">Site Map</h3>
        <p style="margin: 3px 0 0 0; color: #666; font-size: 11px;">{route_name}</p>
    </div>
    '''
    m.get_root().html.add_child(folium.Element(title_html))

    # ========================================
    # Add TomTom Enhancement Checkbox
    # ========================================
    map_var = m.get_name()
    core_arcgis_layers = [optimal_arcgis_group, multi_site_arcgis_group]
    # optimal_enhanced_group is appended last so the JS toggle adds it last,
    # ensuring the star marker renders on top of all other TomTom markers.
    core_tomtom_layers = [comparison_group]

    if has_enhanced_site_markers:
        core_tomtom_layers.append(multi_site_enhanced_group)

    core_tomtom_layers.append(optimal_enhanced_group)

    # Deduplicate while preserving order
    def unique_layers(layers):
        seen = set()
        unique = []
        for layer in layers:
            name = layer.get_name()
            if name in seen:
                continue
            seen.add(name)
            unique.append(layer)
        return unique

    tomtom_layer_vars = [layer.get_name() for layer in unique_layers(core_tomtom_layers)]
    arcgis_layer_vars = [layer.get_name() for layer in unique_layers(core_arcgis_layers)]
    has_enhanced_js = "true" if has_enhanced_results else "false"
    has_aadt_js = "true" if has_aadt_markers else "false"
    aadt_layer_var = multi_site_aadt_group.get_name()
    # Also track the TomTom enhanced markers layer name for AADT toggling
    enhanced_markers_layer_var = multi_site_enhanced_group.get_name()
    parking_toggle_layer_var = parking_group.get_name()
    parking_arcgis_layer_var = parking_arcgis_data_group.get_name()
    parking_tomtom_layer_var = parking_tomtom_data_group.get_name()
    default_checked = ""

    toggle_html = f'''
    <div id="enhancement-toggle-panel" style="position: fixed;
                top: 58px; left: 60px;
                background-color: rgba(255, 255, 255, 0.95);
                padding: 8px 10px;
                border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.2);
                z-index: 9998;
                font-family: Arial;
                font-size: 12px;">
        <label style="display:flex; align-items:center; gap:8px; cursor:pointer;">
            <input type="checkbox" id="tomtom-enhancement-toggle" {default_checked}>
            <span>Use TomTom data</span>
        </label>
        <label id="aadt-toggle-label" style="display:none; align-items:center; gap:8px; cursor:pointer; margin-top:4px;">
            <input type="checkbox" id="aadt-toggle">
            <span>Include AADT</span>
        </label>
    </div>
    '''
    m.get_root().html.add_child(folium.Element(toggle_html))

    toggle_script = f'''
    (function() {{
        var mapVarName = "{map_var}";
        var tomtomLayerNames = {tomtom_layer_vars};
        var arcgisLayerNames = {arcgis_layer_vars};
        var hasEnhanced = {has_enhanced_js};
        var hasAadt = {has_aadt_js};
        var aadtLayerName = "{aadt_layer_var}";
        var enhancedMarkersLayerName = "{enhanced_markers_layer_var}";
        var parkingToggleLayerName = "{parking_toggle_layer_var}";
        var parkingArcgisLayerName = "{parking_arcgis_layer_var}";
        var parkingTomtomLayerName = "{parking_tomtom_layer_var}";
        var arcgisBasemapLayerNames = {json.dumps(arcgis_basemap_layer_vars)};
        var tomtomBasemapLayerNames = {json.dumps(tomtom_basemap_layer_vars)};

        function resolveLayers(names) {{
            return names.map(function(name) {{ return window[name]; }}).filter(Boolean);
        }}

        function setTomTomBasemapOptionsVisibility(enabled) {{
            var control = document.querySelector('.leaflet-control-layers');
            if (!control) return;
            var labels = control.querySelectorAll('.leaflet-control-layers-base label');
            labels.forEach(function(label) {{
                var text = (label.textContent || '').trim();
                if (text.indexOf('TomTom') !== -1) {{
                    label.style.display = enabled ? '' : 'none';
                    var input = label.querySelector('input');
                    if (input) input.disabled = !enabled;
                }}
            }});
        }}

        function enforceBasemapMode(enabled, mapRef) {{
            var tomtomBasemaps = resolveLayers(tomtomBasemapLayerNames);
            var arcgisBasemaps = resolveLayers(arcgisBasemapLayerNames);

            if (enabled) {{
                var hasTomTom = tomtomBasemaps.some(function(layer) {{
                    return mapRef.hasLayer(layer);
                }});

                // When TomTom mode turns on, default the visual basemap to TomTom Basic.
                if (!hasTomTom && tomtomBasemaps.length > 0) {{
                    arcgisBasemaps.forEach(function(layer) {{
                        if (mapRef.hasLayer(layer)) mapRef.removeLayer(layer);
                    }});
                    mapRef.addLayer(tomtomBasemaps[0]);
                }}
            }} else {{
                tomtomBasemaps.forEach(function(layer) {{
                    if (mapRef.hasLayer(layer)) mapRef.removeLayer(layer);
                }});

                var hasArcgis = arcgisBasemaps.some(function(layer) {{
                    return mapRef.hasLayer(layer);
                }});
                if (!hasArcgis && arcgisBasemaps.length > 0) {{
                    mapRef.addLayer(arcgisBasemaps[0]);
                }}
            }}

            setTomTomBasemapOptionsVisibility(enabled);
        }}

        function initializeToggle(retriesLeft) {{
            var checkbox = document.getElementById('tomtom-enhancement-toggle');
            var aadtCheckbox = document.getElementById('aadt-toggle');
            var aadtLabel = document.getElementById('aadt-toggle-label');
            var mapRef = window[mapVarName];

            if (!checkbox || !mapRef) {{
                if (retriesLeft > 0) {{
                    setTimeout(function() {{ initializeToggle(retriesLeft - 1); }}, 50);
                }}
                return;
            }}

            function setAadtState(aadtEnabled) {{
                var aadtLayer = window[aadtLayerName];
                var enhancedLayer = window[enhancedMarkersLayerName];
                if (!aadtLayer) return;
                var enhancementEnabled = checkbox && checkbox.checked;
                if (aadtEnabled) {{
                    // Show AADT markers, hide TomTom-only markers
                    if (!mapRef.hasLayer(aadtLayer)) mapRef.addLayer(aadtLayer);
                    if (enhancedLayer && mapRef.hasLayer(enhancedLayer)) mapRef.removeLayer(enhancedLayer);
                }} else {{
                    // Hide AADT markers, show TomTom-only markers
                    if (mapRef.hasLayer(aadtLayer)) mapRef.removeLayer(aadtLayer);
                    // Only restore TomTom markers when TomTom mode is enabled.
                    if (enhancementEnabled && enhancedLayer && !mapRef.hasLayer(enhancedLayer)) mapRef.addLayer(enhancedLayer);
                }}
                if (typeof window.updateSummaryForAadt === 'function') {{
                    window.updateSummaryForAadt(aadtEnabled);
                }}
                if (typeof window.updateSourcesForAadt === 'function') {{
                    window.updateSourcesForAadt(aadtEnabled);
                }}
            }}

            function setEnhancementState(enabled) {{
                var tomtomLayers = resolveLayers(tomtomLayerNames);
                var arcgisLayers = resolveLayers(arcgisLayerNames);
                var parkingToggleLayer = window[parkingToggleLayerName];
                var parkingArcgisLayer = window[parkingArcgisLayerName];
                var parkingTomtomLayer = window[parkingTomtomLayerName];

                tomtomLayers.forEach(function(layer) {{
                    if (enabled) {{
                        if (!mapRef.hasLayer(layer)) mapRef.addLayer(layer);
                    }} else {{
                        if (mapRef.hasLayer(layer)) mapRef.removeLayer(layer);
                    }}
                }});

                arcgisLayers.forEach(function(layer) {{
                    if (enabled && hasEnhanced) {{
                        if (mapRef.hasLayer(layer)) mapRef.removeLayer(layer);
                    }} else {{
                        if (!mapRef.hasLayer(layer)) mapRef.addLayer(layer);
                    }}
                }});

                // Keep parking data mode-specific while preserving the single Parkings toggle.
                var parkingEnabled = parkingToggleLayer && mapRef.hasLayer(parkingToggleLayer);
                if (parkingEnabled) {{
                    if (enabled) {{
                        if (parkingArcgisLayer && mapRef.hasLayer(parkingArcgisLayer)) mapRef.removeLayer(parkingArcgisLayer);
                        if (parkingTomtomLayer && !mapRef.hasLayer(parkingTomtomLayer)) mapRef.addLayer(parkingTomtomLayer);
                    }} else {{
                        if (parkingTomtomLayer && mapRef.hasLayer(parkingTomtomLayer)) mapRef.removeLayer(parkingTomtomLayer);
                        if (parkingArcgisLayer && !mapRef.hasLayer(parkingArcgisLayer)) mapRef.addLayer(parkingArcgisLayer);
                    }}
                }} else {{
                    if (parkingArcgisLayer && mapRef.hasLayer(parkingArcgisLayer)) mapRef.removeLayer(parkingArcgisLayer);
                    if (parkingTomtomLayer && mapRef.hasLayer(parkingTomtomLayer)) mapRef.removeLayer(parkingTomtomLayer);
                }}

                // Show/hide AADT toggle
                if (hasAadt && aadtLabel) {{
                    aadtLabel.style.display = enabled ? 'flex' : 'none';
                    if (!enabled && aadtCheckbox) {{
                        aadtCheckbox.checked = false;
                        setAadtState(false);
                    }} else if (enabled && aadtCheckbox && aadtCheckbox.checked) {{
                        setAadtState(true);
                    }}
                }}

                enforceBasemapMode(enabled, mapRef);
            }}

            checkbox.addEventListener('change', function() {{
                setEnhancementState(this.checked);
            }});
            setEnhancementState(checkbox.checked);
            setTimeout(function() {{
                setTomTomBasemapOptionsVisibility(checkbox && checkbox.checked);
            }}, 120);

            // Sync parking data layers when user toggles the single Parkings layer in LayerControl.
            mapRef.on('overlayadd', function(e) {{
                var parkingToggleLayer = window[parkingToggleLayerName];
                if (parkingToggleLayer && e.layer === parkingToggleLayer) {{
                    setEnhancementState(checkbox && checkbox.checked);
                }}
            }});
            mapRef.on('overlayremove', function(e) {{
                var parkingToggleLayer = window[parkingToggleLayerName];
                var parkingArcgisLayer = window[parkingArcgisLayerName];
                var parkingTomtomLayer = window[parkingTomtomLayerName];
                if (parkingToggleLayer && e.layer === parkingToggleLayer) {{
                    if (parkingArcgisLayer && mapRef.hasLayer(parkingArcgisLayer)) mapRef.removeLayer(parkingArcgisLayer);
                    if (parkingTomtomLayer && mapRef.hasLayer(parkingTomtomLayer)) mapRef.removeLayer(parkingTomtomLayer);
                }}
            }});

            if (aadtCheckbox) {{
                aadtCheckbox.addEventListener('change', function() {{
                    setAadtState(this.checked);
                }});
            }}
        }}

        setTimeout(function() {{ initializeToggle(40); }}, 0);
    }})();
    '''
    m.get_root().script.add_child(folium.Element(toggle_script))

    # ========================================
    # Add Sources Panel
    # ========================================
    result_sources = results.get("data_provenance", {})
    run_sources = result_sources.get("run", {})
    metric_sources = result_sources.get("metric_sources", {})

    fallback_provider_meta = travel_data.get("provider_metadata", {})
    travel_provider = run_sources.get("travel_provider") or fallback_provider_meta.get("provider", "Unknown")
    travel_strategy = (
        run_sources.get("travel_provider_metadata", {}).get("strategy")
        or fallback_provider_meta.get("strategy")
        or "ArcGIS first, TomTom fallback"
    )
    display_strategy = travel_strategy.replace("TomTom fallback", "TomTom enhancement")
    if "ArcGIS" in display_strategy and "TomTom" in display_strategy:
        display_strategy = "ArcGIS + TomTom"
    fallback_from = (
        run_sources.get("travel_provider_metadata", {}).get("fallback_from")
        or fallback_provider_meta.get("fallback_from")
    )

    fallback_row = ""
    metric_rows = ""
    metric_order = [
        ("AADT", "aadt_source"),
        ("Access zones", "accessibility_zones"),
        ("Best point score", "optimal_location_scoring"),
        ("Business scores", "retail_business_scoring"),
        ("FRC classification", "frc_classification"),
        ("Map basemap", "map_basemap_sources"),
        ("Map POI markers", "map_poi_visualization"),
        ("Parking", "parking_api_sources"),
        ("Route + time", "route_geometry_and_segment_times"),
        ("Site scores", "multi_site_scoring"),
    ]

    # Initial HTML always renders as ArcGIS mode (default); JS switches on toggle
    def resolve_metric_source(metric_key: str) -> str:
        if metric_key == "parking_api_sources":
            return "ArcGIS Places API + ArcGIS Geocoding API"
        if metric_key in {
            "route_geometry_and_segment_times",
            "multi_site_scoring",
            "optimal_location_scoring",
            "accessibility_zones",
            "retail_business_scoring",
        }:
            return "ArcGIS Route API"
        if metric_key == "map_poi_visualization":
            return "ArcGIS Geocoding API (map markers only, not used in scoring)"
        if metric_key == "map_basemap_sources":
            return "ArcGIS Basemap Tile Services API"
        if metric_key == "frc_classification":
            return "Speed-inferred from ArcGIS Route API"
        if metric_key == "aadt_source":
            return "TomTom Traffic Volume Sample"
        return metric_sources.get(metric_key, "Not specified")

    for row_index, (label, key) in enumerate(metric_order):
        source_display = str(resolve_metric_source(key)).replace("TomTom fallback", "TomTom enhancement")
        row_bg = "#ffffff" if row_index % 2 == 0 else "#f8faff"
        # AADT row is hidden by default; FRC row is visible in ArcGIS mode.
        row_display_style = "display:none;" if key == "aadt_source" else ""
        metric_rows += (
            f"<tr id='source-row-{key}' style='background:{row_bg};{row_display_style}'>"
            + f"<td style='padding:8px 10px;vertical-align:top;color:#2b2b2b;font-size:11px;width:39%;'><b>{label}</b></td>"
            + f"<td id='source-cell-{key}' style='padding:8px 10px;color:#4d4d4d;font-size:11px;line-height:1.35;'>{source_display}</td>"
            + "</tr>"
        )

    sources_html = f'''
    <div id="sources-panel" style="position: fixed;
                bottom: 20px; left: 10px;
                width: 430px;
                max-height: 55vh;
                overflow-y: auto;
                background-color: rgba(255, 255, 255, 0.98);
                padding: 14px;
                border-radius: 10px;
                border: 1px solid #d9e7ff;
                box-shadow: 0 8px 24px rgba(26, 115, 232, 0.15);
                z-index: 9998;
                font-family: Arial, sans-serif;
                font-size: 12px;
                transition: all 0.3s;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
            <h3 style="margin: 0; color: #1a73e8; font-size: 15px; letter-spacing: 0.1px;">🧾 Sources</h3>
            <button onclick="document.getElementById('sources-panel').style.display='none';document.getElementById('toggle-sources').style.display='block'"
                    style="background: #eef4ff; border: 1px solid #d0def8; border-radius: 50%; width: 24px; height: 24px; font-size: 16px; cursor: pointer; color: #5f6368; line-height: 20px;">×</button>
        </div>

        <div style="margin-bottom: 10px; background: linear-gradient(180deg, #f3f8ff 0%, #edf5ff 100%); padding: 10px; border-radius: 8px; border: 1px solid #d7e7ff;">
            <span id="sources-provider-label" style="display:inline-block; background:#1a73e8; color:white; font-size:10px; padding:2px 7px; border-radius:999px; margin-bottom:6px;">Provider</span><br>
            <b id="sources-provider-name" style="color:#1a73e8; font-size:13px;">ArcGIS</b><br>
            {fallback_row}
        </div>

        <table style="width: 100%; border-collapse: separate; border-spacing: 0; border: 1px solid #e3ebf8; border-radius: 8px; overflow: hidden; font-size: 11px;">
            {metric_rows}
        </table>
    </div>

    <button id="toggle-sources" onclick="document.getElementById('sources-panel').style.display='block'; this.style.display='none';"
            style="position: fixed;
               bottom: 20px; left: 10px;
                   display: none;
                   background: linear-gradient(180deg, #ffffff 0%, #f6f9ff 100%);
                   border: 1px solid #d6e4fb;
                   border-radius: 20px;
                   padding: 9px 14px;
                   cursor: pointer;
                   box-shadow: 0 6px 14px rgba(26, 115, 232, 0.2);
                   z-index: 9999;
                   font-size: 12px;
                   color: #1a73e8;
                   font-weight: 600;">
        🧾 Show Sources
    </button>
    '''
    m.get_root().html.add_child(folium.Element(sources_html))
    
    # ========================================
    # Add Analysis Summary Panel
    # ========================================
    # Extract key insights for summary
    top_site = results.get("multi_site_comparison", {}).get("top_sites", [{}])[0]
    optimal = results.get("optimal_location", {})
    # Get route info (already extracted above for title)
    summaries = route_info.get("summaries", [{}])
    distance_m = summaries[0].get("distance", 0) if summaries else 0
    distance_km = distance_m / 1000
    
    def extract_route_metrics(route_dataset):
        route = (route_dataset or {}).get("routes", [{}])[0]
        summary = route.get("summaries", [{}])[0] if route.get("summaries") else {}
        # TomTom Traffic Stats uses "averageTravelTime" (seconds); ArcGIS uses "travelTimeInSeconds"
        travel_seconds = (
            summary.get("travelTimeInSeconds")
            or summary.get("travelTime")
            or summary.get("averageTravelTime")
            or 0
        )
        # Neither TomTom nor ArcGIS Traffic Stats include an explicit delay field.
        # Compute delay as: actual travel time - free-flow travel time,
        # where free-flow = segment distance / speed limit (summed per segment).
        segments = route.get("segmentResults", [])
        total_distance_m = sum(seg.get("distance", 0) or 0 for seg in segments)

        free_flow_seconds = 0.0
        actual_seconds_sum = 0.0
        for seg in segments:
            dist = seg.get("distance", 0) or 0
            speed_limit_kmh = seg.get("speedLimit", 0) or 0
            if speed_limit_kmh > 0:
                free_flow_seconds += (dist / 1000.0) / speed_limit_kmh * 3600.0
            tr = (seg.get("segmentTimeResults") or [{}])[0]
            actual_seconds_sum += tr.get("averageTravelTime", 0) or 0

        # Travel time: use summary field if available, else sum segments
        if travel_seconds == 0:
            travel_seconds = actual_seconds_sum

        delay_seconds = max(0.0, actual_seconds_sum - free_flow_seconds) if free_flow_seconds > 0 else 0.0

        return {
            "distance_km": float(total_distance_m) / 1000.0,
            "travel_min": float(travel_seconds) / 60.0,
            "delay_min": float(delay_seconds) / 60.0,
        }

    # ArcGIS metrics come from the route-solved data which has the correct single-path distance.
    # For TomTom: derive travel time from TomTom Traffic Stats data but use the same
    # route-solved distance (same physical road — only traffic conditions differ).
    arcgis_base = esri_data if esri_data else travel_data
    arcgis_metrics = extract_route_metrics(arcgis_base)

    if tomtom_data:
        tomtom_time_metrics = extract_route_metrics(tomtom_data)
        route_distance_km = arcgis_metrics["distance_km"] or extract_route_metrics(travel_data)["distance_km"]
        tomtom_metrics = {
            "distance_km": route_distance_km,   # same physical route
            "travel_min": tomtom_time_metrics["travel_min"],
            "delay_min": tomtom_time_metrics["delay_min"],
        }
    else:
        tomtom_metrics = extract_route_metrics(travel_data)

    # Build reasoning for top site
    top_name = top_site.get("name", "N/A")
    top_score = top_site.get("overall_score", 0)
    top_access = top_site.get("accessibility_score", 0)
    top_reliab = top_site.get("reliability_score", 0)
    top_avg_time = top_site.get("nearby_segment_time_seconds", 0)

    # TomTom-mode top-site summary should match TomTom ranked markers
    tomtom_summary_site = tomtom_top_site if tomtom_top_site else top_site
    
    # Determine reasoning based on scores
    access_reason = "excellent accessibility" if top_access >= 90 else "good accessibility" if top_access >= 70 else "moderate accessibility"
    reliab_reason = "high reliability" if top_reliab >= 70 else "acceptable reliability" if top_reliab >= 50 else "variable traffic"
    
    # Optimal location reasoning
    opt_name = optimal.get("street_name", "N/A")
    opt_score = optimal.get("composite_score", 0)
    opt_access_score = optimal.get("accessibility_score", 0)
    opt_flow_score = optimal.get("traffic_flow_score", 0)
    enhanced_opt_name = enhanced_highlight.get("name", "N/A") if enhanced_highlight else opt_name
    enhanced_opt_score = (
        enhanced_highlight.get("commercial_score", enhanced_highlight.get("overall_score", 0))
        if enhanced_highlight
        else opt_score
    )

    enhanced_name = tomtom_summary_site.get("name", "N/A") if tomtom_summary_site else top_name
    enhanced_score = tomtom_summary_site.get("overall_score", 0) if tomtom_summary_site else top_score
    enhanced_avg_time = tomtom_summary_site.get("nearby_segment_time_seconds", top_avg_time) if tomtom_summary_site else top_avg_time
    enhanced_line_1 = (
        f"Accessibility: {tomtom_summary_site.get('accessibility_score', 0):.1f}/100"
        if tomtom_summary_site
        else f"Accessibility: {top_access:.1f}/100"
    )
    enhanced_line_2 = (
        f"Reliability: {tomtom_summary_site.get('reliability_score', 0):.1f}/100"
        if tomtom_summary_site
        else f"Reliability: {top_reliab:.1f}/100"
    )

    # AADT-enhanced summary data
    if aadt_top_site:
        aadt_name = aadt_top_site.get("name", "N/A")
        aadt_score = aadt_top_site.get("overall_score", 0)
        aadt_access = aadt_top_site.get("accessibility_score", 0)
        aadt_reliab = aadt_top_site.get("reliability_score", 0)
        aadt_exposure = aadt_top_site.get("traffic_exposure_score", 0)
        aadt_avg = aadt_top_site.get("avg_aadt", 0)
        aadt_avg_time = aadt_top_site.get("nearby_segment_time_seconds", 0)
    else:
        aadt_name = top_name
        aadt_score = top_score
        aadt_access = top_access
        aadt_reliab = top_reliab
        aadt_exposure = 0
        aadt_avg = 0
        aadt_avg_time = top_avg_time
    
    summary_html = f'''
    <div id="summary-panel" style="position: fixed; 
                bottom: 50px; right: 10px; 
                width: 340px;
                min-height: 350px;
                max-height: 70vh;
                overflow-y: auto;
                background-color: rgba(255, 255, 255, 0.95); 
                padding: 12px; 
                border-radius: 5px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.2);
                z-index: 9999;
                font-family: Arial;
                font-size: 12px;
                transition: all 0.3s;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <h3 style="margin: 0; color: #1a73e8; font-size: 14px;">📊 Summary</h3>
            <button onclick="document.getElementById('summary-panel').style.display='none'" 
                    style="background: none; border: none; font-size: 18px; cursor: pointer; color: #666;">×</button>
        </div>
        
        <div style="margin-bottom: 10px; font-size: 11px;">
            <b style="color: #333;">Route:</b> {route_name}<br>
            <span id="summary-route-mode" style="color: #666;">Mode: ArcGIS route only</span>
        </div>

        <div id="summary-route-box" style="background:#f7fbff; border:1px solid #d6e4fb; border-left:4px solid #1a73e8; padding:8px; border-radius:4px; margin-bottom:10px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                <b style="color:#1a73e8; font-size:12px;">🛣️ Route overview</b>
                <span id="summary-route-provider" style="font-size:10px; font-weight:700; color:#1a73e8; background:#eef4ff; border:1px solid #d6e4fb; border-radius:999px; padding:2px 7px;">ArcGIS</span>
            </div>
            <table style="width:100%; border-collapse:collapse; font-size:11px; color:#555;">
                <tr>
                    <td style="padding:2px 0; width:43%;">Distance</td>
                    <td id="summary-route-distance" style="padding:2px 0; font-weight:600; color:#222; text-align:right;">{arcgis_metrics['distance_km']:.2f} km</td>
                </tr>
                <tr>
                    <td style="padding:2px 0;">Travel time</td>
                    <td id="summary-route-time" style="padding:2px 0; font-weight:600; color:#222; text-align:right;">{arcgis_metrics['travel_min']:.1f} min</td>
                </tr>
                <tr>
                    <td style="padding:2px 0;">Traffic delay</td>
                    <td id="summary-route-delay" style="padding:2px 0; font-weight:600; color:#222; text-align:right;">{arcgis_metrics['delay_min']:.1f} min</td>
                </tr>
            </table>
        </div>

        <div id="summary-mode-badge" style="display:inline-block; margin-bottom:8px; background:#eef4ff; color:#1a73e8; border:1px solid #d6e4fb; padding:3px 8px; border-radius:999px; font-size:10px; font-weight:600;">
            ArcGIS mode
        </div>
        
        <div style="background: #f0f7ff; padding: 8px; border-radius: 4px; margin-bottom: 10px; min-height: 120px;">
            <b id="summary-site-title" style="display:block; color: #1a73e8; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">🏆 Top site (ArcGIS): {top_name}</b>
            <span id="summary-site-score" style="display:block; color: #4CAF50; font-weight: bold; font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top:2px;">Route score: {top_score:.1f}/100</span>
            <div style="margin-top: 6px; font-size: 11px; color: #555;">
                <span id="summary-site-line1" style="display:block; min-height:16px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Accessibility: {top_access:.1f}/100</span>
                <span id="summary-site-line2" style="display:block; min-height:16px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Reliability: {top_reliab:.1f}/100</span>
                <span id="summary-site-line3" style="display:block; min-height:16px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Nearby road time: {top_avg_time:.0f}s</span>
            </div>
        </div>
        
        <div style="background: #fff8e1; padding: 8px; border-radius: 4px; margin-bottom: 10px; min-height: 100px;">
            <b id="summary-opt-title" style="display:block; color: #f57c00; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">⭐ Best route point: {opt_name}</b>
            <span id="summary-opt-score" style="display:block; color: #f57c00; font-weight: bold; font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top:2px;">Route score: {opt_score:.1f}/100</span>
            <div style="margin-top: 6px; font-size: 11px; color: #555;">
                <span id="summary-opt-line1" style="display:block; min-height:16px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Accessibility: {opt_access_score:.1f}/100</span>
                <span id="summary-opt-line2" style="display:block; min-height:16px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Traffic Flow: {opt_flow_score:.1f}/100</span>
            </div>
        </div>

        <div style="background:#f6f7fb; border:1px solid #e3e8f3; border-radius:4px; padding:8px; margin-bottom: 8px; color:#4b5563; font-size:11px; line-height:1.35;">
            <b>Top site</b> is the best segment; <b>Best route point</b> is the best exact point on the route, so they can differ.
        </div>
        
    </div>
    
        <button id="toggle-summary" onclick="document.getElementById('summary-panel').style.display='block'" 
            style="position: fixed; 
               bottom: 20px; right: 10px;
                   display: none;
                   background: white;
                   border: 1px solid #ccc;
                   border-radius: 5px;
                   padding: 8px 12px;
                   cursor: pointer;
                   box-shadow: 0 2px 5px rgba(0,0,0,0.2);
                   z-index: 9999;
                   font-size: 12px;">
        📊 Show
    </button>
    
    <script>
        document.querySelector('#summary-panel button').addEventListener('click', function() {{
            document.getElementById('summary-panel').style.display = 'none';
            document.getElementById('toggle-summary').style.display = 'block';
        }});
        document.getElementById('toggle-summary').addEventListener('click', function() {{
            this.style.display = 'none';
        }});

        (function() {{
            var checkbox = document.getElementById('tomtom-enhancement-toggle');
            var modeBadge = document.getElementById('summary-mode-badge');
            var routeMode = document.getElementById('summary-route-mode');
            var routeBox = document.getElementById('summary-route-box');
            var routeProvider = document.getElementById('summary-route-provider');
            var routeDistance = document.getElementById('summary-route-distance');
            var routeTime = document.getElementById('summary-route-time');
            var routeDelay = document.getElementById('summary-route-delay');
            var sourcesProviderLabel = document.getElementById('sources-provider-label');
            var sourcesProviderName = document.getElementById('sources-provider-name');
            var sourceRouteTime = document.getElementById('source-cell-route_geometry_and_segment_times');
            var sourceSiteScores = document.getElementById('source-cell-multi_site_scoring');
            var sourceBestPoint = document.getElementById('source-cell-optimal_location_scoring');
            var sourceAccessZones = document.getElementById('source-cell-accessibility_zones');
            var sourceBusiness = document.getElementById('source-cell-retail_business_scoring');
            var sourcePoi = document.getElementById('source-cell-map_poi_visualization');
            var sourceMapBasemap = document.getElementById('source-cell-map_basemap_sources');
            var sourceMapBasemapRow = document.getElementById('source-row-map_basemap_sources');
            var sourceFrc = document.getElementById('source-cell-frc_classification');
            var sourceFrcRow = document.getElementById('source-row-frc_classification');
            var sourceAadt = document.getElementById('source-cell-aadt_source');
            var sourceAadtRow = document.getElementById('source-row-aadt_source');
            var sourceParkingApis = document.getElementById('source-cell-parking_api_sources');
            var sourceParkingApisRow = document.getElementById('source-row-parking_api_sources');
            var siteTitle = document.getElementById('summary-site-title');
            var siteScore = document.getElementById('summary-site-score');
            var siteLine1 = document.getElementById('summary-site-line1');
            var siteLine2 = document.getElementById('summary-site-line2');
            var siteLine3 = document.getElementById('summary-site-line3');
            var optTitle = document.getElementById('summary-opt-title');
            var optScore = document.getElementById('summary-opt-score');
            var optLine1 = document.getElementById('summary-opt-line1');
            var optLine2 = document.getElementById('summary-opt-line2');

            var arcgisSiteTitle = {json.dumps(f"🏆 Top site (ArcGIS): {top_name}")};
            var arcgisSiteScore = {json.dumps(f"Route score: {top_score:.1f}/100")};
            var arcgisLine1 = {json.dumps(f"Accessibility: {top_access:.1f}/100")};
            var arcgisLine2 = {json.dumps(f"Reliability: {top_reliab:.1f}/100")};
            var arcgisLine3 = {json.dumps(f"Nearby road time: {top_avg_time:.0f}s")};

            var enhancedSiteTitle = {json.dumps(f"🏆 Top site (TomTom #1): {enhanced_name}")};
            var enhancedSiteScore = {json.dumps(f"Business score: {enhanced_score:.1f}/100")};
            var enhancedLine1 = {json.dumps(enhanced_line_1)};
            var enhancedLine2 = {json.dumps(enhanced_line_2)};
            var enhancedLine3 = {json.dumps(f"Nearby road time: {enhanced_avg_time:.0f}s")};

            var aadtSiteTitle = {json.dumps(f"🏆 Top site (AADT): {aadt_name}")};
            var aadtSiteScore = {json.dumps(f"AADT score: {aadt_score:.1f}/100")};
            var aadtLine1 = {json.dumps(f"Accessibility: {aadt_access:.1f}/100")};
            var aadtLine2 = {json.dumps(f"AADT exposure: {aadt_exposure:.1f}/100 ({aadt_avg:,.0f} veh/day)")};
            var aadtLine3 = {json.dumps(f"Reliability: {aadt_reliab:.1f}/100")};

            // Store TomTom (non-AADT) values for restoring when AADT is toggled off
            var savedTomTomTitle = enhancedSiteTitle;
            var savedTomTomScore = enhancedSiteScore;
            var savedTomTomLine1 = enhancedLine1;
            var savedTomTomLine2 = enhancedLine2;
            var savedTomTomLine3 = enhancedLine3;

            var arcgisOptTitle = {json.dumps(f"⭐ Best route point: {opt_name}")};
            var arcgisOptScore = {json.dumps(f"Route score: {opt_score:.1f}/100")};
            var arcgisOptLine1 = {json.dumps(f"Accessibility: {opt_access_score:.1f}/100")};
            var arcgisOptLine2 = {json.dumps(f"Traffic Flow: {opt_flow_score:.1f}/100")};

            var enhancedOptTitle = {json.dumps(f"⭐ Best route point: {enhanced_opt_name}")};
            var enhancedOptScore = {json.dumps(f"Business score: {enhanced_opt_score:.1f}/100")};
            var enhancedOptLine1 = {json.dumps("Accessibility: Based on TomTom POI data")};
            var enhancedOptLine2 = {json.dumps("Traffic Flow: Based on TomTom traffic data")};

            var sourceArcgisProvider = {json.dumps("ArcGIS")};
            var sourceTomTomProvider = {json.dumps("ArcGIS + TomTom")};

            var sourceArcgisRouteTime = {json.dumps("ArcGIS Route API")};
            var sourceArcgisSiteScores = {json.dumps("ArcGIS Route API")};
            var sourceArcgisBestPoint = {json.dumps("ArcGIS Route API")};
            var sourceArcgisAccessZones = {json.dumps("ArcGIS Route API")};
            var sourceArcgisBusiness = {json.dumps("ArcGIS Route API")};
            var sourceArcgisPoi = {json.dumps("ArcGIS Geocoding API")};
            var sourceArcgisMapBasemap = {json.dumps("ArcGIS Basemap Tile Services API")};
            var sourceArcgisFrc = {json.dumps("Speed-inferred from ArcGIS Route API")};
            var sourceArcgisParkingApis = {json.dumps("ArcGIS Places API + ArcGIS Geocoding API")};
            var sourceTomTomMapBasemap = {json.dumps("TomTom Map Tile API")};
            var sourceTomTomParkingApis = {json.dumps("ArcGIS Places API + ArcGIS Geocoding API + TomTom Category Search API")};
            var sourceTomTomRouteTime = {json.dumps("ArcGIS Route API + TomTom Traffic Stats API")};
            var sourceTomTomSiteScores = {json.dumps("ArcGIS Route API + TomTom Traffic Stats API")};
            var sourceTomTomBestPoint = {json.dumps("ArcGIS Route API + TomTom Traffic Stats API")};
            var sourceTomTomAccessZones = {json.dumps("ArcGIS Route API")};
            var sourceTomTomBusiness = {json.dumps("ArcGIS Route API + TomTom Traffic Stats API")};
            var sourceTomTomPoi = {json.dumps("ArcGIS Geocoding API + TomTom Places API")};
            var sourceTomTomFrc = {json.dumps("TomTom Traffic Stats API")};

            var arcgisRouteProvider = {json.dumps("ArcGIS")};
            var arcgisRouteDistance = {json.dumps(f"{arcgis_metrics['distance_km']:.2f} km")};
            var arcgisRouteTime = {json.dumps(f"{arcgis_metrics['travel_min']:.1f} min")};
            var arcgisRouteDelay = {json.dumps(f"{arcgis_metrics['delay_min']:.1f} min")};

            var tomtomRouteProvider = {json.dumps("TomTom")};
            var tomtomRouteDistance = {json.dumps(f"{tomtom_metrics['distance_km']:.2f} km")};
            var tomtomRouteTime = {json.dumps(f"{tomtom_metrics['travel_min']:.1f} min")};
            var tomtomRouteDelay = {json.dumps(f"{tomtom_metrics['delay_min']:.1f} min")};

            function escapeHtml(value) {{
                return String(value || '')
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#39;');
            }}

            function renderSourceCell(cell, textValue, tomtomMode) {{
                if (!cell) return;
                if (tomtomMode) {{
                    var safe = escapeHtml(textValue || '');
                    var chipStyle = 'display:inline-block; margin:0 2px; padding:1px 8px; border-radius:999px; border:1px solid #bfdbfe; background:#eff6ff; color:#1d4ed8; font-weight:600;';
                    safe = safe.replace(/TomTom Places API/g, '<span style="' + chipStyle + '">TomTom Places API</span>');
                    safe = safe.replace(/TomTom Traffic API/g, '<span style="' + chipStyle + '">TomTom Traffic API</span>');
                    safe = safe.replace(/TomTom Traffic Stats API/g, '<span style="' + chipStyle + '">TomTom Traffic Stats API</span>');
                    safe = safe.replace(/TomTom Category Search API/g, '<span style="' + chipStyle + '">TomTom Category Search API</span>');
                    safe = safe.replace(/TomTom Map Tile API/g, '<span style="' + chipStyle + '">TomTom Map Tile API</span>');
                    safe = safe.replace(/TomTom Traffic Volume Sample/g, '<span style="' + chipStyle + '">TomTom Traffic Volume Sample</span>');
                    safe = safe.replace(/TomTom traffic volume sample/g, '<span style="' + chipStyle + '">TomTom Traffic Volume Sample</span>');
                    cell.innerHTML = safe;
                    cell.style.color = '#4d4d4d';
                }} else {{
                    cell.textContent = String(textValue || '');
                    cell.style.color = '#4d4d4d';
                }}
            }}

            function updateSourcesMode(enabled) {{
                if (!sourcesProviderName) return;
                var modeCheckbox = document.getElementById('tomtom-enhancement-toggle');
                // Always derive mode from the current toggle state to avoid stale source labels.
                enabled = !!(modeCheckbox && modeCheckbox.checked);

                if (enabled) {{
                    if (sourcesProviderLabel) sourcesProviderLabel.textContent = 'Provider';
                    sourcesProviderName.textContent = sourceTomTomProvider;
                    sourcesProviderName.style.color = '#1a73e8';
                    if (sourceParkingApisRow) sourceParkingApisRow.style.display = '';
                    renderSourceCell(sourceParkingApis, sourceTomTomParkingApis, true);
                    renderSourceCell(sourceRouteTime, sourceTomTomRouteTime, true);
                    renderSourceCell(sourceSiteScores, sourceTomTomSiteScores, true);
                    renderSourceCell(sourceBestPoint, sourceTomTomBestPoint, true);
                    renderSourceCell(sourceAccessZones, sourceTomTomAccessZones, true);
                    renderSourceCell(sourceBusiness, sourceTomTomBusiness, true);
                    renderSourceCell(sourcePoi, sourceTomTomPoi, true);
                    if (sourceMapBasemapRow) sourceMapBasemapRow.style.display = '';
                    renderSourceCell(sourceMapBasemap, sourceTomTomMapBasemap, true);
                    if (sourceFrcRow) sourceFrcRow.style.display = '';
                    renderSourceCell(sourceFrc, sourceTomTomFrc, true);
                    // AADT row only visible if AADT toggle is also checked
                    var aadtCb = document.getElementById('aadt-toggle');
                    var aadtOn = aadtCb && aadtCb.checked;
                    if (sourceAadtRow) sourceAadtRow.style.display = aadtOn ? '' : 'none';
                    if (aadtOn) {{
                        renderSourceCell(sourceAadt, 'TomTom Traffic Volume Sample', true);
                        renderSourceCell(sourceSiteScores, 'ArcGIS Route API + TomTom Traffic Stats API + TomTom Traffic Volume Sample', true);
                    }}
                }} else {{
                    if (sourcesProviderLabel) sourcesProviderLabel.textContent = 'Provider';
                    sourcesProviderName.textContent = sourceArcgisProvider;
                    sourcesProviderName.style.color = '#1a73e8';
                    if (sourceParkingApisRow) sourceParkingApisRow.style.display = '';
                    renderSourceCell(sourceParkingApis, sourceArcgisParkingApis, false);
                    renderSourceCell(sourceRouteTime, sourceArcgisRouteTime, false);
                    renderSourceCell(sourceSiteScores, sourceArcgisSiteScores, false);
                    renderSourceCell(sourceBestPoint, sourceArcgisBestPoint, false);
                    renderSourceCell(sourceAccessZones, sourceArcgisAccessZones, false);
                    renderSourceCell(sourceBusiness, sourceArcgisBusiness, false);
                    renderSourceCell(sourcePoi, sourceArcgisPoi, false);
                    if (sourceMapBasemapRow) sourceMapBasemapRow.style.display = '';
                    renderSourceCell(sourceMapBasemap, sourceArcgisMapBasemap, false);
                    if (sourceFrcRow) sourceFrcRow.style.display = '';
                    if (sourceAadtRow) sourceAadtRow.style.display = 'none';
                }}
            }}

            function updateSummaryMode(enabled) {{
                if (!modeBadge || !routeMode || !routeProvider || !routeDistance || !routeTime || !routeDelay || !siteTitle || !siteScore || !siteLine1 || !siteLine2 || !siteLine3 || !optTitle || !optScore || !optLine1 || !optLine2) return;

                if (enabled) {{
                    modeBadge.textContent = 'TomTom mode';
                    modeBadge.style.background = '#f3e8ff';
                    modeBadge.style.color = '#7b1fa2';
                    modeBadge.style.borderColor = '#e2cfff';
                    routeMode.textContent = 'Mode: ArcGIS + TomTom';
                    routeProvider.textContent = tomtomRouteProvider;
                    routeDistance.textContent = tomtomRouteDistance;
                    routeTime.textContent = tomtomRouteTime;
                    routeDelay.textContent = tomtomRouteDelay;
                    routeProvider.style.color = '#7b1fa2';
                    routeProvider.style.background = '#f3e8ff';
                    routeProvider.style.borderColor = '#e2cfff';
                    if (routeBox) {{
                        routeBox.style.background = '#faf5ff';
                        routeBox.style.borderColor = '#e2cfff';
                        routeBox.style.borderLeftColor = '#7b1fa2';
                    }}

                    siteTitle.textContent = enhancedSiteTitle;
                    siteScore.textContent = enhancedSiteScore;
                    siteLine1.textContent = enhancedLine1;
                    siteLine2.textContent = enhancedLine2;
                    siteLine3.textContent = enhancedLine3;

                    optTitle.textContent = enhancedOptTitle;
                    optScore.textContent = enhancedOptScore;
                    optLine1.textContent = enhancedOptLine1;
                    optLine2.textContent = enhancedOptLine2;
                }} else {{
                    modeBadge.textContent = 'ArcGIS mode';
                    modeBadge.style.background = '#eef4ff';
                    modeBadge.style.color = '#1a73e8';
                    modeBadge.style.borderColor = '#d6e4fb';
                    routeMode.textContent = 'Mode: ArcGIS route only';
                    routeProvider.textContent = arcgisRouteProvider;
                    routeDistance.textContent = arcgisRouteDistance;
                    routeTime.textContent = arcgisRouteTime;
                    routeDelay.textContent = arcgisRouteDelay;
                    routeProvider.style.color = '#1a73e8';
                    routeProvider.style.background = '#eef4ff';
                    routeProvider.style.borderColor = '#d6e4fb';
                    if (routeBox) {{
                        routeBox.style.background = '#f7fbff';
                        routeBox.style.borderColor = '#d6e4fb';
                        routeBox.style.borderLeftColor = '#1a73e8';
                    }}

                    siteTitle.textContent = arcgisSiteTitle;
                    siteScore.textContent = arcgisSiteScore;
                    siteLine1.textContent = arcgisLine1;
                    siteLine2.textContent = arcgisLine2;
                    siteLine3.textContent = arcgisLine3;

                    optTitle.textContent = arcgisOptTitle;
                    optScore.textContent = arcgisOptScore;
                    optLine1.textContent = arcgisOptLine1;
                    optLine2.textContent = arcgisOptLine2;
                }}

                updateSourcesMode(enabled);
            }}

            function updateSummaryForAadt(aadtEnabled) {{
                if (!siteTitle || !siteScore || !siteLine1 || !siteLine2 || !siteLine3 || !modeBadge) return;
                if (aadtEnabled) {{
                    modeBadge.textContent = 'TomTom + AADT mode';
                    siteTitle.textContent = aadtSiteTitle;
                    siteScore.textContent = aadtSiteScore;
                    siteLine1.textContent = aadtLine1;
                    siteLine2.textContent = aadtLine2;
                    siteLine3.textContent = aadtLine3;
                }} else {{
                    modeBadge.textContent = 'TomTom mode';
                    siteTitle.textContent = savedTomTomTitle;
                    siteScore.textContent = savedTomTomScore;
                    siteLine1.textContent = savedTomTomLine1;
                    siteLine2.textContent = savedTomTomLine2;
                    siteLine3.textContent = savedTomTomLine3;
                }}
            }}

            function updateSourcesForAadt(aadtEnabled) {{
                if (!sourceSiteScores) return;
                if (aadtEnabled) {{
                    renderSourceCell(sourceSiteScores, 'ArcGIS Route API + TomTom Traffic Stats API + TomTom Traffic Volume Sample', true);
                    if (sourceAadtRow) sourceAadtRow.style.display = '';
                    renderSourceCell(sourceAadt, 'TomTom Traffic Volume Sample', true);
                }} else {{
                    // Recompute all sources from the current toggle state so ArcGIS mode
                    // always restores ArcGIS-only API labels.
                    var checkbox = document.getElementById('tomtom-enhancement-toggle');
                    var isTomTomMode = checkbox && checkbox.checked;
                    updateSourcesMode(isTomTomMode);
                }}
            }}
            window.updateSourcesForAadt = updateSourcesForAadt;
            window.updateSummaryForAadt = updateSummaryForAadt;

            if (checkbox) {{
                checkbox.checked = false;
                checkbox.addEventListener('change', function() {{ updateSummaryMode(this.checked); }});
            }}
            updateSummaryMode(false);
        }})();
    </script>
    '''
    m.get_root().html.add_child(folium.Element(summary_html))
    
    return m


def main():
    """Generate and save the site selection map"""
    
    # Accept command line arguments or use defaults (relative to project root)
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    results_file = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_project_root, "latest_site_selection.json")
    travel_file = sys.argv[2] if len(sys.argv) > 2 else os.path.join(_project_root, "latest_travel_time.json")
    
    print("Loading site selection results...")
    results = load_results(results_file)
    
    print("Loading travel time data for route geometry...")
    travel_data = load_travel_time_data(travel_file)
    
    print("Creating interactive map...")
    try:
        site_map = create_site_selection_map(results, travel_data)
    except Exception as e:
        print(f"\n❌ Map generation failed: {e}")
        print("Tip: set STRICT_TOMTOM_ERRORS=0 for best-effort mode.")
        raise
    
    # Save to HTML file in project root
    output_file = os.path.join(_project_root, "site_selection_map.html")
    site_map.save(output_file)
    
    print(f"\n✅ Map saved to: {output_file}")
    
    # Serve via local HTTP server and open in browser
    import http.server
    import threading
    file_path = os.path.abspath(output_file)
    serve_dir = os.path.dirname(file_path)
    serve_file = os.path.basename(file_path)

    handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(
        *args, directory=serve_dir, **kwargs
    )
    server = http.server.HTTPServer(("", 8080), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    url = f"http://localhost:8080/{serve_file}"
    webbrowser.open(url)
    print(f"🌐 Serving map at {url}")
    print("   Press Ctrl+C to stop the server.")
    try:
        server_thread.join()
    except KeyboardInterrupt:
        server.shutdown()
        print("\nServer stopped.")
    
    print("\nMap Features:")
    print("  • Toggle layers on/off using the control panel")
    print("  • Click markers for detailed information")
    print("  • Zoom and pan to explore the route")


if __name__ == "__main__":
    main()
