#!/bin/bash
# Script to download vendor assets for offline/air-gapped deployments
# Run this script to download Chart.js and D3.js to the local vendor directory

set -e

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Vendor directory
VENDOR_DIR="app/static/vendor"

echo -e "${GREEN}Downloading vendor assets for offline deployment...${NC}"
echo

# Create vendor directory if it doesn't exist
mkdir -p "$VENDOR_DIR"

# Download Chart.js
echo -e "${YELLOW}Downloading Chart.js...${NC}"
CHARTJS_URL="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
curl -L -o "$VENDOR_DIR/chart.umd.min.js" "$CHARTJS_URL"
echo -e "${GREEN}✓ Chart.js downloaded${NC}"
echo

# Download D3.js
echo -e "${YELLOW}Downloading D3.js...${NC}"
D3JS_URL="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"
curl -L -o "$VENDOR_DIR/d3.min.js" "$D3JS_URL"
echo -e "${GREEN}✓ D3.js downloaded${NC}"
echo

# Print summary
echo -e "${GREEN}Vendor assets downloaded successfully!${NC}"
echo
echo "Downloaded files:"
ls -lh "$VENDOR_DIR"
echo
echo "To use local vendor assets instead of CDN:"
echo "1. Add to your .env file: USE_LOCAL_VENDOR_ASSETS=true"
echo "2. Restart the application"
echo
echo -e "${YELLOW}Note:${NC} These files are not tracked by git (.gitignore)."
echo "For air-gapped deployments, copy the vendor directory to your deployment."
