import json

data = json.load(open('latest_site_selection.json'))
retail_sites = data.get('retail_analysis', {}).get('all_sites', [])

print(f"Total retail sites: {len(retail_sites)}")
print(f"\nTop 5 retail sites (before filtering):")
for i, s in enumerate(sorted(retail_sites, key=lambda x: x.get('commercial_score', 0), reverse=True)[:5], 1):
    print(f"  {i}. {s['name']} - FRC {s['primary_frc']}, Cov: {s['frc_coverage']:.1f}%, Comm: {s['commercial_score']:.1f}")

filtered = [s for s in retail_sites if s.get('name', 'Unknown') != 'Unknown']
print(f"\nFiltered retail sites: {len(filtered)} (removed {len(retail_sites) - len(filtered)})")

ranked = sorted(filtered, key=lambda x: x.get('commercial_score', 0), reverse=True)[:5]
print(f"\nTop 5 after filtering Unknown:")
for i, s in enumerate(ranked, 1):
    print(f"  {i}. {s['name']} - FRC {s['primary_frc']}, Cov: {s['frc_coverage']:.1f}%, Comm: {s['commercial_score']:.1f}")
