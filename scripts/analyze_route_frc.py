#!/usr/bin/env python3
"""Analyze FRC distribution on route and estimate AADT"""
import argparse
import glob
import json
import sys

parser = argparse.ArgumentParser(description="Analyze FRC distribution on a route")
parser.add_argument("coverage_file", nargs="?", default=None,
                    help="Path to a *_cvg_aadt.json coverage file. "
                         "If omitted, the first match under sample_data/ is used.")
args = parser.parse_args()

# Load the travel time data
with open('latest_travel_time.json', 'r') as f:
    data = json.load(f)

# Resolve coverage file
coverage_path = args.coverage_file
if not coverage_path:
    matches = glob.glob("sample_data/**/*_cvg_aadt.json", recursive=True)
    if not matches:
        sys.exit("Error: no *_cvg_aadt.json file found under sample_data/")
    coverage_path = matches[0]

# Load coverage data
with open(coverage_path, 'r') as f:
    coverage = json.load(f)

# Analyze FRC distribution on the route
frc_stats = {}
total_distance = 0

for route in data.get("routes", []):
    for segment in route.get("segmentResults", []):
        frc = segment.get("frc", 8)
        distance = segment.get("distance", 0)
        
        if frc not in frc_stats:
            frc_stats[frc] = {"count": 0, "total_distance": 0}
        
        frc_stats[frc]["count"] += 1
        frc_stats[frc]["total_distance"] += distance
        total_distance += distance

print("=" * 70)
print("ROUTE - FRC DISTRIBUTION")
print("=" * 70)
print(f"Route: {data.get('jobName', 'Unknown')}")
print(f"Total Distance: {total_distance:.1f} meters ({total_distance/1000:.2f} km)")
print()

print("FRC Breakdown:")
print("-" * 70)
for frc in sorted(frc_stats.keys()):
    stats = frc_stats[frc]
    pct = (stats["total_distance"] / total_distance * 100) if total_distance > 0 else 0
    
    # Get coverage data for this FRC
    frc_key = f"FRC{frc}"
    cov_data = coverage.get(frc_key, {})
    total_len = cov_data.get("totalLength", 0)
    covered_len = cov_data.get("coveredLength", 0)
    coverage_pct = (covered_len / total_len * 100) if total_len > 0 else 0
    
    print(f"  FRC {frc}: {stats['count']:2d} segments, {stats['total_distance']:7.1f}m ({pct:5.1f}%)")
    print(f"          CA-wide coverage: {coverage_pct:.1f}%")

print("=" * 70)
print()

# Typical AADT ranges by FRC (industry estimates for California)
typical_aadt = {
    0: 50000,  # Motorway/Interstate (very high volume)
    1: 35000,  # Major Highway (high volume)
    2: 25000,  # Principal Arterial (high volume)
    3: 18000,  # Minor Arterial (moderate-high volume)
    4: 12000,  # Major Collector (moderate volume)
    5: 8000,   # Minor Collector (moderate-low volume)
    6: 5000,   # Local Road (low volume)
    7: 2000,   # Very Local (very low volume)
    8: 500,    # Other/Unknown
    9: 500,    # Other/Unknown
}

print("ESTIMATED AADT CALCULATION (Using Typical Values)")
print("=" * 70)
print("Note: Coverage file does NOT contain AADT data, only data availability.")
print("Using industry-typical AADT values for California roads by FRC class.")
print("-" * 70)

weighted_aadt = 0
for frc in sorted(frc_stats.keys()):
    stats = frc_stats[frc]
    distance = stats["total_distance"]
    weight = distance / total_distance if total_distance > 0 else 0
    estimated_aadt = typical_aadt.get(frc, 500)
    
    weighted_aadt += estimated_aadt * weight
    
    print(f"  FRC {frc}: ~{estimated_aadt:,} vehicles/day × {weight:.1%} = {estimated_aadt * weight:,.0f}")

print("-" * 70)
print(f"Estimated Route Average AADT: ~{weighted_aadt:,.0f} vehicles/day")
print("=" * 70)
print()
print("⚠️  IMPORTANT:")
print("   This is an APPROXIMATION based on typical FRC values, NOT actual data.")
print("   The coverage file only shows what % of roads have data, not traffic counts.")
print("   For accurate AADT, you need segment-level vehicle count data.")
