#!/usr/bin/env python3
"""Check coordinates of all markers to find overlaps"""
import json

data = json.load(open('latest_site_selection.json'))

# Top 5 candidate sites
msc = data.get('multi_site_comparison', {})
sites = msc.get('top_sites', [])[:5]
print('Top 5 candidate sites:')
for s in sites:
    print(f"  {s.get('name', 'N/A')}: {s.get('latitude', 0):.5f}, {s.get('longitude', 0):.5f} - Score: {s.get('overall_score', 0):.1f}")

# Optimal location
opt = data.get('optimal_location', {})
print(f"\nOptimal location:")
print(f"  Street: {opt.get('street', 'N/A')}")
print(f"  Coords: {opt.get('latitude', 0):.5f}, {opt.get('longitude', 0):.5f}")
print(f"  Score: {opt.get('composite_score', 0):.1f}")

# Best for each business type
retail = data.get('retail_analysis', {}).get('all_sites', [])
best_retail = max(retail, key=lambda x: x.get('retail_score', 0)) if retail else None
best_restaurant = max(retail, key=lambda x: x.get('restaurant_score', 0)) if retail else None
best_office = max(retail, key=lambda x: x.get('office_score', 0)) if retail else None

print(f"\nBest business location markers:")
if best_retail:
    print(f"  🏪 Retail: {best_retail['name']} at {best_retail['latitude']:.5f}, {best_retail['longitude']:.5f}")
if best_restaurant:
    print(f"  🍽️  Restaurant: {best_restaurant['name']} at {best_restaurant['latitude']:.5f}, {best_restaurant['longitude']:.5f}")
if best_office:
    print(f"  🏢 Office: {best_office['name']} at {best_office['latitude']:.5f}, {best_office['longitude']:.5f}")

# Check for overlaps
print("\nChecking for overlaps (within 0.0005 degrees ~55m):")
all_markers = []
for s in sites:
    all_markers.append(('Top Site', s['name'], s['latitude'], s['longitude']))
if opt.get('latitude', 0) != 0:
    all_markers.append(('Optimal', opt.get('street', 'N/A'), opt['latitude'], opt['longitude']))
if best_retail:
    all_markers.append(('Retail', best_retail['name'], best_retail['latitude'], best_retail['longitude']))
if best_restaurant:
    all_markers.append(('Restaurant', best_restaurant['name'], best_restaurant['latitude'], best_restaurant['longitude']))
if best_office:
    all_markers.append(('Office', best_office['name'], best_office['latitude'], best_office['longitude']))

for i, (type1, name1, lat1, lon1) in enumerate(all_markers):
    for type2, name2, lat2, lon2 in all_markers[i+1:]:
        dist_lat = abs(lat1 - lat2)
        dist_lon = abs(lon1 - lon2)
        if dist_lat < 0.0005 and dist_lon < 0.0005:
            print(f"  ⚠️  {type1} '{name1}' overlaps with {type2} '{name2}'")
            print(f"      Distance: {dist_lat:.6f}° lat, {dist_lon:.6f}° lon")
