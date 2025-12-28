#!/bin/bash
#
# Setup nginx configuration based on SSL_ENABLED setting
#
# This script reads the .env file and copies the appropriate nginx configuration
# (with or without SSL) to nginx/nginx.conf
#
# Usage: ./scripts/setup-ssl.sh
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check if .env file exists
if [ ! -f .env ]; then
    echo -e "${RED}Error: .env file not found${NC}"
    echo "Please copy .env.example to .env and configure it:"
    echo "  cp .env.example .env"
    exit 1
fi

# Load SSL_ENABLED from .env file
SSL_ENABLED=$(grep -E "^SSL_ENABLED=" .env | cut -d '=' -f2 | tr -d ' "' | tr '[:upper:]' '[:lower:]')

# Default to false if not set
SSL_ENABLED=${SSL_ENABLED:-false}

echo -e "${BLUE}=== Mirror Maestro SSL Setup ===${NC}"
echo ""
echo "SSL_ENABLED: ${SSL_ENABLED}"
echo ""

# Create nginx directory if it doesn't exist
mkdir -p nginx

# Copy appropriate configuration
if [ "$SSL_ENABLED" = "true" ]; then
    echo -e "${YELLOW}Configuring nginx with SSL/TLS support...${NC}"

    # Check if SSL certificates exist
    if [ ! -f ssl/cert.pem ] || [ ! -f ssl/key.pem ]; then
        echo -e "${RED}Error: SSL certificates not found!${NC}"
        echo ""
        echo "SSL is enabled but certificates are missing."
        echo "Please generate or provide SSL certificates:"
        echo ""
        echo "Option 1 - Generate self-signed certificate (development only):"
        echo "  ./scripts/generate-self-signed-cert.sh"
        echo ""
        echo "Option 2 - Use existing certificates:"
        echo "  mkdir -p ssl"
        echo "  cp /path/to/your/cert.pem ssl/cert.pem"
        echo "  cp /path/to/your/key.pem ssl/key.pem"
        echo ""
        exit 1
    fi

    # Copy SSL configuration
    cp nginx/templates/default-ssl.conf.template nginx/nginx.conf

    echo -e "${GREEN}✓ nginx configured for HTTPS${NC}"
    echo ""
    echo "SSL certificates found:"
    echo "  - ssl/cert.pem"
    echo "  - ssl/key.pem"
    echo ""
    echo "The application will be available at:"
    echo "  - https://localhost (HTTP will redirect to HTTPS)"
    echo ""
else
    echo -e "${YELLOW}Configuring nginx without SSL (HTTP only)...${NC}"

    # Copy non-SSL configuration
    cp nginx/templates/default.conf.template nginx/nginx.conf

    echo -e "${GREEN}✓ nginx configured for HTTP${NC}"
    echo ""
    echo "The application will be available at:"
    echo "  - http://localhost"
    echo ""
    echo "To enable SSL/TLS:"
    echo "  1. Generate certificates: ./scripts/generate-self-signed-cert.sh"
    echo "  2. Set SSL_ENABLED=true in .env"
    echo "  3. Run this script again: ./scripts/setup-ssl.sh"
    echo ""
fi

echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo "Next steps:"
echo "  1. Start the application: docker-compose up -d"
echo "  2. View logs: docker-compose logs -f"
echo ""
