#!/usr/bin/env python3
"""Read DBF header to get field definitions without loading all data"""
import struct

def read_dbf_header(filename):
    """Read DBF file header to get field definitions"""
    with open(filename, 'rb') as f:
        # Read main header (32 bytes)
        header = f.read(32)
        version = struct.unpack('B', header[0:1])[0]
        year = struct.unpack('B', header[1:2])[0] + 1900
        month = struct.unpack('B', header[2:3])[0]
        day = struct.unpack('B', header[3:4])[0]
        num_records = struct.unpack('<I', header[4:8])[0]
        header_length = struct.unpack('<H', header[8:10])[0]
        record_length = struct.unpack('<H', header[10:12])[0]
        
        print(f"DBF Version: {version}")
        print(f"Last updated: {year}-{month:02d}-{day:02d}")
        print(f"Number of records: {num_records:,}")
        print(f"Header length: {header_length} bytes")
        print(f"Record length: {record_length} bytes")
        print()
        
        # Read field descriptors (32 bytes each)
        num_fields = (header_length - 33) // 32
        print(f"Number of fields: {num_fields}")
        print("=" * 80)
        print(f"{'#':<4} {'Field Name':<15} {'Type':<6} {'Length':<8} {'Decimals':<10}")
        print("=" * 80)
        
        fields = []
        for i in range(num_fields):
            field_desc = f.read(32)
            field_name = field_desc[0:11].split(b'\x00')[0].decode('ascii')
            field_type = field_desc[11:12].decode('ascii')
            field_length = struct.unpack('B', field_desc[16:17])[0]
            decimal_count = struct.unpack('B', field_desc[17:18])[0]
            
            print(f"{i+1:<4} {field_name:<15} {field_type:<6} {field_length:<8} {decimal_count:<10}")
            fields.append({
                'name': field_name,
                'type': field_type,
                'length': field_length,
                'decimals': decimal_count
            })
        
        return {
            'num_records': num_records,
            'fields': fields,
            'record_length': record_length
        }

print("=" * 80)
print("AADT DBF FILE STRUCTURE")
print("=" * 80)
print()

info = read_dbf_header('aadt.dbf')

print("=" * 80)
print()
print(f"🔍 Key observations:")
print(f"   - Total records: {info['num_records']:,}")
print(f"   - Look for fields like: AADT, VOLUME, TRAFFIC, FRC, STREET_NAME, etc.")
