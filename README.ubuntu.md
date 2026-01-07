# Mirror Maestro - Ubuntu Edition

This directory contains Ubuntu-based Docker configurations for environments where Ubuntu package mirrors are available but Debian mirrors are not.

## Quick Start

### 1. Configure Environment

```bash
# Copy Ubuntu-specific environment template
cp .env.ubuntu.example .env

# Edit with your settings
nano .env
```

### 2. Build with Ubuntu Image

```bash
# Build using the Ubuntu Dockerfile
docker-compose -f docker-compose.ubuntu.yml build
```

### 3. Deploy

```bash
# Start services
docker-compose -f docker-compose.ubuntu.yml up -d

# View logs
docker-compose -f docker-compose.ubuntu.yml logs -f app

# Stop services
docker-compose -f docker-compose.ubuntu.yml down
```

## Differences from Debian Version

### Base Image
- **Debian**: `python:3.11-slim` (Debian 12 Bookworm)
- **Ubuntu**: `ubuntu:22.04` (Jammy Jellyfish) + Python 3.11 installed

### APT Mirror Configuration

**Ubuntu mirrors use `/ubuntu` path instead of `/debian`:**

```bash
# Ubuntu APT Mirror (this version)
APT_MIRROR=http://nexus.company.com/repository/ubuntu-proxy/ubuntu

# Debian APT Mirror (standard version)
APT_MIRROR=http://nexus.company.com/repository/debian-proxy/debian
```

The Dockerfile automatically replaces:
- `http://archive.ubuntu.com/ubuntu` → Your `APT_MIRROR` value
- `http://security.ubuntu.com/ubuntu` → Your `APT_MIRROR` value

### Package Names
All package names are identical between Debian and Ubuntu:
- ✅ `gcc` - Same
- ✅ `libpq-dev` - Same
- ✅ `postgresql-client` - Same
- ✅ `python3.11` - Same

## Nexus Configuration for Ubuntu

### Create Ubuntu APT Proxy Repository

1. **In Nexus, create a new repository**:
   - Type: `apt (proxy)`
   - Name: `ubuntu-proxy`
   - Distribution: `jammy` (Ubuntu 22.04)
   - Remote storage: `http://archive.ubuntu.com/ubuntu`
   - Flat: `false` (uncheck)

2. **Configure in `.env`**:
   ```bash
   APT_MIRROR=http://nexus.company.com/repository/ubuntu-proxy/ubuntu
   ```

### Example Enterprise Configuration

```bash
# In .env
ENVIRONMENT=production

# Secure credentials
POSTGRES_PASSWORD=your-secure-db-password
AUTH_PASSWORD=your-secure-password

# Enterprise artifact mirrors
DOCKER_REGISTRY=harbor.company.com/proxy/
APT_MIRROR=http://nexus.company.com/repository/ubuntu-proxy/ubuntu
PIP_INDEX_URL=http://nexus.company.com/repository/pypi-proxy/simple
PIP_TRUSTED_HOST=nexus.company.com
USE_LOCAL_VENDOR_ASSETS=true
```

## Switching Between Debian and Ubuntu

You can keep both versions and switch between them:

```bash
# Use Debian version (default)
docker-compose build
docker-compose up -d

# Use Ubuntu version
docker-compose -f docker-compose.ubuntu.yml build
docker-compose -f docker-compose.ubuntu.yml up -d
```

## Verification

### Verify Ubuntu Base

```bash
# Check OS version
docker-compose -f docker-compose.ubuntu.yml exec app cat /etc/os-release

# Should show:
# NAME="Ubuntu"
# VERSION="22.04.x LTS (Jammy Jellyfish)"
```

### Verify Python Version

```bash
# Check Python version
docker-compose -f docker-compose.ubuntu.yml exec app python --version

# Should show: Python 3.11.x
```

### Verify APT Mirror

During build, check for mirror configuration:

```bash
docker-compose -f docker-compose.ubuntu.yml build --no-cache app 2>&1 | grep -i "apt\|mirror"
```

## Troubleshooting

### Python 3.11 Not Found

If you get errors about Python 3.11 not being available:

**Issue**: Ubuntu 22.04 includes Python 3.10 by default, but Python 3.11 is available in the default repositories.

**Solution**: The Dockerfile installs `python3.11` package. If your mirror doesn't have it:
1. Ensure your Ubuntu mirror includes the `universe` repository
2. Or switch to Ubuntu 24.04 (Noble) which includes Python 3.11 by default:
   ```dockerfile
   FROM ${DOCKER_REGISTRY}ubuntu:24.04
   ```

### APT Update 404 Errors

**Symptoms**: `apt-get update` fails with 404 errors

**Check**:
1. Verify `APT_MIRROR` includes `/ubuntu` path
2. Ensure Nexus Ubuntu proxy is configured for `jammy` distribution
3. Check network connectivity from build container to Nexus

**Test manually**:
```bash
# Test if mirror is accessible
curl http://nexus.company.com/repository/ubuntu-proxy/ubuntu/dists/jammy/Release
```

### Package Installation Failures

If specific packages fail to install:

```bash
# Check which repositories are configured
docker-compose -f docker-compose.ubuntu.yml run --rm app cat /etc/apt/sources.list

# Manually test package installation
docker-compose -f docker-compose.ubuntu.yml run --rm app apt-cache policy python3.11
```

## Complete Documentation

For comprehensive documentation on enterprise deployment:
- [Enterprise Deployment Guide](docs/ENTERPRISE_DEPLOYMENT.md) - Detailed configuration guide
- [Main README](README.md) - General usage and features
- [CLAUDE.md](CLAUDE.md) - Developer guide

## Support

For issues specific to the Ubuntu version, check:
1. Ubuntu 22.04 compatibility
2. APT mirror path includes `/ubuntu`
3. Python 3.11 availability in your mirror

For general Mirror Maestro issues, see the main [README.md](README.md).
