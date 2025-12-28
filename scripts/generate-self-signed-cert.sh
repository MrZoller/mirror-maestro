#!/bin/bash
#
# Generate self-signed SSL certificate for development/testing
#
# Usage: ./scripts/generate-self-signed-cert.sh [hostname]
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get hostname from argument or use default
HOSTNAME=${1:-localhost}

# Create ssl directory if it doesn't exist
SSL_DIR="ssl"
mkdir -p "$SSL_DIR"

echo -e "${YELLOW}Generating self-signed SSL certificate for: ${HOSTNAME}${NC}"
echo ""

# Generate private key
echo "Generating private key..."
openssl genrsa -out "$SSL_DIR/key.pem" 2048

# Generate certificate signing request
echo "Generating certificate signing request..."
openssl req -new -key "$SSL_DIR/key.pem" -out "$SSL_DIR/csr.pem" \
    -subj "/C=US/ST=State/L=City/O=Organization/OU=Development/CN=${HOSTNAME}"

# Generate self-signed certificate (valid for 365 days)
echo "Generating self-signed certificate (valid for 365 days)..."
openssl x509 -req -days 365 -in "$SSL_DIR/csr.pem" \
    -signkey "$SSL_DIR/key.pem" -out "$SSL_DIR/cert.pem" \
    -extfile <(printf "subjectAltName=DNS:${HOSTNAME},DNS:*.${HOSTNAME},DNS:localhost,IP:127.0.0.1")

# Clean up CSR file
rm "$SSL_DIR/csr.pem"

# Set appropriate permissions
chmod 600 "$SSL_DIR/key.pem"
chmod 644 "$SSL_DIR/cert.pem"

echo ""
echo -e "${GREEN}✓ SSL certificate generated successfully!${NC}"
echo ""
echo "Certificate files created:"
echo "  - Certificate: ${SSL_DIR}/cert.pem"
echo "  - Private key: ${SSL_DIR}/key.pem"
echo ""
echo -e "${YELLOW}⚠ WARNING: This is a self-signed certificate for development only!${NC}"
echo "   Browsers will show a security warning. For production, use a certificate"
echo "   from a trusted Certificate Authority (e.g., Let's Encrypt)."
echo ""
echo "Next steps:"
echo "  1. Set SSL_ENABLED=true in your .env file"
echo "  2. Run: ./scripts/setup-ssl.sh"
echo "  3. Start the application: docker-compose up -d"
echo "  4. Access via HTTPS: https://${HOSTNAME}"
echo ""
