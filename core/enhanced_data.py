"""
Enhanced Location Data Integration
ArcGIS-first where supported, TomTom fallback where ArcGIS lacks direct support.
"""

import os
import requests
import json
import math
import time
from typing import Dict, List, Any
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# API keys loaded from environment variable
ARCGIS_API_KEY = os.getenv("ARCGIS_API_KEY", "MOCK_ARCGIS_API_KEY")
TOMTOM_API_KEY = os.getenv("TOMTOM_PLACES_API_KEY")

# API Endpoints
SEARCH_POI_URL = "https://api.tomtom.com/search/2/poiSearch"
NEARBY_SEARCH_URL = "https://api.tomtom.com/search/2/nearbySearch"
CATEGORY_SEARCH_URL = "https://api.tomtom.com/search/2/categorySearch"
ARCGIS_FIND_ADDRESS_CANDIDATES_URL = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
ARCGIS_PLACES_NEAR_POINT_URL = "https://places-api.arcgis.com/arcgis/rest/services/places-service/v1/places/near-point"

_MIN_TOMTOM_REQUEST_INTERVAL_SEC = 0.15
_MAX_TOMTOM_RETRIES = 3
_MAX_TOMTOM_CATEGORY_LIMIT = 100
_MAX_TOMTOM_RADIUS_METERS = 10000
_last_tomtom_request_time = 0.0
_category_poi_cache: Dict[tuple, List[Dict[str, Any]]] = {}
_tomtom_api_health = {
    "request_attempts": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "rate_limited_responses": 0,
    "retried_requests": 0,
    "exceptions": 0,
    "cache_hits": 0,
    "status_counts": {},
}


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in kilometers"""
    R = 6371  # Earth's radius in km
    
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    
    return R * c


# Comprehensive POI Category Definitions for Site Selection
POI_CATEGORIES = {
    # High-traffic generators (foot traffic indicators)
    "FOOT_TRAFFIC": [
        "RESTAURANT", "CAFE_PUB", "SHOPPING_CENTER", "DEPARTMENT_STORE",
        "PUBLIC_TRANSPORT_STOP", "RAILWAY_STATION", "IMPORTANT_TOURIST_ATTRACTION",
        "MARKET", "ENTERTAINMENT", "CINEMA", "THEATER"
    ],
    # Essential amenities (convenience factors)
    "AMENITIES": [
        "CASH_DISPENSER", "BANK", "PHARMACY", "PETROL_STATION",
        "OPEN_PARKING_AREA", "PARKING_GARAGE", "POST_OFFICE"
    ],
    # Retail indicators (commercial viability)
    "RETAIL": [
        "SHOP", "SHOPPING_CENTER", "DEPARTMENT_STORE", "MARKET"
    ],
    # Restaurant/Food indicators
    "FOOD_SERVICE": [
        "RESTAURANT", "CAFE_PUB", "RESTAURANT_AREA"
    ],
    # Business district indicators
    "BUSINESS": [
        "BUSINESS_PARK", "COMMERCIAL_BUILDING", "COMPANY", "BANK",
        "OFFICE_BUILDING"
    ],
    # Accessibility indicators
    "TRANSIT": [
        "PUBLIC_TRANSPORT_STOP", "RAILWAY_STATION", "BUS_STATION",
        "METRO_STATION", "TAXI_STAND"
    ]
}

VALID_CATEGORY_CODES = {
    code
    for codes in POI_CATEGORIES.values()
    for code in codes
}


def _sleep_for_rate_limit():
    global _last_tomtom_request_time
    now = time.time()
    elapsed = now - _last_tomtom_request_time
    if elapsed < _MIN_TOMTOM_REQUEST_INTERVAL_SEC:
        time.sleep(_MIN_TOMTOM_REQUEST_INTERVAL_SEC - elapsed)
    _last_tomtom_request_time = time.time()


def _record_status(status_code: int):
    status_key = str(status_code)
    status_counts = _tomtom_api_health["status_counts"]
    status_counts[status_key] = status_counts.get(status_key, 0) + 1


def reset_tomtom_api_health():
    _tomtom_api_health["request_attempts"] = 0
    _tomtom_api_health["successful_requests"] = 0
    _tomtom_api_health["failed_requests"] = 0
    _tomtom_api_health["rate_limited_responses"] = 0
    _tomtom_api_health["retried_requests"] = 0
    _tomtom_api_health["exceptions"] = 0
    _tomtom_api_health["cache_hits"] = 0
    _tomtom_api_health["status_counts"] = {}


def get_tomtom_api_health_report() -> Dict[str, Any]:
    return {
        "request_attempts": _tomtom_api_health["request_attempts"],
        "successful_requests": _tomtom_api_health["successful_requests"],
        "failed_requests": _tomtom_api_health["failed_requests"],
        "rate_limited_responses": _tomtom_api_health["rate_limited_responses"],
        "retried_requests": _tomtom_api_health["retried_requests"],
        "exceptions": _tomtom_api_health["exceptions"],
        "cache_hits": _tomtom_api_health["cache_hits"],
        "status_counts": dict(_tomtom_api_health["status_counts"]),
    }


def _tomtom_get_with_retry(url: str, params: Dict[str, Any], timeout: int = 10):
    last_response = None
    for attempt in range(_MAX_TOMTOM_RETRIES):
        _sleep_for_rate_limit()
        try:
            response = requests.get(url, params=params, timeout=timeout)
        except requests.RequestException:
            _tomtom_api_health["request_attempts"] += 1
            _tomtom_api_health["exceptions"] += 1
            _tomtom_api_health["failed_requests"] += 1
            if attempt < _MAX_TOMTOM_RETRIES - 1:
                _tomtom_api_health["retried_requests"] += 1
                time.sleep(0.5 * (2 ** attempt))
                continue
            return None

        _tomtom_api_health["request_attempts"] += 1
        _record_status(response.status_code)
        last_response = response

        if response.status_code == 200:
            _tomtom_api_health["successful_requests"] += 1
            return response

        if response.status_code == 429:
            _tomtom_api_health["failed_requests"] += 1
            _tomtom_api_health["rate_limited_responses"] += 1
            retry_after_header = response.headers.get("Retry-After")
            if retry_after_header and retry_after_header.isdigit():
                wait_seconds = float(retry_after_header)
            else:
                wait_seconds = 0.5 * (2 ** attempt)
            if attempt < _MAX_TOMTOM_RETRIES - 1:
                _tomtom_api_health["retried_requests"] += 1
            time.sleep(wait_seconds)
            continue

        _tomtom_api_health["failed_requests"] += 1
        return response

    return last_response


def search_nearby_pois(lat: float, lon: float, radius: int = 500, 
                       categories: List[str] = None) -> List[Dict[str, Any]]:
    """
    Search for nearby POIs (Points of Interest) around a location.
    
    Args:
        lat: Latitude
        lon: Longitude
        radius: Search radius in meters (default 500m)
        categories: List of POI categories to search for
    
    Returns:
        List of nearby POIs with details
    """
    if categories is None:
        categories = ["restaurant", "shopping", "parking", "atm", "hospital", "school"]

    if not TOMTOM_API_KEY:
        return []

    safe_radius = max(1, min(int(radius), _MAX_TOMTOM_RADIUS_METERS))
    normalized_categories = [str(category).strip() for category in categories if str(category).strip()]

    # TomTom nearbySearch expects numeric category IDs in categorySet.
    # If text categories are passed (e.g., "parking"), switch to poiSearch query fallback.
    numeric_category_ids = [cat for cat in normalized_categories if cat.isdigit()]
    text_queries = [cat for cat in normalized_categories if not cat.isdigit()]
    
    try:
        collected_pois: List[Dict[str, Any]] = []
        seen_keys = set()

        if numeric_category_ids:
            url = f"{NEARBY_SEARCH_URL}/.json"
            nearby_params = {
                "key": TOMTOM_API_KEY,
                "lat": lat,
                "lon": lon,
                "radius": safe_radius,
                "limit": _MAX_TOMTOM_CATEGORY_LIMIT,
                "categorySet": ",".join(numeric_category_ids)
            }
            response = _tomtom_get_with_retry(url, params=nearby_params, timeout=10)
            if response is not None and response.status_code == 200:
                data = response.json()
                for poi in data.get("results", []):
                    name = poi.get("poi", {}).get("name", "Unknown")
                    poi_category = poi.get("poi", {}).get("categories", ["Unknown"])[0] if poi.get("poi", {}).get("categories") else "Unknown"
                    latitude = poi.get("position", {}).get("lat", 0)
                    longitude = poi.get("position", {}).get("lon", 0)
                    key = (round(latitude, 6), round(longitude, 6), str(name).strip().lower())
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    collected_pois.append({
                        "name": name,
                        "category": poi_category,
                        "address": poi.get("address", {}).get("freeformAddress", ""),
                        "distance": poi.get("dist", 0),
                        "latitude": latitude,
                        "longitude": longitude,
                    })

        for query in text_queries:
            url = f"{SEARCH_POI_URL}/{query}.json"
            query_params = {
                "key": TOMTOM_API_KEY,
                "lat": lat,
                "lon": lon,
                "radius": safe_radius,
                "limit": _MAX_TOMTOM_CATEGORY_LIMIT,
            }
            response = _tomtom_get_with_retry(url, params=query_params, timeout=10)
            if response is None or response.status_code != 200:
                continue

            data = response.json()
            for poi in data.get("results", []):
                name = poi.get("poi", {}).get("name", "Unknown")
                poi_category = poi.get("poi", {}).get("categories", [query])[0] if poi.get("poi", {}).get("categories") else query
                latitude = poi.get("position", {}).get("lat", 0)
                longitude = poi.get("position", {}).get("lon", 0)
                key = (round(latitude, 6), round(longitude, 6), str(name).strip().lower())
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                collected_pois.append({
                    "name": name,
                    "category": poi_category,
                    "address": poi.get("address", {}).get("freeformAddress", ""),
                    "distance": poi.get("dist", 0),
                    "latitude": latitude,
                    "longitude": longitude,
                })

        return collected_pois
    except Exception as e:
        print(f"Error searching POIs: {e}")
        return []


def search_category_pois(lat: float, lon: float, category: str, radius: int = 500, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Search for POIs by category code around a location.
    
    Args:
        lat: Latitude
        lon: Longitude
        category: POI category code (e.g., 'RESTAURANT', 'SHOPPING_CENTER')
        radius: Search radius in meters
        limit: Maximum number of results
    
    Returns:
        List of POIs matching the category
    """
    normalized_category = (category or "").strip().upper()
    if normalized_category not in VALID_CATEGORY_CODES:
        return []

    safe_limit = max(1, min(int(limit), _MAX_TOMTOM_CATEGORY_LIMIT))
    safe_radius = max(1, min(int(radius), _MAX_TOMTOM_RADIUS_METERS))

    cache_key = (round(lat, 6), round(lon, 6), normalized_category, safe_radius, safe_limit)
    cached = _category_poi_cache.get(cache_key)
    if cached is not None:
        _tomtom_api_health["cache_hits"] += 1
        return cached

    url = f"{CATEGORY_SEARCH_URL}/{normalized_category}.json"

    if not TOMTOM_API_KEY:
        return []
    
    params = {
        "key": TOMTOM_API_KEY,
        "lat": lat,
        "lon": lon,
        "radius": safe_radius,
        "limit": safe_limit
    }
    
    try:
        response = _tomtom_get_with_retry(url, params=params, timeout=10)
        if response is None:
            return []

        if response.status_code == 200:
            data = response.json()
            results = data.get("results", [])
            
            pois = []
            for poi in results:
                pois.append({
                    "name": poi.get("poi", {}).get("name", "Unknown"),
                    "category": normalized_category,
                    "address": poi.get("address", {}).get("freeformAddress", ""),
                    "distance": poi.get("dist", 0),
                    "latitude": poi.get("position", {}).get("lat", 0),
                    "longitude": poi.get("position", {}).get("lon", 0)
                })

            _category_poi_cache[cache_key] = pois
            return pois
        else:
            if response.status_code not in (400, 429):
                print(f"Category search API error ({normalized_category}): {response.status_code}")
            _category_poi_cache[cache_key] = []
            return []
    except Exception as e:
        print(f"Error searching category {category}: {e}")
        return []


def search_specific_poi_arcgis(lat: float, lon: float, query: str, radius: int = 1000) -> int:
    """
    ArcGIS geocoding-based POI search near a location.
    Returns candidate count, or 0 on error.
    """
    if not ARCGIS_API_KEY:
        return 0

    params = {
        "f": "json",
        "token": ARCGIS_API_KEY,
        "singleLine": query,
        "location": f"{lon},{lat}",
        "distance": radius,
        "maxLocations": 100,
        "category": "POI",
        "outFields": "*"
    }

    try:
        response = requests.get(ARCGIS_FIND_ADDRESS_CANDIDATES_URL, params=params, timeout=10)
        if response.status_code != 200:
            return 0
        data = response.json()
        if "error" in data:
            return 0
        return len(data.get("candidates", []))
    except Exception:
        return 0


def search_specific_poi_tomtom(lat: float, lon: float, query: str, radius: int = 1000) -> int:
    """TomTom POI search fallback for query-based counts."""
    if not TOMTOM_API_KEY:
        return 0

    url = f"{SEARCH_POI_URL}/{query}.json"
    
    params = {
        "key": TOMTOM_API_KEY,
        "lat": lat,
        "lon": lon,
        "radius": radius,
        "limit": 100
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return len(data.get("results", []))
        else:
            return 0
    except Exception as e:
        print(f"Error searching specific POI: {e}")
        return 0


def search_specific_poi(lat: float, lon: float, query: str, radius: int = 1000) -> int:
    """
    Search for a specific POI type with ArcGIS-first strategy.
    Falls back to TomTom when ArcGIS does not return data.
    """
    arcgis_count = search_specific_poi_arcgis(lat, lon, query, radius)
    if arcgis_count > 0:
        return arcgis_count
    return search_specific_poi_tomtom(lat, lon, query, radius)


def search_pois_arcgis(lat: float, lon: float, query: str, radius: int = 1000, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Search for POIs using ArcGIS Geocoding API and return full details.
    
    Args:
        lat: Latitude
        lon: Longitude
        query: Search query (e.g., 'parking', 'restaurant')
        radius: Search radius in meters
        limit: Maximum number of results
    
    Returns:
        List of POI dictionaries with name, latitude, longitude, category, address
    """
    if not ARCGIS_API_KEY or ARCGIS_API_KEY == "MOCK_ARCGIS_API_KEY":
        return []

    # Calculate search extent (bounding box) in degrees
    # Approximately 111,000 meters per degree latitude
    # Longitude varies by latitude, but use conservative approximation
    radius_degrees = (radius / 111000) * 1.5  # Add 50% margin for search
    
    params = {
        "f": "json",
        "token": ARCGIS_API_KEY,
        "singleLine": query,
        "location": f"{lon},{lat}",
        "searchExtent": f"{lon-radius_degrees},{lat-radius_degrees},{lon+radius_degrees},{lat+radius_degrees}",
        "maxLocations": min(limit * 2, 100),  # Request more to account for filtering
        "category": "POI",
        "outFields": "PlaceName,Place_addr,Type"
    }

    try:
        response = requests.get(ARCGIS_FIND_ADDRESS_CANDIDATES_URL, params=params, timeout=10)
        if response.status_code != 200:
            return []
        
        data = response.json()
        if "error" in data:
            return []
        
        candidates = data.get("candidates", [])
        pois = []
        
        for candidate in candidates:
            location = candidate.get("location", {})
            attributes = candidate.get("attributes", {})
            
            poi_lat = location.get("y", 0)
            poi_lon = location.get("x", 0)
            
            # Calculate distance from search center
            distance = haversine_distance(lat, lon, poi_lat, poi_lon) * 1000  # Convert km to meters
            
            # Only include POIs within the specified radius
            if distance <= radius:
                poi = {
                    "name": attributes.get("PlaceName") or candidate.get("address", "Unknown"),
                    "latitude": poi_lat,
                    "longitude": poi_lon,
                    "category": attributes.get("Type", query.upper()),
                    "address": attributes.get("Place_addr", ""),
                    "distance": distance,
                }
                pois.append(poi)
        
        # Sort by distance and limit results
        pois.sort(key=lambda p: p.get("distance", 999999))
        return pois[:limit]
    except Exception as e:
        print(f"Error searching ArcGIS POIs: {e}")
        return []


def _normalize_arcgis_places_results(data: Dict[str, Any], fallback_query: str) -> List[Dict[str, Any]]:
    """Normalize ArcGIS Places near-point response to the common POI shape."""
    raw_places = data.get("places") or data.get("results") or []
    normalized = []

    for place in raw_places:
        location = place.get("location") or place.get("geometry") or {}
        poi_lat = (
            location.get("y")
            or location.get("latitude")
            or location.get("lat")
            or 0
        )
        poi_lon = (
            location.get("x")
            or location.get("longitude")
            or location.get("lon")
            or 0
        )

        categories = place.get("categories") or []
        category_label = fallback_query.upper()
        if categories and isinstance(categories, list):
            first_category = categories[0]
            if isinstance(first_category, dict):
                category_label = first_category.get("label") or first_category.get("name") or category_label
            elif isinstance(first_category, str):
                category_label = first_category

        address_obj = place.get("address")
        if isinstance(address_obj, dict):
            address = (
                address_obj.get("formattedAddress")
                or address_obj.get("streetAddress")
                or ""
            )
        else:
            address = address_obj or ""

        normalized.append({
            "name": place.get("name") or place.get("title") or "Unknown",
            "latitude": poi_lat,
            "longitude": poi_lon,
            "category": category_label,
            "address": address,
            "distance": place.get("distance") or place.get("distanceMeters") or 0,
        })

    return normalized


def search_pois_arcgis_places_first(lat: float, lon: float, query: str, radius: int = 1000, limit: int = 100) -> List[Dict[str, Any]]:
    """
    ArcGIS Places-first POI search with fallback to ArcGIS geocoding.
    This is especially useful for richer parking retrieval.
    """
    if not ARCGIS_API_KEY or ARCGIS_API_KEY == "MOCK_ARCGIS_API_KEY":
        return []

    safe_limit = max(1, min(int(limit), 100))
    safe_radius = max(1, min(int(radius), 20000))

    params = {
        "f": "json",
        "x": lon,
        "y": lat,
        "radius": safe_radius,
        "searchText": query,
        "pageSize": min(safe_limit, 20),
    }

    # Try Places API with bearer auth first, then token query auth.
    auth_attempts = [
        {"headers": {"Authorization": f"Bearer {ARCGIS_API_KEY}"}, "extra_params": {}},
        {"headers": {}, "extra_params": {"token": ARCGIS_API_KEY}},
    ]

    for auth in auth_attempts:
        try:
            response = requests.get(
                ARCGIS_PLACES_NEAR_POINT_URL,
                params={**params, **auth["extra_params"]},
                headers=auth["headers"],
                timeout=10,
            )
            if response.status_code != 200:
                continue
            data = response.json()
            if "error" in data:
                continue

            places = _normalize_arcgis_places_results(data, query)
            if places:
                # Keep only points within radius when distance isn't provided.
                filtered = []
                for poi in places:
                    dist = poi.get("distance", 0) or 0
                    if not dist:
                        dist = haversine_distance(lat, lon, poi.get("latitude", 0), poi.get("longitude", 0)) * 1000
                        poi["distance"] = dist
                    if dist <= safe_radius:
                        filtered.append(poi)
                filtered.sort(key=lambda p: p.get("distance", 999999))
                if filtered:
                    return filtered[:safe_limit]
        except Exception:
            continue

    # Fallback to existing ArcGIS geocoding POI search.
    return search_pois_arcgis(lat, lon, query, radius=safe_radius, limit=safe_limit)


def calculate_foot_traffic_score(pois: Dict[str, int]) -> float:
    """
    Calculate foot traffic score based on nearby POI counts.
    
    Args:
        pois: Dictionary of POI category counts
    
    Returns:
        Score from 0-100 indicating foot traffic potential
    """
    weights = {
        "transit": 8,          # Transit hubs = high foot traffic
        "food_service": 6,     # Restaurants/cafes
        "retail": 5,           # Shopping areas
        "foot_traffic": 4,     # Other traffic generators
        "business": 3          # Business districts
    }
    
    score = 0
    score += min(40, pois.get("transit", 0) * weights["transit"])
    score += min(30, pois.get("food_service", 0) * weights["food_service"])
    score += min(20, pois.get("retail", 0) * weights["retail"])
    score += min(15, pois.get("foot_traffic", 0) * weights["foot_traffic"])
    score += min(10, pois.get("business", 0) * weights["business"])
    
    return min(100, score)


def calculate_commercial_viability_score(pois: Dict[str, int], traffic: Dict[str, Any]) -> Dict[str, float]:
    """
    Calculate business-type specific viability scores.
    
    Args:
        pois: Dictionary of POI category counts
        traffic: Traffic flow data
    
    Returns:
        Dictionary with viability scores for different business types
    """
    # Base scores from POI environment
    parking_bonus = min(25, pois.get("parking", 0) * 5)
    transit_bonus = min(30, pois.get("transit", 0) * 6)
    foot_traffic = calculate_foot_traffic_score(pois)
    
    # Traffic consideration (lower congestion = better for some businesses)
    congestion_factor = 1 - (traffic.get("congestion_ratio", 0) / 100)
    
    return {
        "retail_viability": min(100, (
            foot_traffic * 0.5 +           # 50% foot traffic
            parking_bonus * 0.3 +          # 30% parking availability
            pois.get("retail", 0) * 2 +   # Nearby retail is good
            transit_bonus * 0.2            # 20% transit access
        )),
        "restaurant_viability": min(100, (
            foot_traffic * 0.6 +           # 60% foot traffic
            parking_bonus * 0.25 +         # 25% parking
            pois.get("food_service", 0) +  # Competition/cluster effect
            transit_bonus * 0.15           # 15% transit
        )),
        "office_viability": min(100, (
            transit_bonus * 0.4 +          # 40% transit access
            parking_bonus * 0.3 +          # 30% parking
            congestion_factor * 20 +       # 20% low congestion
            pois.get("business", 0) * 2    # Business district proximity
        )),
        "cafe_viability": min(100, (
            foot_traffic * 0.7 +           # 70% foot traffic (critical)
            pois.get("business", 0) * 3 +  # Office workers nearby
            transit_bonus * 0.2 +          # 20% transit
            parking_bonus * 0.1            # 10% parking
        ))
    }


def enrich_site_with_traffic_and_pois(site: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enrich a site with comprehensive POI data.
    
    Args:
        site: Site dictionary with latitude and longitude
    
    Returns:
        Enriched site data with detailed POI analysis
    """
    lat = site.get("latitude", 0)
    lon = site.get("longitude", 0)
    
    print(f"  Enriching {site.get('name', 'Unknown')}...")
    
    # Traffic flow API was removed from this module; keep compatibility fields.
    traffic = {}
    
    # Count POIs by category groups
    poi_counts = {}
    
    # Parking & transit (essential amenities)
    poi_counts["parking"] = search_specific_poi(lat, lon, "parking", radius=300)
    poi_counts["transit"] = (
        search_specific_poi(lat, lon, "public transport", radius=500) +
        search_specific_poi(lat, lon, "railway station", radius=800)
    )
    
    # Food service
    poi_counts["food_service"] = (
        search_specific_poi(lat, lon, "restaurant", radius=500) +
        search_specific_poi(lat, lon, "cafe", radius=500)
    )
    
    # Retail
    poi_counts["retail"] = (
        search_specific_poi(lat, lon, "shopping", radius=500) +
        search_specific_poi(lat, lon, "shop", radius=500)
    )
    
    # Business district indicators
    poi_counts["business"] = search_specific_poi(lat, lon, "office", radius=500)
    
    # Foot traffic generators
    poi_counts["foot_traffic"] = (
        search_specific_poi(lat, lon, "cinema", radius=800) +
        search_specific_poi(lat, lon, "entertainment", radius=800) +
        search_specific_poi(lat, lon, "tourist attraction", radius=800)
    )
    
    # Financial services
    poi_counts["financial"] = (
        search_specific_poi(lat, lon, "bank", radius=500) +
        search_specific_poi(lat, lon, "atm", radius=300)
    )
    
    # Calculate scores
    foot_traffic_score = calculate_foot_traffic_score(poi_counts)
    viability_scores = calculate_commercial_viability_score(poi_counts, traffic)
    
    # Add enrichment data
    site["enriched"] = {
        "traffic_flow": {
            "current_speed": traffic.get("current_speed", 0),
            "free_flow_speed": traffic.get("free_flow_speed", 0),
            "congestion_ratio": traffic.get("congestion_ratio", 0),
            "confidence": traffic.get("confidence", 0)
        },
        "poi_counts": poi_counts,
        "foot_traffic_score": foot_traffic_score,
        "viability_scores": viability_scores,
        # Legacy compatibility
        "nearby_amenities": {
            "parking_count": poi_counts.get("parking", 0),
            "restaurant_count": poi_counts.get("food_service", 0),
            "retail_count": poi_counts.get("retail", 0)
        },
        "amenity_density_score": min(100, (
            poi_counts.get("parking", 0) * 2 + 
            poi_counts.get("food_service", 0) + 
            poi_counts.get("retail", 0)
        ) * 5)
    }
    
    return site


def enrich_all_top_sites(sites: List[Dict[str, Any]], max_sites: int = 5) -> List[Dict[str, Any]]:
    """
    Enrich multiple sites with traffic and comprehensive POI data.
    
    Args:
        sites: List of sites to enrich
        max_sites: Maximum number of sites to enrich (API rate limit consideration)
    
    Returns:
        List of enriched sites
    """
    enriched_sites = []
    
    print(f"\nEnriching top {min(len(sites), max_sites)} sites with Places API data...")
    print("=" * 80)
    
    for i, site in enumerate(sites[:max_sites]):
        enriched_site = enrich_site_with_traffic_and_pois(site)
        enriched_sites.append(enriched_site)
        
        # Show progress with enhanced metrics
        enriched = enriched_site.get("enriched", {})
        poi_counts = enriched.get("poi_counts", {})
        viability = enriched.get("viability_scores", {})
        
        print(f"    POIs: {poi_counts.get('parking', 0)} parking, "
              f"{poi_counts.get('food_service', 0)} food, "
              f"{poi_counts.get('retail', 0)} retail, "
              f"{poi_counts.get('transit', 0)} transit")
        print(f"    Foot Traffic Score: {enriched.get('foot_traffic_score', 0):.1f}/100")
        print(f"    Viability: Retail {viability.get('retail_viability', 0):.0f} | "
              f"Restaurant {viability.get('restaurant_viability', 0):.0f} | "
              f"Office {viability.get('office_viability', 0):.0f}")
        print()
    
    return enriched_sites


def main():
    """Test the enhanced Places API integration"""
    print("=" * 80)
    print("ENHANCED LOCATION DATA INTEGRATION (ArcGIS-first)")
    print("Testing comprehensive POI analysis with new category-based scoring")
    print("=" * 80)
    
    # Test with sample location
    test_location = {
        "name": "Sample Location",
        "latitude": 51.4429,
        "longitude": 5.4474
    }
    
    enriched = enrich_site_with_traffic_and_pois(test_location)
    
    print("\n📊 ENRICHED SITE ANALYSIS:")
    print("=" * 80)
    
    enriched_data = enriched.get("enriched", {})
    
    # POI Counts
    print("\n📍 POI Analysis (by category):")
    poi_counts = enriched_data.get("poi_counts", {})
    for category, count in poi_counts.items():
        print(f"  {category.replace('_', ' ').title()}: {count}")
    
    # Foot Traffic
    print(f"\n👥 Foot Traffic Score: {enriched_data.get('foot_traffic_score', 0):.1f}/100")
    
    # Viability Scores
    print("\n💼 Commercial Viability Scores:")
    viability = enriched_data.get("viability_scores", {})
    print(f"  Retail: {viability.get('retail_viability', 0):.1f}/100")
    print(f"  Restaurant: {viability.get('restaurant_viability', 0):.1f}/100")
    print(f"  Office: {viability.get('office_viability', 0):.1f}/100")
    print(f"  Cafe: {viability.get('cafe_viability', 0):.1f}/100")
    
    print("\n" + "=" * 80)
    print("✅ Enhanced location integration complete!")
    print("=" * 80)
    print("\nNew Features:")
    print("  • Comprehensive POI category analysis")
    print("  • Foot traffic scoring based on nearby attractions")
    print("  • Business-type specific viability scores")
    print("  • Transit accessibility analysis")
    print("  • Competition/cluster effect evaluation")
    
    # Save detailed results
    output_file = "enhanced_poi_analysis_test.json"
    with open(output_file, 'w') as f:
        json.dump(enriched, f, indent=2)
    print(f"\nDetailed results saved to: {output_file}")


if __name__ == "__main__":
    main()
