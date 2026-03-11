#!/usr/bin/env python3
"""Quick peek at AADT data schema without loading entire file"""
import os
import shapefile as shp

print("=" * 70)
print("READING AADT SHAPEFILE SCHEMA")
print("=" * 70)

# Use pyshp which can handle large files more efficiently
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_aadt_path = os.path.join(_PROJECT_DIR, 'sample_data', 'usa', 'ca', 'aadt')
sf = shp.Reader(_aadt_path)

print(f"\nTotal records: {len(sf)}")
print(f"\nBounding box: {sf.bbox}")
print(f"\nShape type: {sf.shapeType} ({sf.shapeTypeName})")

print(f"\nField definitions:")
print("-" * 70)
for i, field in enumerate(sf.fields[1:]):  # Skip deletion flag field
    field_name, field_type, field_length, decimal_count = field[0], field[1], field[2], field[3]
    print(f"{i+1:2d}. {field_name:20s} Type: {field_type:1s}  Length: {field_length:4d}  Decimals: {decimal_count}")

print("\n" + "=" * 70)
print("SAMPLE RECORDS (First 3)")
print("=" * 70)

for i in range(min(3, len(sf))):
    shape_rec = sf.shapeRecord(i)
    print(f"\n--- Record {i+1} ---")
    print(f"Shape type: {shape_rec.shape.shapeType}")
    
    # Print field values
    for j, field in enumerate(sf.fields[1:]):
        field_name = field[0]
        value = shape_rec.record[j]
        print(f"  {field_name}: {value}")
    
    # Print first few coordinates
    if hasattr(shape_rec.shape, 'points') and len(shape_rec.shape.points) > 0:
        print(f"  Geometry: {len(shape_rec.shape.points)} points")
        print(f"  First point: {shape_rec.shape.points[0]}")
        if len(shape_rec.shape.points) > 1:
            print(f"  Last point: {shape_rec.shape.points[-1]}")

print("\n" + "=" * 70)
