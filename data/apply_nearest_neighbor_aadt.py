#!/usr/bin/env python3
"""
Apply AADT data to unmatched segments using nearest-neighbor matching.
For segments without AADT data, find the closest segment that has AADT data
and assign that value.
"""

import json
import math
import os
from typing import Dict, List, Tuple

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in km between two lat/lon points"""
    R = 6371  # Earth radius in km
    
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    
    return R * c

def get_segment_midpoint(segment: Dict) -> Tuple[float, float]:
    """Get the midpoint of a segment's shape"""
    shape = segment.get("shape", [])
    if not shape:
        return None, None
    
    mid_idx = len(shape) // 2
    mid_point = shape[mid_idx]
    return mid_point["latitude"], mid_point["longitude"]

def main():
    # Load travel time data to get all segments
    print("Loading travel time data...")
    _project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(_project_dir, "latest_travel_time.json"), "r") as f:
        travel_data = json.load(f)
    
    # Load existing AADT results
    print("Loading existing AADT data...")
    with open(os.path.join(_project_dir, "aadt_results.json"), "r") as f:
        aadt_data = json.load(f)
    
    # Extract matched segment IDs and their AADT values with coordinates
    matched_segments = {}
    for match in aadt_data.get("matched_segments", []):
        seg_id = match["segment_id"]
        matched_segments[seg_id] = {
            "aadt": match["aadt_match"]["aadt"],
            "lat": match["lat"],
            "lon": match["lon"],
            "street_name": match["street_name"],
            "frc": match["frc"]
        }
    
    print(f"Found {len(matched_segments)} segments with AADT data")
    
    # Process all segments from travel data
    all_segments = []
    for route in travel_data.get("routes", []):
        for segment in route.get("segmentResults", []):
            seg_id = segment.get("segmentId", 0)
            lat, lon = get_segment_midpoint(segment)
            
            if lat is None or lon is None:
                continue
            
            # Get street name and FRC
            street_name = "Unknown"
            for time_result in segment.get("segmentTimeResults", []):
                street_name = time_result.get("streetName", "Unknown")
                break
            
            frc = segment.get("frc", 8)
            distance = segment.get("distance", 0)
            
            all_segments.append({
                "segment_id": seg_id,
                "street_name": street_name,
                "frc": frc,
                "distance": distance,
                "lat": lat,
                "lon": lon
            })
    
    print(f"Total segments in route: {len(all_segments)}")
    print(f"Unmatched segments: {len(all_segments) - len(matched_segments)}")
    
    # Find nearest neighbor for unmatched segments
    nearest_neighbor_matches = []
    
    for segment in all_segments:
        seg_id = segment["segment_id"]
        
        if seg_id in matched_segments:
            # Already has AADT data
            continue
        
        # Find nearest segment with AADT data
        min_distance = float('inf')
        nearest_match = None
        
        for matched_id, matched_info in matched_segments.items():
            dist = haversine_distance(
                segment["lat"], segment["lon"],
                matched_info["lat"], matched_info["lon"]
            )
            
            if dist < min_distance:
                min_distance = dist
                nearest_match = {
                    "nearest_segment_id": matched_id,
                    "aadt": matched_info["aadt"],
                    "distance_km": dist,
                    "nearest_street": matched_info["street_name"]
                }
        
        if nearest_match:
            nearest_neighbor_matches.append({
                "segment_id": seg_id,
                "street_name": segment["street_name"],
                "frc": segment["frc"],
                "distance": segment["distance"],
                "lat": segment["lat"],
                "lon": segment["lon"],
                "aadt_nearest_neighbor": nearest_match
            })
            
            print(f"  Segment {seg_id} ({segment['street_name']}) -> "
                  f"Nearest: Segment {nearest_match['nearest_segment_id']} "
                  f"({nearest_match['nearest_street']}, AADT: {nearest_match['aadt']}, "
                  f"dist: {nearest_match['distance_km']*1000:.0f}m)")
    
    # Calculate new route average including nearest neighbor matches
    total_weighted_aadt = 0
    total_distance = 0
    
    # Add original matches
    for match in aadt_data.get("matched_segments", []):
        aadt = match["aadt_match"]["aadt"]
        distance = match["distance"]
        total_weighted_aadt += aadt * distance
        total_distance += distance
    
    # Add nearest neighbor matches
    for match in nearest_neighbor_matches:
        aadt = match["aadt_nearest_neighbor"]["aadt"]
        distance = match["distance"]
        total_weighted_aadt += aadt * distance
        total_distance += distance
    
    if total_distance > 0:
        new_route_average = total_weighted_aadt / total_distance
    else:
        new_route_average = aadt_data.get("route_average_aadt", 0)
    
    # Update AADT results
    aadt_data["route_average_aadt_with_nearest_neighbor"] = new_route_average
    aadt_data["nearest_neighbor_matches"] = nearest_neighbor_matches
    aadt_data["total_segments"] = len(all_segments)
    aadt_data["matched_segments_count"] = len(matched_segments)
    aadt_data["nearest_neighbor_count"] = len(nearest_neighbor_matches)
    
    # Save updated results
    output_file = os.path.join(_project_dir, "aadt_results.json")
    with open(output_file, "w") as f:
        json.dump(aadt_data, f, indent=2)
    
    print(f"\n✅ Updated AADT results saved to: {output_file}")
    print(f"\nSummary:")
    print(f"  Total segments: {len(all_segments)}")
    print(f"  Originally matched: {len(matched_segments)} ({len(matched_segments)/len(all_segments)*100:.1f}%)")
    print(f"  Nearest neighbor: {len(nearest_neighbor_matches)} ({len(nearest_neighbor_matches)/len(all_segments)*100:.1f}%)")
    print(f"  Total coverage: {(len(matched_segments) + len(nearest_neighbor_matches))/len(all_segments)*100:.1f}%")
    print(f"\n  Route average AADT:")
    print(f"    Original (matched only): {aadt_data.get('route_average_aadt', 0):.0f} vehicles/day")
    print(f"    With nearest neighbor: {new_route_average:.0f} vehicles/day")

if __name__ == "__main__":
    main()
