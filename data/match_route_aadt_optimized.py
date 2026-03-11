#!/usr/bin/env python3
"""Optimized AADT matching with bounding box filtering and larger search radius"""
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

def get_bounding_box(lat, lon, radius_km):
    """Calculate bounding box around point (approximation)"""
    # Rough conversion: 1 degree ≈ 111km at equator
    lat_delta = radius_km / 111.0
    lon_delta = radius_km / (111.0 * math.cos(math.radians(lat)))
    return {
        'min_lat': lat - lat_delta,
        'max_lat': lat + lat_delta,
        'min_lon': lon - lon_delta,
        'max_lon': lon + lon_delta
    }

if __name__ == "__main__":
    # Load route data
    print("=" * 80)
    print("OPTIMIZED AADT MATCHING")
    print("=" * 80)

    _PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(_PROJECT_DIR, 'latest_travel_time.json'), 'r') as f:
        route_data = json.load(f)

    # Extract route segments with coordinates
    route_segments = []
    for route in route_data.get("routes", []):
        for segment in route.get("segmentResults", []):
            shape = segment.get("shape", [])
            if shape:
                mid_idx = len(shape) // 2
                mid_point = shape[mid_idx]
                route_segments.append({
                    'segment_id': segment.get('segmentId'),
                    'street_name': segment.get('streetName'),
                    'frc': segment.get('frc'),
                    'distance': segment.get('distance'),
                    'lat': mid_point['latitude'],
                    'lon': mid_point['longitude']
                })

    print(f"\nRoute segments to match: {len(route_segments)}")

    # Calculate overall route bounding box (with buffer)
    all_lats = [seg['lat'] for seg in route_segments]
    all_lons = [seg['lon'] for seg in route_segments]
    route_bbox = {
        'min_lat': min(all_lats) - 0.01,  # ~1km buffer
        'max_lat': max(all_lats) + 0.01,
        'min_lon': min(all_lons) - 0.01,
        'max_lon': max(all_lons) + 0.01
    }

    print(f"Route bounding box: ({route_bbox['min_lat']:.4f}, {route_bbox['min_lon']:.4f}) to ({route_bbox['max_lat']:.4f}, {route_bbox['max_lon']:.4f})")

    # Open AADT shapefile
    print("\nOpening AADT shapefile...")
    _aadt_path = os.path.join(_PROJECT_DIR, 'sample_data', 'usa', 'ca', 'aadt')
    sf = shp.Reader(_aadt_path)
    print(f"Total AADT records: {len(sf):,}")

    # PHASE 1: Filter records by bounding box
    print("\nPhase 1: Filtering by bounding box...")
    filtered_records = []
    search_radius_km = 0.2  # 200m search radius (increased from 50m)

    for i, shape_rec in enumerate(sf.iterShapeRecords()):
        if (i + 1) % 500000 == 0:
            print(f"  Scanned {i+1:,} records, found {len(filtered_records)} in bbox...")
        
        # Quick bbox check using shapefile's bbox
        if hasattr(shape_rec.shape, 'bbox'):
            rec_bbox = shape_rec.shape.bbox  # [min_lon, min_lat, max_lon, max_lat]
            # Check if record bbox overlaps with route bbox
            if (rec_bbox[2] < route_bbox['min_lon'] or rec_bbox[0] > route_bbox['max_lon'] or
                rec_bbox[3] < route_bbox['min_lat'] or rec_bbox[1] > route_bbox['max_lat']):
                continue  # Outside route area, skip
        
        # If in bbox, save for detailed matching
        filtered_records.append(shape_rec)

    print(f"  ✓ Filtered to {len(filtered_records):,} records within route area")

    # PHASE 2: Match route segments to filtered AADT records
    print("\nPhase 2: Matching route segments...")
    matches = []

    for route_seg in route_segments:
        print(f"\n  Segment {route_seg['segment_id']}: {route_seg['street_name']} (FRC {route_seg['frc']})")
        
        best_match = None
        best_distance = float('inf')
        
        for shape_rec in filtered_records:
            rec_frc = shape_rec.record[1]  # field 1 is 'frc'
            
            # Only check segments with matching FRC
            if rec_frc != route_seg['frc']:
                continue
            
            # Check geometry proximity
            if hasattr(shape_rec.shape, 'points') and len(shape_rec.shape.points) > 0:
                aadt_points = shape_rec.shape.points
                mid_idx = len(aadt_points) // 2
                aadt_lon, aadt_lat = aadt_points[mid_idx]  # Note: shapefile is (lon, lat)
                
                dist = haversine_distance(route_seg['lat'], route_seg['lon'], aadt_lat, aadt_lon)
                
                if dist < search_radius_km and dist < best_distance:
                    aadt_value = shape_rec.record[5]  # field 5 is 'aadt'
                    best_distance = dist
                    best_match = {
                        'aadt_id': shape_rec.record[0],
                        'aadt': aadt_value,
                        'frc': rec_frc,
                        'distance_km': dist
                    }
        
        if best_match:
            route_seg['aadt_match'] = best_match
            matches.append(route_seg)
            print(f"    ✓ AADT = {best_match['aadt']:,} vehicles/day (match distance: {best_distance*1000:.0f}m)")
        else:
            print(f"    ✗ No match found within {search_radius_km*1000:.0f}m")

    # RESULTS
    print("\n" + "=" * 80)
    print("RESULTS")
    print("=" * 80)

    if matches:
        total_distance = sum(m['distance'] for m in matches)
        weighted_aadt = sum(m['aadt_match']['aadt'] * m['distance'] for m in matches) / total_distance if total_distance > 0 else 0
        
        print(f"\nMatched: {len(matches)} / {len(route_segments)} segments\n")
        
        print(f"{'ID':<4} {'Street Name':<30} {'FRC':<4} {'Dist(m)':<10} {'AADT':<12} {'Match(m)':<10}")
        print("-" * 80)
        for m in matches:
            aadt = m['aadt_match']['aadt']
            match_dist = m['aadt_match']['distance_km'] * 1000
            print(f"{m['segment_id']:<4} {m['street_name'][:29]:<30} {m['frc']:<4} {m['distance']:>8.0f}  {aadt:>10,}  {match_dist:>8.0f}")
        
        print("-" * 80)
        print(f"\n📊 ROUTE AVERAGE AADT: {weighted_aadt:,.0f} vehicles/day")
        print(f"   (Distance-weighted average)")
        
        # FRC breakdown
        print(f"\n📈 AADT by FRC Class:")
        frc_groups = {}
        for m in matches:
            frc = m['frc']
            if frc not in frc_groups:
                frc_groups[frc] = {'count': 0, 'total_aadt': 0, 'total_dist': 0}
            frc_groups[frc]['count'] += 1
            frc_groups[frc]['total_aadt'] += m['aadt_match']['aadt'] * m['distance']
            frc_groups[frc]['total_dist'] += m['distance']
        
        for frc in sorted(frc_groups.keys()):
            g = frc_groups[frc]
            avg_aadt = g['total_aadt'] / g['total_dist'] if g['total_dist'] > 0 else 0
            pct = (g['total_dist'] / total_distance * 100) if total_distance > 0 else 0
            print(f"   FRC {frc}: {avg_aadt:>8,.0f} vehicles/day ({g['count']} segments, {pct:.1f}% of route)")
        
        # Save results
        output_file = os.path.join(_PROJECT_DIR, 'aadt_results.json')
        output_data = {
            'route_average_aadt': weighted_aadt,
            'matched_segments': matches,
            'match_rate': len(matches) / len(route_segments),
            'frc_breakdown': {str(k): {'avg_aadt': v['total_aadt']/v['total_dist'], 
                                        'count': v['count'], 
                                        'distance_pct': v['total_dist']/total_distance*100}
                             for k, v in frc_groups.items()}
        }
        with open(output_file, 'w') as f:
            json.dump(output_data, f, indent=2)
        print(f"\n💾 Results saved to: {output_file}")
        
    else:
        print("\n⚠️  No matches found.")
        print("   Possible reasons:")
        print("   - AADT data doesn't cover the configured route area")
        print("   - Street alignment differs between datasets")
        print("   - Try increasing search_radius_km in the script")

    print("\n" + "=" * 80)
