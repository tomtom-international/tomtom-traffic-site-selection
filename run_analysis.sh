#!/bin/bash
# Automated Site Selection Workflow
# Run this script to execute the complete analysis pipeline.
#
# ► To change the route, edit route_config.py (single source of truth for
#   origin/destination, labels, timezone, etc.) then re-run this script.

set -euo pipefail

# Default to best-effort mode so TomTom 429 rate-limit errors don't abort the pipeline.
# Override with STRICT_TOMTOM_ERRORS=1 bash run_analysis.sh if you want hard failures.
export STRICT_TOMTOM_ERRORS="${STRICT_TOMTOM_ERRORS:-0}"

echo "======================================"
echo "  SITE SELECTION ANALYSIS WORKFLOW"
echo "======================================"
echo ""
echo "Provider strategy: ArcGIS first, TomTom fallback"
echo "TomTom strict mode: ${STRICT_TOMTOM_ERRORS}"
echo ""

echo "Step 1/5: Calculating travel times (ArcGIS first)..."
echo "--------------------------------------"
python -m core.travel_time
if [ $? -ne 0 ]; then
    echo "Error: Travel time analysis failed"
    exit 1
fi
echo ""

echo "Step 2/5: Matching route segments to AADT shapefile..."
echo "--------------------------------------"
python -m data.match_route_aadt_optimized
if [ $? -ne 0 ]; then
    echo "Error: AADT matching failed"
    exit 1
fi
echo ""

echo "Step 3/5: Applying nearest-neighbor AADT to unmatched segments..."
echo "--------------------------------------"
python -m data.apply_nearest_neighbor_aadt
if [ $? -ne 0 ]; then
    echo "Error: Nearest-neighbor AADT failed"
    exit 1
fi
echo ""

echo "Step 4/5: Running site selection analysis..."
echo "--------------------------------------"
python -m core.site_selection
if [ $? -ne 0 ]; then
    echo "Error: Site selection analysis failed"
    exit 1
fi
echo ""

echo "Step 5/5: Generating interactive map..."
echo "--------------------------------------"
python -m visualization.plot_site_map
if [ $? -ne 0 ]; then
    echo "Error: Map generation failed"
    exit 1
fi
echo ""

echo "======================================"
echo "  ✅ WORKFLOW COMPLETE!"
echo "======================================"
echo ""
echo "Generated files:"
echo "  📊 latest_travel_time.json    - Travel time analysis (ArcGIS/TomTom schema)"
echo "  📈 latest_site_selection.json - Site selection analysis"
echo "  🗺️  site_selection_map.html    - Interactive map"
echo ""
echo "To change the route: edit config/route_config.py and re-run ./run_analysis.sh"
echo ""
echo "Environment keys (optional/fallback):"
echo "  ARCGIS_API_KEY=MOCK_ARCGIS_API_KEY"
echo "  TOMTOM_TRAFFIC_API_KEY=..."
echo "  TOMTOM_PLACES_API_KEY=..."
echo ""
echo "Open site_selection_map.html in your browser to view the results."
