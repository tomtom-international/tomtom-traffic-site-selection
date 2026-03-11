#!/usr/bin/env python3
"""Read AADT shapefile data and find matching segments for a configured route"""
import json
import os

try:
    import geopandas as gpd
    import shapely
    has_geopandas = True
except ImportError:
    has_geopandas = False
    print("⚠️  geopandas not available, trying dbfread...")

if not has_geopandas:
    try:
        from dbfread import DBF
        has_dbfread = True
    except ImportError:
        has_dbfread = False
        print("⚠️  dbfread not available, trying pyshp...")

if not has_geopandas and not has_dbfread:
    try:
        import shapefile as shp
        has_pyshp = True
    except ImportError:
        has_pyshp = False
        print("❌ No shapefile libraries available")
        print("Install one of: geopandas, dbfread, or pyshp")
        exit(1)

# Load route data to get coordinates
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
route_json_path = os.path.join(_PROJECT_DIR, 'latest_travel_time.json')
with open(route_json_path, 'r') as f:
    route_data = json.load(f)

print("=" * 70)
print("READING AADT SHAPEFILE")
print("=" * 70)

# First, let's just examine the structure
if has_geopandas:
    print("Using geopandas to read shapefile...")
    # Read just the first few rows to see schema
    _aadt_shp = os.path.join(_PROJECT_DIR, 'sample_data', 'usa', 'ca', 'aadt.shp')
    gdf = gpd.read_file(_aadt_shp, rows=10)
    print(f"\nFound {len(gdf)} records (showing first 10)")
    print(f"\nColumns: {list(gdf.columns)}")
    print(f"\nFirst few records:")
    print(gdf.head())
    print(f"\nCRS: {gdf.crs}")
    
elif has_dbfread:
    print("Using dbfread to read .dbf file...")
    _aadt_dbf = os.path.join(_PROJECT_DIR, 'sample_data', 'usa', 'ca', 'aadt.dbf')
    table = DBF(_aadt_dbf, load=True)
    print(f"\nField names: {table.field_names}")
    print(f"\nFirst 5 records:")
    for i, record in enumerate(table):
        if i >= 5:
            break
        print(f"  Record {i+1}: {dict(record)}")

elif has_pyshp:
    print("Using pyshp to read shapefile...")
    _aadt_path = os.path.join(_PROJECT_DIR, 'sample_data', 'usa', 'ca', 'aadt')
    sf = shp.Reader(_aadt_path)
    print(f"\nTotal records: {len(sf)}")
    print(f"\nFields: {sf.fields}")
    print(f"\nFirst 3 records:")
    for i in range(min(3, len(sf))):
        record = sf.record(i)
        print(f"  Record {i+1}: {record}")

print("=" * 70)
