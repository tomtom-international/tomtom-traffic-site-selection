#!/usr/bin/env python3
"""Debug: Check if retail/restaurant/office markers are being created"""
import json

# Load results
with open('latest_site_selection.json') as f:
    results = json.load(f)

retail_sites = results.get("retail_analysis", {}).get("all_sites", [])

print(f"Total retail sites: {len(retail_sites)}")
print()

if not retail_sites:
    print("❌ NO RETAIL SITES FOUND - markers won't be created!")
else:
    # Find best for each category
    best_retail = max(retail_sites, key=lambda x: x.get("retail_score", 0))
    best_restaurant = max(retail_sites, key=lambda x: x.get("restaurant_score", 0))
    best_office = max(retail_sites, key=lambda x: x.get("office_score", 0))
    
    print("✅ Best markers SHOULD be created:")
    print()
    print(f"🏪 RETAIL (pink shopping cart):")
    print(f"   Location: {best_retail['name']}")
    print(f"   Original: {best_retail['latitude']:.5f}, {best_retail['longitude']:.5f}")
    print(f"   Offset NE: {best_retail['latitude']+0.0008:.5f}, {best_retail['longitude']+0.0008:.5f}")
    print(f"   Score: {best_retail['retail_score']:.1f}")
    print()
    
    print(f"🍽️ RESTAURANT (orange utensils):")
    print(f"   Location: {best_restaurant['name']}")
    print(f"   Original: {best_restaurant['latitude']:.5f}, {best_restaurant['longitude']:.5f}")
    print(f"   Offset SE: {best_restaurant['latitude']-0.0008:.5f}, {best_restaurant['longitude']+0.0008:.5f}")
    print(f"   Score: {best_restaurant['restaurant_score']:.1f}")
    print()
    
    print(f"🏢 OFFICE (gray building):")
    print(f"   Location: {best_office['name']}")
    print(f"   Original: {best_office['latitude']:.5f}, {best_office['longitude']:.5f}")
    print(f"   Offset SW: {best_office['latitude']-0.0008:.5f}, {best_office['longitude']-0.0008:.5f}")
    print(f"   Score: {best_office['office_score']:.1f}")
    print()
    
    print("Check the map layer control panel for:")
    print("  - 🏪 Best for Retail")
    print("  - 🍽️ Best for Restaurant")
    print("  - 🏢 Best for Office")
    print()
    print("These layers should be checked/enabled by default (show=True)")
