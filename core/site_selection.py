"""
Site Selection Analysis for TomTom and ArcGIS Travel Time Data
Compare multiple candidate sites based on travel time and accessibility metrics

Data Sources by Mode:
- TomTom Mode: Uses TomTom Travel Stats API + AADT shapefile (California traffic volumes)
- ArcGIS Mode: Uses ArcGIS World Routing API only (no TomTom data, no AADT)
"""

import json
import math
import os
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Any


# NOTE: FRC (Functional Road Class) values used in this analysis are:
# - Provided directly by TomTom Traffic Stats API (when using TomTom mode)
# - Inferred from average speed for ArcGIS routes (FRC 2-5 based on speed thresholds)
# FRC is used only as a road classification metric in scoring, not as external data.

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class CandidateSite:
    """Represents a candidate site for selection"""
    name: str
    latitude: float
    longitude: float
    segment_ids: List[int]  # Associated road segments
    description: str = ""
    
    # Calculated metrics
    nearby_segment_time_seconds: float = 0.0
    median_travel_time: float = 0.0
    avg_speed: float = 0.0
    avg_aadt: float = 0.0  # Average Annual Daily Traffic (vehicles/day)
    accessibility_score: float = 0.0
    traffic_reliability: float = 0.0
    traffic_exposure_score: float = 0.0  # Score based on AADT
    primary_frc: int = 8  # Dominant Functional Road Class near site (speed-inferred)
    frc_coverage: float = 0.0  # Percentage of FRC coverage from probe data (0-100)
    overall_score: float = 0.0


def load_travel_time_data(filepath: str) -> Dict[str, Any]:
    """Load the TomTom travel time results JSON file"""
    with open(filepath, 'r') as f:
        return json.load(f)


def load_aadt_data(filepath: str = None) -> Dict[int, int]:
    """Load AADT data and create segment_id -> aadt mapping (TomTom mode only)"""
    if filepath is None:
        filepath = BASE_DIR / "aadt_results.json"
    
    if not os.path.exists(filepath):
        print(f"⚠️  AADT data not found at {filepath}")
        print("   This is TomTom-exclusive data from California shapefile.")
        return {}
    
    try:
        with open(filepath, 'r') as f:
            aadt_data = json.load(f)
        
        # Create mapping: segment_id -> aadt
        aadt_map = {}
        
        # Add originally matched segments
        for seg in aadt_data.get('matched_segments', []):
            seg_id = seg.get('segment_id')
            aadt = seg.get('aadt_match', {}).get('aadt', 0)
            if seg_id and aadt:
                aadt_map[seg_id] = aadt
        
        # Add nearest neighbor matches
        for seg in aadt_data.get('nearest_neighbor_matches', []):
            seg_id = seg.get('segment_id')
            aadt = seg.get('aadt_nearest_neighbor', {}).get('aadt', 0)
            if seg_id and aadt:
                aadt_map[seg_id] = aadt
        
        matched_count = len(aadt_data.get('matched_segments', []))
        nearest_count = len(aadt_data.get('nearest_neighbor_matches', []))
        
        print(f"✓ Loaded AADT data for {len(aadt_map)} segments")
        print(f"  ({matched_count} direct matches + {nearest_count} nearest neighbor)")
        return aadt_map
    except Exception as e:
        print(f"⚠️  Error loading AADT data: {e}")
        return {}


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


def find_nearest_segments(data: Dict, site_lat: float, site_lon: float, max_distance_km: float = 0.3) -> List[Dict]:
    """Find road segments near a given location"""
    nearby_segments = []
    
    for route in data.get("routes", []):
        for segment in route.get("segmentResults", []):
            shape = segment.get("shape", [])
            if not shape:
                continue
            
            # Check if any point in segment shape is near the site
            for point in shape:
                dist = haversine_distance(site_lat, site_lon, point["latitude"], point["longitude"])
                if dist <= max_distance_km:
                    nearby_segments.append(segment)
                    break
    
    return nearby_segments


def calculate_segment_metrics(segment: Dict) -> Dict[str, float]:
    """Extract travel time metrics from a segment"""
    time_results = segment.get("segmentTimeResults", [])
    if not time_results:
        return {}
    
    # Use the first time result (Full Day Average)
    result = time_results[0]
    
    return {
        "segment_travel_time_seconds": result.get("averageTravelTime", 0),
        "median_travel_time": result.get("medianTravelTime", 0),
        "avg_speed": result.get("averageSpeed", 0),
        "harmonic_speed": result.get("harmonicAverageSpeed", 0),
        "sample_size": result.get("sampleSize", 0),
        "normalized_sample_size": result.get("normalizedSampleSize", 0),  # FRC coverage (0-1)
        "speed_std_dev": result.get("standardDeviationSpeed", 0),
        "travel_time_std_dev": result.get("travelTimeStandardDeviation", 0),
        "distance": segment.get("distance", 0),
        "speed_limit": segment.get("speedLimit", 0),
        "frc": segment.get("frc", 8),  # Functional Road Class
        "street_name": segment.get("streetName", "Unknown")
    }


def calculate_accessibility_score(metrics: Dict[str, float]) -> float:
    """
    Calculate accessibility score (0-100) based on travel time metrics
    Higher score = better accessibility
    """
    if not metrics:
        return 0
    
    score = 100.0
    
    # Penalize for high travel time (relative to distance)
    distance_m = metrics.get("distance", 1)
    avg_time = metrics.get("segment_travel_time_seconds", 0)
    if distance_m > 0 and avg_time > 0:
        # Expected time at speed limit
        speed_limit_ms = metrics.get("speed_limit", 50) * 1000 / 3600  # Convert to m/s
        expected_time = distance_m / speed_limit_ms if speed_limit_ms > 0 else avg_time
        time_ratio = avg_time / expected_time if expected_time > 0 else 1
        score -= min(30, (time_ratio - 1) * 20)  # Penalize up to 30 points for delays
    
    # Bonus for higher speeds
    avg_speed = metrics.get("avg_speed", 0)
    if avg_speed >= 40:
        score += 10
    elif avg_speed >= 30:
        score += 5
    elif avg_speed < 15:
        score -= 10
    
    # Bonus for lower FRC (more important roads)
    frc = metrics.get("frc", 8)
    score += (8 - frc) * 2  # Up to 16 points for FRC 0
    
    return max(0, min(100, score))


def calculate_traffic_reliability(metrics: Dict[str, float]) -> float:
    """
    Calculate traffic reliability score (0-100)
    Lower variability = higher reliability
    """
    if not metrics:
        return 0
    
    score = 100.0
    
    # Penalize for high speed variability
    speed_std = metrics.get("speed_std_dev", 0)
    avg_speed = metrics.get("avg_speed", 1)
    if avg_speed > 0:
        cv_speed = speed_std / avg_speed  # Coefficient of variation
        score -= min(40, cv_speed * 100)
    
    # Penalize for high travel time variability
    time_std = metrics.get("travel_time_std_dev", 0)
    median_time = metrics.get("median_travel_time", 1)
    if median_time > 0:
        cv_time = time_std / median_time
        score -= min(40, cv_time * 50)
    
    return max(0, min(100, score))


def calculate_traffic_exposure_score(avg_aadt: float) -> float:
    """Calculate traffic exposure score (0-100) based on AADT
    
    Higher AADT = More potential customers passing by
    Score normalized for typical urban/suburban AADT ranges
    """
    if avg_aadt <= 0:
        return 0
    
    # Scale AADT to 0-100 score
    # Typical ranges:
    #   < 2,000: Low exposure (rural/residential)
    #   2,000-5,000: Moderate (collectors)
    #   5,000-15,000: Good (minor arterials)
    #   15,000-30,000: Excellent (major arterials)
    #   > 30,000: Very high (may indicate congestion issues)
    
    if avg_aadt < 2000:
        score = (avg_aadt / 2000) * 30  # 0-30 points
    elif avg_aadt < 5000:
        score = 30 + ((avg_aadt - 2000) / 3000) * 20  # 30-50 points
    elif avg_aadt < 15000:
        score = 50 + ((avg_aadt - 5000) / 10000) * 30  # 50-80 points
    elif avg_aadt < 30000:
        score = 80 + ((avg_aadt - 15000) / 15000) * 15  # 80-95 points
    else:
        # Diminishing returns above 30k (congestion concerns)
        score = 95 + min(5, (avg_aadt - 30000) / 10000)  # 95-100 points
    
    return min(100, max(0, score))


def evaluate_site(data: Dict, site: CandidateSite, aadt_map: Dict[int, int] = None) -> CandidateSite:
    """Evaluate a candidate site based on nearby road segment metrics"""
    
    if aadt_map is None:
        aadt_map = {}
    
    # Find nearby segments
    nearby_segments = find_nearest_segments(data, site.latitude, site.longitude)
    
    if not nearby_segments:
        print(f"  Warning: No segments found near {site.name}")
        return site
    
    site.segment_ids = [s.get("segmentId", 0) for s in nearby_segments]
    
    # Aggregate metrics from all nearby segments
    total_distance = 0
    weighted_speed = 0
    weighted_aadt = 0
    total_travel_time = 0
    total_median_time = 0
    accessibility_scores = []
    reliability_scores = []
    frc_distance_weights = {}  # Track FRC by distance for dominant FRC calculation
    frc_coverage_values = []  # Track FRC coverage percentages
    raw_sample_sizes = []  # Fallback when normalizedSampleSize is missing
    aadt_segments_count = 0  # Track how many segments have AADT data
    
    for segment in nearby_segments:
        metrics = calculate_segment_metrics(segment)
        if not metrics:
            continue
        
        distance = metrics.get("distance", 0)
        total_distance += distance
        weighted_speed += metrics.get("avg_speed", 0) * distance
        total_travel_time += metrics.get("segment_travel_time_seconds", 0)
        total_median_time += metrics.get("median_travel_time", 0)
        
        # Get AADT if available for this segment
        seg_id = segment.get("segmentId", 0)
        if seg_id in aadt_map:
            aadt = aadt_map[seg_id]
            weighted_aadt += aadt * distance
            aadt_segments_count += 1
        
        # Track FRC weighted by distance
        frc = metrics.get("frc", 8)
        frc_distance_weights[frc] = frc_distance_weights.get(frc, 0) + distance
        
        # Track FRC coverage (normalizedSampleSize is 0-1, convert to percentage)
        normalized_coverage = metrics.get("normalized_sample_size", 0)
        if normalized_coverage > 0:
            frc_coverage_values.append(normalized_coverage * 100)
        raw_sample_sizes.append(metrics.get("sample_size", 0))
        
        accessibility_scores.append(calculate_accessibility_score(metrics))
        reliability_scores.append(calculate_traffic_reliability(metrics))
    
    # Calculate aggregated metrics
    if total_distance > 0:
        site.avg_speed = weighted_speed / total_distance
        if weighted_aadt > 0:
            site.avg_aadt = weighted_aadt / total_distance
    
    site.nearby_segment_time_seconds = total_travel_time
    site.median_travel_time = total_median_time
    
    if accessibility_scores:
        site.accessibility_score = sum(accessibility_scores) / len(accessibility_scores)
    
    if reliability_scores:
        site.traffic_reliability = sum(reliability_scores) / len(reliability_scores)
    
    # Calculate traffic exposure score from AADT
    site.traffic_exposure_score = calculate_traffic_exposure_score(site.avg_aadt)
    
    # Calculate average FRC coverage percentage
    if frc_coverage_values:
        site.frc_coverage = sum(frc_coverage_values) / len(frc_coverage_values)
    elif raw_sample_sizes:
        # Fallback for ArcGIS data which lacks normalizedSampleSize:
        # normalize sampleSize values relative to the max across nearby segments.
        max_sample = max(raw_sample_sizes) if raw_sample_sizes else 0
        if max_sample > 0:
            site.frc_coverage = sum(s / max_sample * 100.0 for s in raw_sample_sizes) / len(raw_sample_sizes)
        else:
            site.frc_coverage = 0.0
    else:
        site.frc_coverage = 0.0
    
    # Determine dominant FRC (by distance weight)
    # FRC values come from route provider (TomTom API or speed-inferred for ArcGIS)
    if frc_distance_weights:
        site.primary_frc = max(frc_distance_weights, key=frc_distance_weights.get)
    else:
        site.primary_frc = 8
    
    # Calculate overall score
    # If AADT data available: accessibility 30%, exposure 25%, reliability 25%, speed 20%
    # If no AADT: fall back to original weights (accessibility 40%, reliability 30%, speed 30%)
    if site.avg_aadt > 0:
        site.overall_score = (
            site.accessibility_score * 0.30 +
            site.traffic_exposure_score * 0.25 +
            site.traffic_reliability * 0.25 +
            min(100, site.avg_speed * 2) * 0.20  # Speed bonus, capped at 100
        )
    else:
        # Original formula without AADT
        site.overall_score = (
            site.accessibility_score * 0.40 +
            site.traffic_reliability * 0.30 +
            min(100, site.avg_speed * 2) * 0.30
        )
    
    return site


def define_candidate_sites_from_route(data: Dict) -> List[CandidateSite]:
    """
    Define candidate sites based on key locations along the route
    Extracts strategic points from the segment data
    """
    sites = []
    
    for route in data.get("routes", []):
        segments = route.get("segmentResults", [])
        
        # Create one candidate site per segment (not deduplicated by street name)
        for segment in segments:
            street_name = segment.get("streetName", "Unknown")
            seg_id = segment.get("segmentId", 0)
            if not street_name or street_name == "Unknown":
                continue
            shape = segment.get("shape", [])
            if not shape:
                continue
            mid_idx = len(shape) // 2
            mid_point = shape[mid_idx]
            sites.append(CandidateSite(
                name=street_name,
                latitude=mid_point["latitude"],
                longitude=mid_point["longitude"],
                segment_ids=[seg_id],
                description=f"Location on {street_name}"
            ))
    
    return sites


def rank_sites(sites: List[CandidateSite]) -> List[CandidateSite]:
    """Rank sites by overall score"""
    return sorted(sites, key=lambda s: s.overall_score, reverse=True)


def print_site_comparison(sites: List[CandidateSite]):
    """Print a comparison table of all candidate sites"""
    print("\n" + "=" * 100)
    print("SITE SELECTION ANALYSIS RESULTS")
    print("=" * 100)
    
    # Check if any sites have AADT data
    has_aadt = any(site.avg_aadt > 0 for site in sites)
    
    if has_aadt:
        print(f"\n{'Rank':<5} {'Site Name':<30} {'Score':<8} {'Access':<8} {'Expos':<8} {'Reliab':<8} {'AADT':<10} {'FRC':<5}")
        print("-" * 95)
        
        for rank, site in enumerate(sites, 1):
            aadt_str = f"{site.avg_aadt:>8,.0f}" if site.avg_aadt > 0 else "     N/A"
            print(f"{rank:<5} {site.name[:29]:<30} {site.overall_score:>6.1f}  "
                  f"{site.accessibility_score:>6.1f}  {site.traffic_exposure_score:>6.1f}  {site.traffic_reliability:>6.1f}  "
                  f"{aadt_str}  {site.primary_frc:>3}")
        
        print("\n" + "-" * 95)
        print("Score: Overall site score (0-100, higher is better)")
        print("Access: Accessibility score based on travel time efficiency")
        print("Expos: Traffic exposure score based on AADT (vehicles/day)")
        print("Reliab: Traffic reliability score based on travel time consistency")
        print("AADT: Annual Average Daily Traffic (vehicles/day)")
        print("FRC: Functional Road Class (0=motorway, 7=local road)")
    else:
        # Fallback to original display if no AADT data
        print(f"\n{'Rank':<5} {'Site Name':<30} {'Score':<8} {'Access':<8} {'Reliab':<8} {'FRC':<5}")
        print("-" * 75)
        
        for rank, site in enumerate(sites, 1):
            print(f"{rank:<5} {site.name[:29]:<30} {site.overall_score:>6.1f}  "
                  f"{site.accessibility_score:>6.1f}  {site.traffic_reliability:>6.1f}  "
                  f"{site.primary_frc:>3}")
        
        print("\n" + "-" * 75)
        print("Score: Overall site score (0-100, higher is better)")
        print("Access: Accessibility score based on travel time efficiency")
        print("Reliab: Traffic reliability score based on travel time consistency")
        print("FRC: Functional Road Class (0=motorway, 7=local road)")


def print_top_sites_detail(sites: List[CandidateSite], top_n: int = 3):
    """Print detailed analysis of top-ranked sites"""
    print("\n" + "=" * 80)
    print(f"TOP {top_n} RECOMMENDED SITES")
    print("=" * 80)
    
    for rank, site in enumerate(sites[:top_n], 1):
        print(f"\n{'#' + str(rank):} {site.name}")
        print("-" * 40)
        print(f"  Location: ({site.latitude:.5f}, {site.longitude:.5f})")
        print(f"  Description: {site.description}")
        print(f"  \n  Metrics:")
        print(f"    Overall Score:        {site.overall_score:.1f}/100")
        print(f"    Accessibility Score:  {site.accessibility_score:.1f}/100")
        if site.avg_aadt > 0:
            print(f"    Traffic Exposure:     {site.traffic_exposure_score:.1f}/100")
            print(f"    AADT:                 {site.avg_aadt:,.0f} vehicles/day")
        print(f"    Reliability Score:    {site.traffic_reliability:.1f}/100")
        print(f"    Road Class (FRC):     {site.primary_frc}")
        print(f"    Average Speed:        {site.avg_speed:.1f} km/h")
        print(f"    Nearby Segment Time:  {site.nearby_segment_time_seconds:.1f} seconds")
        print(f"    Median Travel Time:   {site.median_travel_time:.1f} seconds")
        print(f"    Connected Segments:   {len(site.segment_ids)}")
        
        # Recommendation
        if site.overall_score >= 80:
            recommendation = "Highly Recommended - Excellent accessibility and reliability"
        elif site.overall_score >= 60:
            recommendation = "Recommended - Good accessibility with acceptable traffic conditions"
        elif site.overall_score >= 40:
            recommendation = "Acceptable - Moderate accessibility, consider alternatives"
        else:
            recommendation = "Not Recommended - Poor accessibility or unreliable traffic"
        
        print(f"  \n  Recommendation: {recommendation}")


def export_results_to_json(sites: List[CandidateSite], output_file: str):
    """Export site selection results to JSON"""
    results = {
        "analysis_type": "Multi-Site Comparison",
        "total_sites_evaluated": len(sites),
        "sites": []
    }
    
    for rank, site in enumerate(sites, 1):
        results["sites"].append({
            "rank": rank,
            "name": site.name,
            "location": {
                "latitude": site.latitude,
                "longitude": site.longitude
            },
            "description": site.description,
            "scores": {
                "overall": round(site.overall_score, 2),
                "accessibility": round(site.accessibility_score, 2),
                "reliability": round(site.traffic_reliability, 2)
            },
            "metrics": {
                "average_speed_kmh": round(site.avg_speed, 2),
                "nearby_segment_time_seconds": round(site.nearby_segment_time_seconds, 2),
                "median_travel_time_seconds": round(site.median_travel_time, 2),
                "connected_segments": len(site.segment_ids)
            }
        })
    
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\nResults exported to: {output_file}")


# ============================================================================
# ANALYSIS TYPE 1: OPTIMAL LOCATION ALONG ROUTE
# ============================================================================

def find_optimal_location(data: Dict) -> Dict[str, Any]:
    """
    Find the optimal location along the route based on accessibility metrics.
    Uses a weighted scoring system to identify the best single point.
    """
    all_points = []
    
    for route in data.get("routes", []):
        for segment in route.get("segmentResults", []):
            metrics = calculate_segment_metrics(segment)
            if not metrics:
                continue
            
            shape = segment.get("shape", [])
            if not shape:
                continue
            
            # Calculate scores for this segment
            accessibility = calculate_accessibility_score(metrics)
            reliability = calculate_traffic_reliability(metrics)
            
            # Use midpoint of segment
            mid_idx = len(shape) // 2
            mid_point = shape[mid_idx]
            
            # Calculate composite score with additional factors
            traffic_flow_score = min(100, metrics.get("avg_speed", 0) * 2.5)
            visibility_score = 100 - (metrics.get("frc", 4) * 10)  # Lower FRC = major road = more visibility
            
            composite_score = (
                accessibility * 0.30 +
                reliability * 0.25 +
                traffic_flow_score * 0.25 +
                visibility_score * 0.20
            )
            
            all_points.append({
                "latitude": mid_point["latitude"],
                "longitude": mid_point["longitude"],
                "street_name": metrics.get("street_name", "Unknown"),
                "segment_id": segment.get("segmentId"),
                "accessibility_score": accessibility,
                "reliability_score": reliability,
                "traffic_flow_score": traffic_flow_score,
                "visibility_score": visibility_score,
                "composite_score": composite_score,
                "avg_speed": metrics.get("avg_speed", 0),
                "frc": metrics.get("frc", 8),
                "frc_coverage": metrics.get("normalized_sample_size", 0) * 100,
                "distance_from_start": metrics.get("distance", 0)
            })
    
    if not all_points:
        return None
    
    # Sort by composite score
    all_points.sort(key=lambda x: x["composite_score"], reverse=True)
    
    return {
        "optimal_location": all_points[0],
        "top_5_locations": all_points[:5],
        "total_points_analyzed": len(all_points)
    }


def print_optimal_location_results(results: Dict):
    """Print optimal location analysis results"""
    print("\n" + "=" * 80)
    print("ANALYSIS 1: OPTIMAL LOCATION ALONG ROUTE")
    print("=" * 80)
    
    if not results:
        print("No optimal location found.")
        return
    
    optimal = results["optimal_location"]
    
    print(f"\n★ OPTIMAL LOCATION IDENTIFIED ★")
    print("-" * 40)
    print(f"  Street: {optimal['street_name']}")
    print(f"  Coordinates: ({optimal['latitude']:.5f}, {optimal['longitude']:.5f})")
    print(f"  Composite Score: {optimal['composite_score']:.1f}/100")
    print(f"\n  Score Breakdown:")
    print(f"    Accessibility:   {optimal['accessibility_score']:.1f}/100 (30% weight)")
    print(f"    Reliability:     {optimal['reliability_score']:.1f}/100 (25% weight)")
    print(f"    Traffic Flow:    {optimal['traffic_flow_score']:.1f}/100 (25% weight)")
    print(f"    Visibility:      {optimal['visibility_score']:.1f}/100 (20% weight)")
    print(f"\n  Traffic Metrics:")
    print(f"    Average Speed: {optimal['avg_speed']:.1f} km/h")
    print(f"    Road Class (FRC): {optimal['frc']}")
    
    print(f"\n  Top 5 Alternative Locations:")
    print(f"  {'Rank':<5} {'Street':<30} {'Score':<8} {'Speed':<8}")
    print("  " + "-" * 55)
    for i, loc in enumerate(results["top_5_locations"], 1):
        print(f"  {i:<5} {loc['street_name'][:29]:<30} {loc['composite_score']:>6.1f}  {loc['avg_speed']:>6.1f}")
    
    print(f"\n  Total points analyzed: {results['total_points_analyzed']}")


# ============================================================================
# ANALYSIS TYPE 2: ACCESSIBILITY SCORING
# ============================================================================

def calculate_accessibility_map(data: Dict) -> Dict[str, Any]:
    """
    Calculate accessibility scores for all segments creating an accessibility map.
    Scores areas based on travel time efficiency to/from key destinations.
    """
    # Extract route summary data
    route_summary = None
    for route in data.get("routes", []):
        summaries = route.get("summaries", [])
        if summaries:
            route_summary = summaries[0]
            break
    
    if not route_summary:
        return None
    
    total_distance = route_summary.get("distance", 0)
    total_avg_time = route_summary.get("averageTravelTime", 0)
    
    accessibility_zones = {
        "excellent": [],  # Score >= 90
        "good": [],       # Score 70-89
        "moderate": [],   # Score 50-69
        "poor": []        # Score < 50
    }
    
    segment_accessibility = []
    
    for route in data.get("routes", []):
        cumulative_distance = 0
        
        for segment in route.get("segmentResults", []):
            metrics = calculate_segment_metrics(segment)
            if not metrics:
                continue
            
            segment_distance = metrics.get("distance", 0)
            cumulative_distance += segment_distance
            
            # Calculate position along route (0-1)
            position_ratio = cumulative_distance / total_distance if total_distance > 0 else 0
            
            # Accessibility score based on multiple factors
            base_score = calculate_accessibility_score(metrics)
            
            # Bonus for central locations (middle of route)
            centrality_bonus = 10 * (1 - abs(0.5 - position_ratio) * 2)
            
            # Penalty for congested segments
            congestion_penalty = 0
            avg_speed = metrics.get("avg_speed", 0)
            speed_limit = metrics.get("speed_limit", 50)
            if speed_limit > 0:
                congestion_ratio = avg_speed / speed_limit
                if congestion_ratio < 0.5:
                    congestion_penalty = 20
                elif congestion_ratio < 0.7:
                    congestion_penalty = 10
            
            final_score = base_score + centrality_bonus - congestion_penalty
            final_score = max(0, min(100, final_score))
            
            shape = segment.get("shape", [])
            mid_point = shape[len(shape)//2] if shape else {"latitude": 0, "longitude": 0}
            
            segment_data = {
                "segment_id": segment.get("segmentId"),
                "street_name": metrics.get("street_name", "Unknown"),
                "latitude": mid_point.get("latitude", 0),
                "longitude": mid_point.get("longitude", 0),
                "accessibility_score": final_score,
                "base_score": base_score,
                "centrality_bonus": centrality_bonus,
                "congestion_penalty": congestion_penalty,
                "position_ratio": position_ratio,
                "avg_speed": avg_speed,
                "travel_time": metrics.get("segment_travel_time_seconds", 0)
            }
            
            segment_accessibility.append(segment_data)
            
            # Categorize into zones
            if final_score >= 90:
                accessibility_zones["excellent"].append(segment_data)
            elif final_score >= 70:
                accessibility_zones["good"].append(segment_data)
            elif final_score >= 50:
                accessibility_zones["moderate"].append(segment_data)
            else:
                accessibility_zones["poor"].append(segment_data)
    
    # Calculate zone statistics
    zone_stats = {}
    for zone, segments in accessibility_zones.items():
        if segments:
            zone_stats[zone] = {
                "count": len(segments),
                "avg_score": sum(s["accessibility_score"] for s in segments) / len(segments),
                "avg_speed": sum(s["avg_speed"] for s in segments) / len(segments),
                "streets": list(set(s["street_name"] for s in segments))
            }
        else:
            zone_stats[zone] = {"count": 0, "avg_score": 0, "avg_speed": 0, "streets": []}
    
    return {
        "zones": accessibility_zones,
        "zone_statistics": zone_stats,
        "all_segments": segment_accessibility,
        "route_total_distance": total_distance,
        "route_avg_travel_time": total_avg_time
    }


def print_accessibility_map_results(results: Dict):
    """Print accessibility scoring results"""
    print("\n" + "=" * 80)
    print("ANALYSIS 2: ACCESSIBILITY SCORING MAP")
    print("=" * 80)
    
    if not results:
        print("No accessibility data available.")
        return
    
    stats = results["zone_statistics"]
    total_segments = sum(s["count"] for s in stats.values())
    
    print(f"\n  Route Overview:")
    print(f"    Total Distance: {results['route_total_distance']:.0f} meters")
    print(f"    Avg Travel Time: {results['route_avg_travel_time']:.1f} seconds")
    print(f"    Total Segments Analyzed: {total_segments}")
    
    print(f"\n  Accessibility Zone Distribution:")
    print(f"  {'Zone':<12} {'Segments':<10} {'%':<8} {'Avg Score':<12} {'Avg Speed':<10}")
    print("  " + "-" * 55)
    
    zone_colors = {
        "excellent": "🟢",
        "good": "🟡", 
        "moderate": "🟠",
        "poor": "🔴"
    }
    
    for zone in ["excellent", "good", "moderate", "poor"]:
        stat = stats[zone]
        pct = (stat["count"] / total_segments * 100) if total_segments > 0 else 0
        print(f"  {zone_colors[zone]} {zone.capitalize():<10} {stat['count']:<10} {pct:>5.1f}%  "
              f"{stat['avg_score']:>10.1f}  {stat['avg_speed']:>8.1f}")
    
    print(f"\n  Excellent Accessibility Areas (Score >= 90):")
    if stats["excellent"]["streets"]:
        for street in stats["excellent"]["streets"][:5]:
            print(f"    • {street}")
    else:
        print("    None identified")
    
    print(f"\n  Poor Accessibility Areas (Score < 50):")
    if stats["poor"]["streets"]:
        for street in stats["poor"]["streets"][:5]:
            print(f"    • {street}")
    else:
        print("    None identified - all areas have acceptable accessibility")


# ============================================================================
# ANALYSIS TYPE 3: RETAIL/BUSINESS SITE SELECTION
# ============================================================================

@dataclass
class RetailSite:
    """Represents a potential retail/business site"""
    name: str
    latitude: float
    longitude: float
    
    # Traffic exposure metrics
    daily_traffic_exposure: float = 0.0
    peak_hour_exposure: float = 0.0
    
    # Accessibility for customers
    customer_accessibility: float = 0.0
    parking_potential: float = 0.0
    
    # Visibility & prominence
    road_visibility: float = 0.0
    intersection_proximity: float = 0.0
    
    # Road classification
    primary_frc: int = 8
    frc_coverage: float = 0.0  # Percentage of FRC coverage from probe data (0-100)

    # Business suitability scores
    retail_score: float = 0.0
    restaurant_score: float = 0.0
    office_score: float = 0.0
    
    # Overall commercial score
    commercial_score: float = 0.0


def evaluate_retail_potential(data: Dict, aadt_map: Dict[int, int] = None) -> List[RetailSite]:
    """
    Evaluate each location for retail/business site potential.
    Considers traffic exposure, visibility, and accessibility.
    """
    if aadt_map is None:
        aadt_map = {}
    
    retail_sites = []
    
    # Pre-compute max sampleSize across all segments for FRC coverage fallback
    max_sample_size = 0
    for route in data.get("routes", []):
        for seg in route.get("segmentResults", []):
            tr = (seg.get("segmentTimeResults") or [{}])[0]
            max_sample_size = max(max_sample_size, float(tr.get("sampleSize", 0) or 0))
    
    for route in data.get("routes", []):
        segments = route.get("segmentResults", [])
        
        for i, segment in enumerate(segments):
            metrics = calculate_segment_metrics(segment)
            if not metrics:
                continue
            
            shape = segment.get("shape", [])
            if not shape:
                continue
            
            mid_point = shape[len(shape)//2]
            
            site = RetailSite(
                name=metrics.get("street_name", "Unknown"),
                latitude=mid_point["latitude"],
                longitude=mid_point["longitude"]
            )
            
            # Traffic Exposure Score (based on AADT data if available)
            seg_id = segment.get("segmentId", 0)
            if seg_id in aadt_map:
                # Use actual AADT traffic volume data
                aadt = aadt_map[seg_id]
                site.daily_traffic_exposure = calculate_traffic_exposure_score(aadt)
            else:
                # Fallback to sample size as proxy if AADT not available
                time_results = segment.get("segmentTimeResults", [])
                if time_results:
                    sample_size = time_results[0].get("sampleSize", 0)
                    # Normalize to 0-100 scale (assuming max of 500k samples is excellent)
                    site.daily_traffic_exposure = min(100, sample_size / 5000)
                else:
                    site.daily_traffic_exposure = 0
            
            # Customer Accessibility (inverse of travel time)
            segment_time_seconds = metrics.get("segment_travel_time_seconds", 60)
            site.customer_accessibility = max(0, 100 - (segment_time_seconds * 0.5))
            
            # Road Visibility (based on FRC - lower FRC = major road = more visible)
            frc = metrics.get("frc", 8)
            site.road_visibility = (8 - frc) * 12.5  # FRC 0 = 100, FRC 8 = 0

            # Store FRC info (from route provider)
            site.primary_frc = frc
            
            # Store FRC coverage (normalizedSampleSize is 0-1, convert to percentage)
            normalized_coverage = metrics.get("normalized_sample_size", 0)
            if normalized_coverage > 0:
                site.frc_coverage = normalized_coverage * 100
            else:
                # Fallback for ArcGIS data: use sampleSize relative to a baseline
                sample_size = metrics.get("sample_size", 0)
                site.frc_coverage = min(100.0, (sample_size / max(1, max_sample_size)) * 100.0) if sample_size > 0 else 0.0
            
            # Intersection Proximity (bonus if near start/end of segment or between segments)
            is_start = (i == 0)
            is_end = (i == len(segments) - 1)
            site.intersection_proximity = 70 if (is_start or is_end) else 50
            
            # Parking Potential (lower speed areas often have more parking)
            avg_speed = metrics.get("avg_speed", 30)
            if avg_speed < 20:
                site.parking_potential = 90  # Slow area = likely parking available
            elif avg_speed < 30:
                site.parking_potential = 70
            elif avg_speed < 40:
                site.parking_potential = 50
            else:
                site.parking_potential = 30  # Fast road = less parking
            
            # Calculate business-specific scores
            
            # Retail Score: High traffic + good visibility + parking
            site.retail_score = (
                site.daily_traffic_exposure * 0.35 +
                site.road_visibility * 0.25 +
                site.parking_potential * 0.25 +
                site.customer_accessibility * 0.15
            )
            
            # Restaurant Score: Accessibility + parking + moderate traffic
            site.restaurant_score = (
                site.customer_accessibility * 0.30 +
                site.parking_potential * 0.30 +
                site.daily_traffic_exposure * 0.20 +
                site.intersection_proximity * 0.20
            )
            
            # Office Score: Accessibility + reliability (commute predictability)
            reliability = calculate_traffic_reliability(metrics)
            site.office_score = (
                site.customer_accessibility * 0.35 +
                reliability * 0.35 +
                site.road_visibility * 0.20 +
                site.intersection_proximity * 0.10
            )
            
            # Overall Commercial Score
            # Weighted average of business-type specific scores
            site.commercial_score = (
                site.retail_score * 0.35 +
                site.restaurant_score * 0.35 +
                site.office_score * 0.30
            )
            
            retail_sites.append(site)
    
    # Remove duplicates (same street name) keeping highest score
    unique_sites = {}
    for site in retail_sites:
        if site.name not in unique_sites or site.commercial_score > unique_sites[site.name].commercial_score:
            unique_sites[site.name] = site
    
    # Sort by commercial score
    return sorted(unique_sites.values(), key=lambda s: s.commercial_score, reverse=True)


def print_retail_analysis_results(sites: List[RetailSite]):
    """Print retail/business site selection results"""
    print("\n" + "=" * 80)
    print("ANALYSIS 3: RETAIL/BUSINESS SITE SELECTION")
    print("=" * 80)
    
    if not sites:
        print("No retail sites analyzed.")
        return
    
    print(f"\n  Overall Commercial Potential Ranking:")
    print(f"  {'Rank':<5} {'Location':<25} {'Commercial':<10} {'Retail':<8} {'Restaurant':<10} {'Office':<8}")
    print("  " + "-" * 70)
    
    for i, site in enumerate(sites[:10], 1):
        print(f"  {i:<5} {site.name[:24]:<25} {site.commercial_score:>8.1f}  "
              f"{site.retail_score:>6.1f}  {site.restaurant_score:>8.1f}  {site.office_score:>6.1f}")
    
    # Best for each category
    best_retail = max(sites, key=lambda s: s.retail_score)
    best_restaurant = max(sites, key=lambda s: s.restaurant_score)
    best_office = max(sites, key=lambda s: s.office_score)
    
    print(f"\n  ★ BEST LOCATIONS BY BUSINESS TYPE ★")
    print("-" * 40)
    
    print(f"\n  🏪 Best for RETAIL:")
    print(f"     Location: {best_retail.name}")
    print(f"     Score: {best_retail.retail_score:.1f}/100")
    print(f"     Coordinates: ({best_retail.latitude:.5f}, {best_retail.longitude:.5f})")
    print(f"     Key Strengths: Traffic Exposure ({best_retail.daily_traffic_exposure:.0f}), "
          f"Visibility ({best_retail.road_visibility:.0f})")
    
    print(f"\n  🍽️  Best for RESTAURANT:")
    print(f"     Location: {best_restaurant.name}")
    print(f"     Score: {best_restaurant.restaurant_score:.1f}/100")
    print(f"     Coordinates: ({best_restaurant.latitude:.5f}, {best_restaurant.longitude:.5f})")
    print(f"     Key Strengths: Accessibility ({best_restaurant.customer_accessibility:.0f}), "
          f"Parking ({best_restaurant.parking_potential:.0f})")
    
    print(f"\n  🏢 Best for OFFICE:")
    print(f"     Location: {best_office.name}")
    print(f"     Score: {best_office.office_score:.1f}/100")
    print(f"     Coordinates: ({best_office.latitude:.5f}, {best_office.longitude:.5f})")
    print(f"     Key Strengths: Accessibility ({best_office.customer_accessibility:.0f}), "
          f"Visibility ({best_office.road_visibility:.0f})")


def export_all_results(multi_site_results: List[CandidateSite], 
                       optimal_results: Dict,
                       accessibility_results: Dict,
                       retail_sites: List[RetailSite],
                       output_file: str,
                       travel_data: Dict[str, Any] = None):
    """Export all analysis results to a comprehensive JSON file"""

    provider_metadata = (travel_data or {}).get("provider_metadata", {})
    travel_provider = provider_metadata.get("provider", "Unknown")

    data_provenance = {
        "run": {
            "travel_provider": travel_provider,
            "travel_provider_metadata": provider_metadata,
            "source_job_name": (travel_data or {}).get("jobName"),
            "source_creation_time": (travel_data or {}).get("creationTime")
        },
        "metric_sources": {
            "route_geometry_and_segment_times": travel_provider,
            "multi_site_scoring": "Derived from travel provider output",
            "optimal_location_scoring": "Derived from travel provider output",
            "accessibility_zones": "Derived from travel provider output",
            "retail_business_scoring": "Derived from travel provider output",
            "frc_classification": "From route provider (TomTom API) or speed-inferred (ArcGIS)"
        }
    }
    
    all_results = {
        "analysis_summary": {
            "total_analyses": 4,
            "analyses_performed": [
                "Multi-Site Comparison",
                "Optimal Location Along Route",
                "Accessibility Scoring Map",
                "Retail/Business Site Selection"
            ]
        },

        "data_provenance": data_provenance,
        
        "multi_site_comparison": {
            "total_sites": len(multi_site_results),
            "top_sites": [
                {
                    "rank": i,
                    "name": s.name,
                    "latitude": s.latitude,
                    "longitude": s.longitude,
                    "overall_score": round(s.overall_score, 2),
                    "accessibility_score": round(s.accessibility_score, 2),
                    "reliability_score": round(s.traffic_reliability, 2),
                    "primary_frc": s.primary_frc,
                    "frc_coverage": round(s.frc_coverage, 1),
                    "nearby_segment_time_seconds": round(s.nearby_segment_time_seconds, 1),
                    "avg_speed": round(s.avg_speed, 1)
                }
                for i, s in enumerate(multi_site_results[:10], 1)
            ]
        },
        
        "optimal_location": optimal_results["optimal_location"] if optimal_results else None,
        
        "accessibility_zones": {
            zone: {
                "count": len(segments),
                "streets": list(set(s["street_name"] for s in segments))
            }
            for zone, segments in (accessibility_results["zones"].items() if accessibility_results else {})
        },
        
        "retail_analysis": {
            "best_overall": retail_sites[0].name if retail_sites else None,
            "best_retail": max(retail_sites, key=lambda s: s.retail_score).name if retail_sites else None,
            "best_restaurant": max(retail_sites, key=lambda s: s.restaurant_score).name if retail_sites else None,
            "best_office": max(retail_sites, key=lambda s: s.office_score).name if retail_sites else None,
            "all_sites": [
                {
                    "name": s.name,
                    "latitude": s.latitude,
                    "longitude": s.longitude,
                    "commercial_score": round(s.commercial_score, 2),
                    "retail_score": round(s.retail_score, 2),
                    "restaurant_score": round(s.restaurant_score, 2),
                    "office_score": round(s.office_score, 2),
                    "primary_frc": s.primary_frc,
                    "frc_coverage": round(s.frc_coverage, 1)
                }
                for s in retail_sites
            ]
        }
    }
    
    with open(output_file, 'w') as f:
        json.dump(all_results, f, indent=2)
    
    print(f"\nAll results exported to: {output_file}")


def main():
    """Main function to run all site selection analyses"""
    
    # Load travel time data - accept command line argument or use default
    input_file = sys.argv[1] if len(sys.argv) > 1 else str(BASE_DIR / "latest_travel_time.json")
    print(f"Loading travel time data from: {input_file}")
    
    try:
        data = load_travel_time_data(input_file)
    except FileNotFoundError:
        print(f"Error: File not found: {input_file}")
        return
    
    print(f"Loaded data for: {data.get('jobName', 'Unknown')}")
    
    # Check provider mode
    provider_metadata = data.get("provider_metadata", {})
    provider = provider_metadata.get("provider", "Unknown")
    
    # Load AADT traffic volume data (TomTom mode only)
    # AADT is exclusive TomTom data - not available in ArcGIS mode
    aadt_map = {}
    if provider == "TomTom":
        print("\nLoading AADT traffic volume data (TomTom exclusive)...")
        aadt_map = load_aadt_data()
    else:
        print(f"\n✓ Running in {provider} mode - AADT data skipped (TomTom exclusive)")
    
    # ========================================
    # ANALYSIS 1: Multi-Site Comparison
    # ========================================
    print("\n" + "=" * 80)
    print("RUNNING ALL SITE SELECTION ANALYSES")
    print("=" * 80)
    
    print("\n[1/4] Running Multi-Site Comparison...")
    sites = define_candidate_sites_from_route(data)
    for i, site in enumerate(sites):
        sites[i] = evaluate_site(data, site, aadt_map)
    ranked_sites = rank_sites(sites)
    print_site_comparison(ranked_sites)
    print_top_sites_detail(ranked_sites, top_n=5)
    
    # ========================================
    # ANALYSIS 2: Optimal Location Along Route
    # ========================================
    print("\n[2/4] Finding Optimal Location Along Route...")
    optimal_results = find_optimal_location(data)
    print_optimal_location_results(optimal_results)
    
    # ========================================
    # ANALYSIS 3: Accessibility Scoring
    # ========================================
    print("\n[3/4] Calculating Accessibility Scores...")
    accessibility_results = calculate_accessibility_map(data)
    print_accessibility_map_results(accessibility_results)
    
    # ========================================
    # ANALYSIS 4: Retail/Business Site Selection
    # ========================================
    print("\n[4/4] Evaluating Retail/Business Potential...")
    retail_sites = evaluate_retail_potential(data, aadt_map)
    print_retail_analysis_results(retail_sites)
    
    # ========================================
    # Export All Results
    # ========================================
    output_file = str(BASE_DIR / "comprehensive_site_selection_results.json")
    export_all_results(
        ranked_sites, 
        optimal_results, 
        accessibility_results, 
        retail_sites,
        output_file,
        data
    )
    
    # Also save to latest file for automated workflow
    latest_file = str(BASE_DIR / "latest_site_selection.json")
    export_all_results(
        ranked_sites, 
        optimal_results, 
        accessibility_results, 
        retail_sites,
        latest_file,
        data
    )
    
    print("\n" + "=" * 80)
    print("ALL SITE SELECTION ANALYSES COMPLETE!")
    print("=" * 80)
    print("\nAnalyses performed:")
    print("  1. Multi-Site Comparison - Compare 20 locations by accessibility & reliability")
    print("  2. Optimal Location - Find single best point along route")
    print("  3. Accessibility Scoring - Map accessibility zones along route")
    print("  4. Retail/Business Selection - Evaluate commercial potential by business type")


if __name__ == "__main__":
    main()
