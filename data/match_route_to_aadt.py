#!/usr/bin/env python3
"""Match route segments to AADT data"""
import json
import os
import sys
try:
    import shapefile as shp
except ImportError:
    import pyshp as shp
import math

def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two points in km"""
    R = 6371
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

# Load route data
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_PROJECT_DIR, 'latest_travel_time.json'), 'r') as f:
    route_data = json.load(f)

# Extract route segments with coordinates
route_segments = []
for route in route_data.get("routes", []):
    for segment in route.get("segmentResults", []):
        shape = segment.get("shape", [])
        if shape:
            # Get midpoint of segment
            mid_idx = len(shape) // 2
            mid_point = shape[mid_idx]
            route_segments.append({
                'segment_id': segment.get('segmentId'),
                'street_name': segment.get('streetName'),
                'frc': segment.get('frc'),
                'distance': segment.get('distance'),
                'lat': mid_point['latitude'],
                'lon': mid_point['longitude'],
                'first_point': shape[0],
                'last_point': shape[-1]
            })

print("=" * 80)
print("MATCHING ROUTE SEGMENTS TO AADT DATA")
print("=" * 80)
print(f"\nRoute segments to match: {len(route_segments)}")
print("\nSearching AADT shapefile (2.13M records)...")
print("This may take a few minutes...\n")

# Open AADT shapefile
_aadt_path = os.path.join(_PROJECT_DIR, 'sample_data', 'usa', 'ca', 'aadt')
sf = shp.Reader(_aadt_path)

# Search for matches
matches = []
search_radius_km = 0.05  # 50 meters

for route_seg in route_segments:
    print(f"Searching for: Segment {route_seg['segment_id']} - {route_seg['street_name']} (FRC {route_seg['frc']})")
    
    best_match = None
    best_distance = float('inf')
    
    # Search through AADT records (this is slow but necessary)
    for i, shape_rec in enumerate(sf.iterShapeRecords()):
        # Get FRC from AADT record
        rec_frc = shape_rec.record[1]  # field index 1 is 'frc'
        
        # Only check segments with matching FRC (optimization)
        if rec_frc != route_seg['frc']:
            continue
        
        # Check if geometry is near our route segment
        if hasattr(shape_rec.shape, 'points') and len(shape_rec.shape.points) > 0:
            # Check midpoint of AADT segment
            aadt_points = shape_rec.shape.points
            mid_idx = len(aadt_points) // 2
            aadt_lat, aadt_lon = aadt_points[mid_idx][1], aadt_points[mid_idx][0]  # Note: shapefile is (lon, lat)
            
            dist = haversine_distance(route_seg['lat'], route_seg['lon'], aadt_lat, aadt_lon)
            
            if dist < search_radius_km and dist < best_distance:
                aadt_value = shape_rec.record[5]  # field index 5 is 'aadt'
                best_distance = dist
                best_match = {
                    'aadt_index': i,
                    'aadt_id': shape_rec.record[0],
                    'aadt': aadt_value,
                    'frc': rec_frc,
                    'distance_km': dist
                }
        
        # Progress indicator every 100k records
        if (i + 1) % 100000 == 0:
            print(f"  ... searched {i+1:,} records")
    
    if best_match:
        route_seg['aadt_match'] = best_match
        matches.append(route_seg)
        print(f"  ✓ Found match! AADT = {best_match['aadt']:,} vehicles/day (distance: {best_distance*1000:.0f}m)")
    else:
        print(f"  ✗ No match found within {search_radius_km*1000:.0f}m")
    print()

print("\n" + "=" * 80)
print("RESULTS SUMMARY")
print("=" * 80)

if matches:
    total_distance = sum(m['distance'] for m in matches)
    weighted_aadt = sum(m['aadt_match']['aadt'] * m['distance'] for m in matches) / total_distance if total_distance > 0 else 0
    
    print(f"\nMatched {len(matches)} out of {len(route_segments)} segments\n")
    
    print(f"{'Seg':<4} {'Street Name':<25} {'FRC':<4} {'Distance':<10} {'AADT':<12}")
    print("-" * 80)
    for m in matches:
        aadt = m['aadt_match']['aadt']
        print(f"{m['segment_id']:<4} {m['street_name'][:24]:<25} {m['frc']:<4} {m['distance']:>8.1f}m  {aadt:>10,}")
    
    print("-" * 80)
    print(f"\n📊 ROUTE AVERAGE AADT: {weighted_aadt:,.0f} vehicles/day")
    print(f"   (Distance-weighted average across matched segments)")
else:
    print("\n⚠️  No matches found. The route segments may not overlap with AADT coverage.")
    print("   Try increasing search_radius_km in the script.")

print("\n" + "=" * 80)
