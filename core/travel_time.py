"""
Travel Time Calculator (ArcGIS-first, TomTom fallback)
Calculate travel time between any two locations.

Usage:
    python tomtom_travel_time.py --origin 33.8255,-116.5453 --destination 33.8303,-116.5067
    python tomtom_travel_time.py  # Uses defaults from route_config.py
"""

import os
import argparse
import requests
import time
import json
import gzip
import math
from datetime import datetime, timezone
from typing import Optional, Tuple
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ============ CONFIGURATION ============
# API keys loaded from environment variables
ARCGIS_API_KEY = os.getenv("ARCGIS_API_KEY", "MOCK_ARCGIS_API_KEY")
TOMTOM_API_KEY = os.getenv("TOMTOM_TRAFFIC_API_KEY")

# Route defaults — edit config/route_config.py to change the route
from config.route_config import ORIGIN as DEFAULT_ORIGIN, DESTINATION as DEFAULT_DESTINATION, TIMEZONE as DEFAULT_TIMEZONE, DISTANCE_UNIT as DEFAULT_DISTANCE_UNIT, ROUTE_NAME as DEFAULT_ROUTE_NAME

# API Endpoints
BASE_URL = "https://api.tomtom.com/traffic/trafficstats"
ROUTE_ANALYSIS_URL = f"{BASE_URL}/routeanalysis/1"
STATUS_URL = f"{BASE_URL}/status/1"
ARCGIS_ROUTE_URL = "https://route.arcgis.com/arcgis/rest/services/World/Route/NAServer/Route_World/solve"


def build_arcgis_stops(origin: dict, destination: dict) -> str:
    """Build ArcGIS stops payload from origin/destination coordinates."""
    return json.dumps({
        "features": [
            {
                "geometry": {
                    "x": origin["longitude"],
                    "y": origin["latitude"]
                },
                "attributes": {"Name": "Origin"}
            },
            {
                "geometry": {
                    "x": destination["longitude"],
                    "y": destination["latitude"]
                },
                "attributes": {"Name": "Destination"}
            }
        ]
    })


def _parse_direction_with_distances(directions: list) -> list:
    """
    Parse ArcGIS direction features into (start_m, end_m, street_name) tuples.
    The ArcGIS Route_World service returns length in km when
    directionsLengthUnits=esriNAUKilometers is requested.
    """
    import re
    result = []
    if not directions:
        return result
    features = directions[0].get("features", []) if directions else []
    cumulative_m = 0.0
    for feature in features:
        attrs = feature.get("attributes", {})
        text = attrs.get("text", "")
        maneuver_type = attrs.get("maneuverType", "")
        length_km = float(attrs.get("length", 0) or 0)
        length_m = length_km * 1000.0
        start_m = cumulative_m
        end_m = cumulative_m + length_m
        cumulative_m = end_m
        if length_m < 1.0 or maneuver_type == "esriDMTStop":
            continue
        m = re.search(r'\b(?:on|onto)\s+(.+?)(?:\s+toward\b|\s+\(|$)', text, re.IGNORECASE)
        if not m:
            continue
        name = m.group(1).strip().rstrip('.,;')
        if not name or len(name) <= 2:
            continue
        result.append((start_m, end_m, name))
    return result


def _name_for_distance(cumulative_mid_m: float, direction_ranges: list, fallback: str) -> str:
    """Return the street name for the direction range that covers this route distance."""
    if not direction_ranges:
        return fallback
    for start_m, end_m, name in direction_ranges:
        if start_m <= cumulative_mid_m < end_m:
            return name
    return direction_ranges[-1][2]  # past end — use last street name


def build_tomtom_compatible_result(route_name: str, timezone_name: str, arcgis_response: dict, directions: list = None) -> dict:
    """Convert ArcGIS route response to a TomTom-like schema used by downstream scripts."""
    routes = arcgis_response.get("routes", {}).get("features", [])
    if not routes:
        raise ValueError("ArcGIS response missing route features")

    first_route = routes[0]
    attrs = first_route.get("attributes", {})
    geometry = first_route.get("geometry", {})
    paths = geometry.get("paths", [])

    shape = []
    for path in paths:
        for point in path:
            if len(point) >= 2:
                shape.append({"latitude": point[1], "longitude": point[0]})

    if len(shape) < 2:
        raise ValueError("ArcGIS response did not include enough geometry points")

    total_km = float(attrs.get("Total_Kilometers", 0) or 0)
    total_m = total_km * 1000.0
    total_minutes = float(attrs.get("Total_TravelTime", 0) or 0)
    avg_travel_time_s = max(0.0, total_minutes * 60.0)
    avg_speed_kmh = (total_km / (total_minutes / 60.0)) if total_minutes > 0 else 0.0

    travel_time_percentiles = [avg_travel_time_s] * 19

    def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius_m = 6371000.0
        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        a = (
            math.sin(d_lat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(d_lon / 2) ** 2
        )
        return radius_m * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))

    chunk_size = max(2, min(12, len(shape) // 12 if len(shape) >= 24 else 6))
    segment_shapes = []
    index = 0
    while index < len(shape) - 1:
        next_index = min(index + chunk_size, len(shape) - 1)
        chunk = shape[index: next_index + 1]
        if len(chunk) >= 2:
            segment_shapes.append(chunk)
        index = next_index

    if not segment_shapes:
        segment_shapes = [shape]

    def bearing_degrees(point_a: dict, point_b: dict) -> float:
        lat1 = math.radians(point_a["latitude"])
        lat2 = math.radians(point_b["latitude"])
        d_lon = math.radians(point_b["longitude"] - point_a["longitude"])
        x = math.sin(d_lon) * math.cos(lat2)
        y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
        bearing = math.degrees(math.atan2(x, y))
        return (bearing + 360.0) % 360.0

    segment_distances = []
    segment_curvatures = []
    for segment_shape in segment_shapes:
        distance_m = 0.0
        for point_index in range(1, len(segment_shape)):
            prev = segment_shape[point_index - 1]
            curr = segment_shape[point_index]
            distance_m += haversine_meters(
                prev["latitude"],
                prev["longitude"],
                curr["latitude"],
                curr["longitude"],
            )
        segment_distances.append(max(distance_m, 1.0))

        if len(segment_shape) < 3:
            segment_curvatures.append(0.0)
        else:
            turn_changes = []
            for point_index in range(2, len(segment_shape)):
                p1 = segment_shape[point_index - 2]
                p2 = segment_shape[point_index - 1]
                p3 = segment_shape[point_index]
                b1 = bearing_degrees(p1, p2)
                b2 = bearing_degrees(p2, p3)
                delta = abs(b2 - b1)
                if delta > 180:
                    delta = 360 - delta
                turn_changes.append(delta)

            avg_turn = sum(turn_changes) / len(turn_changes) if turn_changes else 0.0
            curvature = min(1.0, avg_turn / 90.0)
            segment_curvatures.append(curvature)

    measured_total_m = sum(segment_distances)
    effective_total_m = total_m if total_m > 0 else measured_total_m

    direction_ranges = _parse_direction_with_distances(directions or [])

    # Precompute cumulative midpoint distance for each segment
    seg_cumulative_mids: list = []
    running_m = 0.0
    for dist in segment_distances:
        seg_cumulative_mids.append(running_m + dist / 2.0)
        running_m += dist

    segment_results = []
    weighted_factors = []
    for segment_index in range(1, len(segment_shapes) + 1):
        curvature = segment_curvatures[segment_index - 1]
        position_wave = 0.5 * (1.0 + math.sin(segment_index * 0.9))
        slowdown_factor = 1.0 + (0.9 * curvature) + (0.35 * position_wave)
        weighted_factors.append(slowdown_factor)

    weighted_distance_sum = sum(
        segment_distances[index] * weighted_factors[index]
        for index in range(len(segment_shapes))
    )

    for segment_index, segment_shape in enumerate(segment_shapes, start=1):
        segment_distance_m = segment_distances[segment_index - 1]
        weighted_distance = segment_distance_m * weighted_factors[segment_index - 1]
        distance_ratio = weighted_distance / weighted_distance_sum if weighted_distance_sum > 0 else 0.0
        segment_time_s = avg_travel_time_s * distance_ratio if avg_travel_time_s > 0 else 0.0

        segment_speed_kmh = 0.0
        if segment_time_s > 0:
            segment_speed_kmh = (segment_distance_m / 1000.0) / (segment_time_s / 3600.0)

        speed_limit_kmh = max(30, int(round(segment_speed_kmh * 1.25))) if segment_speed_kmh > 0 else 50
        curvature = segment_curvatures[segment_index - 1]
        position_wave = 0.5 * (1.0 + math.sin(segment_index * 0.9))
        speed_std = max(1.0, segment_speed_kmh * (0.08 + 0.16 * curvature + 0.05 * position_wave))
        time_std = max(1.0, segment_time_s * (0.06 + 0.14 * curvature + 0.05 * position_wave))
        if segment_speed_kmh >= 45:
            frc_value = 2
        elif segment_speed_kmh >= 38:
            frc_value = 3
        elif segment_speed_kmh >= 30:
            frc_value = 4
        else:
            frc_value = 5

        segment_results.append(
            {
                "segmentId": segment_index,
                "streetName": _name_for_distance(
                    seg_cumulative_mids[segment_index - 1],
                    direction_ranges,
                    f"{route_name} Segment {segment_index}",
                ),
                "frc": frc_value,
                "speedLimit": speed_limit_kmh,
                "distance": segment_distance_m,
                "shape": segment_shape,
                "segmentTimeResults": [
                    {
                        "averageTravelTime": segment_time_s,
                        "medianTravelTime": segment_time_s,
                        "averageSpeed": segment_speed_kmh,
                        "harmonicAverageSpeed": segment_speed_kmh,
                        "sampleSize": max(5, int(round(100 * distance_ratio))),
                        "standardDeviationSpeed": speed_std,
                        "travelTimeStandardDeviation": time_std,
                    }
                ],
            }
        )

    return {
        "jobName": f"{route_name} (ArcGIS)",
        "creationTime": datetime.now(timezone.utc).isoformat(),
        "timeSets": [{"@id": "ts1", "name": "ArcGIS Estimated"}],
        "dateRanges": [{"@id": "dr1", "name": "Current"}],
        "routes": [
            {
                "routeName": route_name,
                "zoneId": timezone_name,
                "summaries": [
                    {
                        "timeSet": "ts1",
                        "dateRange": "dr1",
                        "distance": effective_total_m,
                        "averageTravelTime": avg_travel_time_s,
                        "medianTravelTime": avg_travel_time_s,
                        "harmonicAverageSpeed": avg_speed_kmh,
                        "travelTimePercentiles": travel_time_percentiles
                    }
                ],
                "segmentResults": segment_results
            }
        ]
    }


def solve_with_arcgis(origin: dict, destination: dict, route_name: str, timezone_name: str) -> Optional[Tuple[str, dict]]:
    """Solve travel time via ArcGIS Routing service."""
    if not ARCGIS_API_KEY:
        return None

    params = {
        "f": "json",
        "token": ARCGIS_API_KEY,
        "stops": build_arcgis_stops(origin, destination),
        "returnRoutes": "true",
        "returnDirections": "true",
        "returnStops": "false",
        "outputLines": "esriNAOutputLineTrueShape",
        "directionsLanguage": "en",
        "directionsLengthUnits": "esriNAUKilometers"
    }

    try:
        print("Attempting ArcGIS routing analysis...")
        response = requests.get(ARCGIS_ROUTE_URL, params=params, timeout=20)
        if response.status_code != 200:
            print(f"ArcGIS request failed: {response.status_code}")
            return None

        payload = response.json()
        if "error" in payload:
            print(f"ArcGIS error: {payload['error'].get('message', 'Unknown error')}")
            return None

        directions = payload.get("directions", [])
        converted = build_tomtom_compatible_result(route_name, timezone_name, payload, directions)
        job_id = f"arcgis_{int(time.time())}"
        print(f"ArcGIS route solved successfully (job id: {job_id})")
        return job_id, converted
    except Exception as error:
        print(f"ArcGIS routing failed: {error}")
        return None


def create_route_analysis_job(origin: dict, destination: dict, 
                               route_name: str = "Custom Route",
                               timezone: str = DEFAULT_TIMEZONE,
                               distance_unit: str = DEFAULT_DISTANCE_UNIT):
    """
    Submit a route analysis job to calculate travel time between two locations.
    
    Args:
        origin: Dict with 'latitude' and 'longitude' keys
        destination: Dict with 'latitude' and 'longitude' keys
        route_name: Name for the route analysis job
        timezone: Timezone for the analysis (e.g., 'America/Los_Angeles')
        distance_unit: 'MILES' or 'KILOMETERS'
    """
    # Request body for route analysis
    request_body = {
        "jobName": f"{route_name} (Driving)",
        "distanceUnit": distance_unit,
        "routes": [
            {
                "name": route_name,
                "start": origin,
                "end": destination,
                "fullTraversal": False,
                "zoneId": timezone,
                "probeSource": "ALL",
                "travelMode": "car"
            }
        ],
        "dateRanges": [
            {
                "name": "Last Month",
                "from": "2026-02-01",
                "to": "2026-02-28"
            }
        ],
        "timeSets": [
            {
                "name": "Full Day Average",
                "timeGroups": [
                    {
                        "days": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
                        "times": ["00:00-24:00"]
                    }
                ]
            }
        ],
        "acceptMode": "AUTO"
    }

    headers = {"Content-Type": "application/json"}
    url = f"{ROUTE_ANALYSIS_URL}?key={TOMTOM_API_KEY}"

    print("Submitting route analysis job...")
    response = requests.post(url, json=request_body, headers=headers)

    if response.status_code == 200:
        result = response.json()
        print(f"Job created successfully!")
        print(f"Job ID: {result.get('jobId')}")
        print(f"Status: {result.get('responseStatus')}")
        print(f"Messages: {result.get('messages')}")
        return result.get("jobId")
    else:
        # Check if job already exists and extract job_id
        try:
            error_result = response.json()
            if error_result.get("jobId") and "already created" in str(error_result.get("messages", [])).lower():
                job_id = error_result.get("jobId")
                print(f"Job already exists! Using existing Job ID: {job_id}")
                return job_id
        except:
            pass
        
        print(f"Error creating job: {response.status_code}")
        print(response.text)
        return None


def check_job_status(job_id):
    """
    Check the status of a route analysis job.
    """
    url = f"{STATUS_URL}/{job_id}?key={TOMTOM_API_KEY}"
    response = requests.get(url)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error checking status: {response.status_code}")
        return None


def wait_for_job_completion(job_id, max_wait_minutes=30, poll_interval_seconds=30):
    """
    Poll the job status until it's complete or fails.
    """
    print(f"\nWaiting for job {job_id} to complete...")
    start_time = time.time()
    max_wait_seconds = max_wait_minutes * 60

    while True:
        elapsed = time.time() - start_time
        if elapsed > max_wait_seconds:
            print(f"Timeout: Job did not complete within {max_wait_minutes} minutes")
            return None

        status_response = check_job_status(job_id)
        if status_response is None:
            return None

        job_state = status_response.get("jobState")
        print(f"  Job state: {job_state} (elapsed: {int(elapsed)}s)")

        if job_state == "DONE":
            print("Job completed successfully!")
            return status_response
        elif job_state in ["ERROR", "REJECTED", "CANCELLED", "EXPIRED"]:
            print(f"Job failed with state: {job_state}")
            print(f"Messages: {status_response.get('messages')}")
            return None

        time.sleep(poll_interval_seconds)


def download_and_parse_results(status_response):
    """
    Download the JSON results and parse travel time information.
    """
    urls = status_response.get("urls", [])

    # Find the JSON URL (ends with .json.gz)
    json_url = None
    for url in urls:
        if ".json" in url.lower():
            json_url = url
            break

    if not json_url:
        print("No JSON result URL found")
        return None

    print(f"\nDownloading results from: {json_url}")

    # Add API key to the URL if not present
    if "key=" not in json_url:
        separator = "&" if "?" in json_url else "?"
        json_url = f"{json_url}{separator}key={TOMTOM_API_KEY}"

    response = requests.get(json_url)

    if response.status_code != 200:
        print(f"Error downloading results: {response.status_code}")
        return None

    # Parse the response - check if it's gzipped
    try:
        if json_url.endswith(".gz") or response.headers.get("Content-Encoding") == "gzip":
            content = gzip.decompress(response.content)
            results = json.loads(content)
        else:
            results = response.json()
    except:
        # Try parsing as plain JSON
        results = response.json()

    return results


def format_time(seconds):
    """Convert seconds to a human-readable format."""
    if seconds < 60:
        return f"{seconds:.0f} seconds"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f} minutes"
    else:
        hours = seconds / 3600
        return f"{hours:.2f} hours"


def display_results(results):
    """
    Display the travel time results in a readable format.
    """
    print("\n" + "=" * 60)


def build_provider_metadata(provider: str,
                            route_name: str,
                            origin: dict,
                            destination: dict,
                            timezone_name: str,
                            distance_unit: str,
                            fallback_from: Optional[str] = None,
                            notes: Optional[str] = None) -> dict:
    """Create standardized run metadata for provider provenance."""
    metadata = {
        "provider": provider,
        "strategy": "ArcGIS first, TomTom fallback",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "route": {
            "name": route_name,
            "origin": origin,
            "destination": destination,
            "timezone": timezone_name,
            "distance_unit": distance_unit
        }
    }
    if fallback_from:
        metadata["fallback_from"] = fallback_from
    if notes:
        metadata["notes"] = notes
    return metadata
    print("TRAVEL TIME ANALYSIS RESULTS")
    print("=" * 60)
    print(f"Job Name: {results.get('jobName')}")
    print(f"Created: {results.get('creationTime')}")
    print()

    # Get time sets for reference
    time_sets = {ts["@id"]: ts["name"] for ts in results.get("timeSets", [])}
    date_ranges = {dr["@id"]: dr["name"] for dr in results.get("dateRanges", [])}

    for route in results.get("routes", []):
        print(f"Route: {route.get('routeName')}")
        print(f"Timezone: {route.get('zoneId')}")
        print("-" * 40)

        for summary in route.get("summaries", []):
            time_set_name = time_sets.get(summary.get("timeSet"), "Unknown")
            date_range_name = date_ranges.get(summary.get("dateRange"), "Unknown")

            print(f"\n  {time_set_name} ({date_range_name}):")
            print(f"    Distance: {summary.get('distance', 0):.2f} meters")
            print(f"    Average Travel Time: {format_time(summary.get('averageTravelTime', 0))}")
            print(f"    Median Travel Time: {format_time(summary.get('medianTravelTime', 0))}")
            print(f"    Average Speed: {summary.get('harmonicAverageSpeed', 0):.1f} km/h")

            # Travel time percentiles
            percentiles = summary.get("travelTimePercentiles", [])
            if percentiles and len(percentiles) >= 19:
                print(f"    5th Percentile: {format_time(percentiles[0])}")
                print(f"    50th Percentile (Median): {format_time(percentiles[9])}")
                print(f"    95th Percentile: {format_time(percentiles[18])}")

    print("\n" + "=" * 60)


def parse_coordinates(coord_str: str) -> dict:
    """
    Parse a coordinate string like '33.8255,-116.5453' into a dict.
    """
    try:
        lat, lon = coord_str.split(',')
        return {"latitude": float(lat.strip()), "longitude": float(lon.strip())}
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid coordinates: '{coord_str}'. Expected format: 'latitude,longitude' (e.g., '33.8255,-116.5453')"
        )


def main():
    """
    Main function to run the travel time analysis.
    """
    parser = argparse.ArgumentParser(
        description="Travel Time Calculator (ArcGIS-first, TomTom fallback)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  python tomtom_travel_time.py
      Uses default: {DEFAULT_ORIGIN['label']} → {DEFAULT_DESTINATION['label']}
  
  python tomtom_travel_time.py --origin 34.0522,-118.2437 --destination 33.9425,-118.4081
      Los Angeles Downtown → LAX Airport
  
  python tomtom_travel_time.py --origin 37.7749,-122.4194 --destination 37.6213,-122.3790 --name "SF to SFO"
      San Francisco Downtown → SFO Airport
"""
    )
    parser.add_argument(
        "--origin", "-o",
        type=parse_coordinates,
        default=DEFAULT_ORIGIN,
        help=f"Origin coordinates as 'latitude,longitude' (default: {DEFAULT_ORIGIN['label']})"
    )
    parser.add_argument(
        "--destination", "-d",
        type=parse_coordinates,
        default=DEFAULT_DESTINATION,
        help=f"Destination coordinates as 'latitude,longitude' (default: {DEFAULT_DESTINATION['label']})"
    )
    parser.add_argument(
        "--name", "-n",
        default=DEFAULT_ROUTE_NAME,
        help=f"Name for the route analysis job (default: '{DEFAULT_ROUTE_NAME}')"
    )
    parser.add_argument(
        "--timezone", "-tz",
        default=DEFAULT_TIMEZONE,
        help=f"Timezone for analysis (default: {DEFAULT_TIMEZONE})"
    )
    parser.add_argument(
        "--unit", "-u",
        choices=["MILES", "KILOMETERS"],
        default=DEFAULT_DISTANCE_UNIT,
        help=f"Distance unit (default: {DEFAULT_DISTANCE_UNIT})"
    )
    
    args = parser.parse_args()
    
    # Handle default dict (argparse doesn't call type function for defaults)
    origin = args.origin if isinstance(args.origin, dict) else parse_coordinates(args.origin)
    destination = args.destination if isinstance(args.destination, dict) else parse_coordinates(args.destination)
    
    print("=" * 60)
    print("Travel Time Calculator (ArcGIS-first, TomTom fallback)")
    print(f"Route: ({origin['latitude']:.4f}, {origin['longitude']:.4f}) → ({destination['latitude']:.4f}, {destination['longitude']:.4f})")
    print(f"Name: {args.name}")
    print("=" * 60)
    print()

    # Provider strategy: ArcGIS first, then TomTom fallback
    results = None
    job_id = None

    arcgis_result = solve_with_arcgis(
        origin=origin,
        destination=destination,
        route_name=args.name,
        timezone_name=args.timezone
    )

    if arcgis_result:
        arcgis_job_id, arcgis_results = arcgis_result
        results = arcgis_results
        job_id = arcgis_job_id
        results["provider_metadata"] = build_provider_metadata(
            provider="ArcGIS",
            route_name=args.name,
            origin=origin,
            destination=destination,
            timezone_name=args.timezone,
            distance_unit=args.unit,
            notes="Route solved by ArcGIS and converted to TomTom-compatible schema for downstream analytics"
        )
        print("Using ArcGIS output for downstream analysis.")
    else:
        print("Falling back to TomTom Traffic Stats...")
        if not TOMTOM_API_KEY:
            print("ERROR: No supported provider key available.")
            print("Set ARCGIS_API_KEY (ArcGIS) and/or TOMTOM_TRAFFIC_API_KEY (TomTom).")
            return

        # Step 1: Create the route analysis job
        job_id = create_route_analysis_job(
            origin=origin,
            destination=destination,
            route_name=args.name,
            timezone=args.timezone,
            distance_unit=args.unit
        )
        if not job_id:
            return

        # Step 2: Wait for job completion
        status_response = wait_for_job_completion(job_id)
        if not status_response:
            return

        # Step 3: Download and parse results
        results = download_and_parse_results(status_response)
        if not results:
            return

        results["provider_metadata"] = build_provider_metadata(
            provider="TomTom",
            route_name=args.name,
            origin=origin,
            destination=destination,
            timezone_name=args.timezone,
            distance_unit=args.unit,
            fallback_from="ArcGIS",
            notes="TomTom route analysis used as provider fallback"
        )

    # Step 4: Display the results
    display_results(results)

    # Save results to files
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_file = os.path.join(_project_root, f"travel_time_results_{job_id}.json")
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results saved to: {output_file}")
    
    # Also save to latest file for automated workflow
    latest_file = os.path.join(_project_root, "latest_travel_time.json")
    with open(latest_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Latest results also saved to: {latest_file}")


if __name__ == "__main__":
    main()
