#!/usr/bin/env python3
"""Test script to verify ArcGIS POI search functionality."""

from core.enhanced_data import search_pois_arcgis

# Test coordinates (sample location)
test_lat = 33.8366
test_lon = -116.5453

print("Testing ArcGIS POI search...")
print(f"Searching near: {test_lat}, {test_lon}")
print()

# Test different radii
for radius in [500, 1000, 2000]:
    print(f"Query: 'parking', Radius: {radius}m")
    results = search_pois_arcgis(test_lat, test_lon, "parking", radius=radius, limit=10)
    print(f"  Found {len(results)} results")
    if results:
        for i, poi in enumerate(results[:3], 1):
            print(f"    {i}. {poi.get('name', 'Unknown')} - {poi.get('distance', 0):.0f}m away")
    print()

# Try different POI types to verify API works
print("="*60)
print("Testing different POI types (1000m radius):\n")

for search_term in ["restaurant", "coffee", "hotel", "gas station", "shopping"]:
    results = search_pois_arcgis(test_lat, test_lon, search_term, radius=1000, limit=5)
    print(f"'{search_term}': {len(results)} results")
    if results:
        for poi in results[:2]:
            print(f"  - {poi.get('name', 'Unknown')} ({poi.get('distance', 0):.0f}m)")
    print()

