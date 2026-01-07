# Enterprise Deployment with Local Artifact Mirrors

This guide explains how to deploy Mirror Maestro in air-gapped or enterprise environments where external internet access is restricted. All dependencies can be pulled from local mirrors such as Nexus, Artifactory, or Harbor.

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Configuration](#configuration)
  - [Docker Registry Mirror](#docker-registry-mirror)
  - [APT Package Mirror](#apt-package-mirror)
  - [Python Package Index (PyPI) Mirror](#python-package-index-pypi-mirror)
  - [Frontend Vendor Assets](#frontend-vendor-assets)
- [Deployment Steps](#deployment-steps)
- [Nexus Configuration Example](#nexus-configuration-example)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)

## Overview

Mirror Maestro pulls artifacts from the following external sources:

1. **Docker Images**:
   - `python:3.11-slim` (base application image)
   - `postgres:16-alpine` (database)
   - `nginx:1.25-alpine` (reverse proxy)

2. **APT Packages** (Debian packages in the Docker build):
   - gcc, libpq-dev, postgresql-client

3. **Python Packages** (from PyPI):
   - See `requirements.txt` for the full list

4. **Frontend Assets** (CDN):
   - Chart.js (visualization library)
   - D3.js (topology graph library)

All of these can be redirected to use local mirrors.

## Prerequisites

- Local artifact repository (Nexus, Artifactory, Harbor, etc.)
- The following repositories configured in your artifact manager:
  - Docker registry proxy
  - Debian/APT repository proxy
  - PyPI repository proxy
  - Raw/generic repository (for frontend assets, optional)

## Configuration

All configuration is done via environment variables in the `.env` file.

### Docker Registry Mirror

**Public Default**: Docker Hub (`docker.io`)

**Local Mirror Setup**:

1. Configure a Docker registry proxy in your artifact manager (e.g., Nexus Docker proxy)
2. Set the `DOCKER_REGISTRY` environment variable in `.env`:

```bash
# Include trailing slash
DOCKER_REGISTRY=harbor.company.com/proxy/

# Or for Nexus
DOCKER_REGISTRY=nexus.company.com:5000/
```

This will prefix all Docker image names:
- `python:3.11-slim` → `harbor.company.com/proxy/python:3.11-slim`
- `postgres:16-alpine` → `harbor.company.com/proxy/postgres:16-alpine`
- `nginx:1.25-alpine` → `harbor.company.com/proxy/nginx:1.25-alpine`

### APT Package Mirror

**Public Default**: `http://deb.debian.org`

**Local Mirror Setup**:

1. Configure a Debian/APT repository proxy in your artifact manager
2. Set the `APT_MIRROR` environment variable in `.env`:

```bash
APT_MIRROR=http://nexus.company.com/repository/debian-proxy
```

This replaces the default Debian mirror in the container's `sources.list` during build.

### Python Package Index (PyPI) Mirror

**Public Default**: `https://pypi.org/simple`

**Local Mirror Setup**:

1. Configure a PyPI repository proxy in your artifact manager
2. Set the `PIP_INDEX_URL` environment variable in `.env`:

```bash
PIP_INDEX_URL=http://nexus.company.com/repository/pypi-proxy/simple
```

**For HTTP (non-HTTPS) mirrors**:

If your PyPI mirror doesn't use HTTPS, you'll need to add it as a trusted host:

```bash
PIP_INDEX_URL=http://nexus.company.com/repository/pypi-proxy/simple
PIP_TRUSTED_HOST=nexus.company.com
```

### Frontend Vendor Assets

**Public Default**: jsDelivr CDN

**Option 1: Use Local Copies (Air-Gapped)**

For completely air-gapped environments:

1. Download vendor assets (from a machine with internet access):

```bash
./scripts/download-vendor-assets.sh
```

This downloads:
- `app/static/vendor/chart.umd.min.js` (Chart.js)
- `app/static/vendor/d3.min.js` (D3.js)

2. Copy the `app/static/vendor` directory to your deployment

3. Enable local vendor assets in `.env`:

```bash
USE_LOCAL_VENDOR_ASSETS=true
```

**Option 2: Use Custom CDN or Proxy**

If you have a CDN proxy or raw repository:

```bash
CDN_CHARTJS_URL=http://nexus.company.com/repository/raw-proxy/chart.js/4.4.0/chart.umd.min.js
CDN_D3JS_URL=http://nexus.company.com/repository/raw-proxy/d3/7/d3.min.js
```

## Deployment Steps

### Step 1: Configure Environment

Create or update your `.env` file with your local mirror URLs:

```bash
# Copy the example
cp .env.example .env

# Edit with your local mirror configurations
nano .env
```

Example `.env` for enterprise deployment:

```bash
# Environment
ENVIRONMENT=production

# Database (use secure credentials!)
POSTGRES_USER=mirror_maestro
POSTGRES_PASSWORD=<secure-password>
POSTGRES_DB=mirror_maestro
DATABASE_URL=postgresql+asyncpg://mirror_maestro:<secure-password>@db:5432/mirror_maestro

# Authentication (use secure credentials!)
AUTH_ENABLED=true
AUTH_USERNAME=admin
AUTH_PASSWORD=<secure-password>

# Enterprise artifact mirrors
DOCKER_REGISTRY=harbor.company.com/proxy/
APT_MIRROR=http://nexus.company.com/repository/debian-proxy
PIP_INDEX_URL=http://nexus.company.com/repository/pypi-proxy/simple
PIP_TRUSTED_HOST=nexus.company.com
USE_LOCAL_VENDOR_ASSETS=true
```

### Step 2: Download Vendor Assets (if using local copies)

From a machine with internet access:

```bash
./scripts/download-vendor-assets.sh
```

Copy the generated `app/static/vendor/` directory to your deployment package.

### Step 3: Build the Application

```bash
docker-compose build
```

The build will use your configured mirrors for:
- Pulling base Docker images
- Installing APT packages
- Installing Python packages

### Step 4: Deploy

```bash
docker-compose up -d
```

### Step 5: Verify

Check that the application is running and using local mirrors:

```bash
# Check container logs
docker-compose logs app | grep -i "mirror\|proxy"

# Verify application is healthy
curl http://localhost/api/health
```

## Nexus Configuration Example

Here's how to configure Sonatype Nexus as your artifact mirror.

### 1. Docker Registry Proxy

1. **Create Docker Proxy Repository**:
   - Type: docker (proxy)
   - Name: docker-proxy
   - Remote storage: https://registry-1.docker.io
   - Docker Index: Use Docker Hub
   - HTTP port: 5000 (or your choice)

2. **Configure in `.env`**:
   ```bash
   DOCKER_REGISTRY=nexus.company.com:5000/
   ```

3. **Docker daemon configuration** (on build machine):
   ```json
   {
     "insecure-registries": ["nexus.company.com:5000"]
   }
   ```

### 2. APT Repository Proxy

1. **Create APT Proxy Repository**:
   - Type: apt (proxy)
   - Name: debian-proxy
   - Distribution: bookworm (Debian 12, for Python 3.11-slim)
   - Remote storage: http://deb.debian.org/debian

2. **Configure in `.env`**:
   ```bash
   APT_MIRROR=http://nexus.company.com/repository/debian-proxy
   ```

### 3. PyPI Repository Proxy

1. **Create PyPI Proxy Repository**:
   - Type: pypi (proxy)
   - Name: pypi-proxy
   - Remote storage: https://pypi.org

2. **Configure in `.env`**:
   ```bash
   PIP_INDEX_URL=http://nexus.company.com/repository/pypi-proxy/simple
   PIP_TRUSTED_HOST=nexus.company.com
   ```

### 4. Raw Repository for Frontend Assets (Optional)

1. **Create Raw Proxy Repository**:
   - Type: raw (proxy)
   - Name: jsdelivr-proxy
   - Remote storage: https://cdn.jsdelivr.net

2. **Configure in `.env`**:
   ```bash
   CDN_CHARTJS_URL=http://nexus.company.com/repository/jsdelivr-proxy/npm/chart.js@4.4.0/dist/chart.umd.min.js
   CDN_D3JS_URL=http://nexus.company.com/repository/jsdelivr-proxy/npm/d3@7/dist/d3.min.js
   ```

## Verification

### Verify Docker Registry

Check that Docker is pulling from your mirror:

```bash
docker-compose pull
# Should show your registry in the pull output
```

### Verify APT Mirror

During build, check the logs for APT mirror usage:

```bash
docker-compose build --no-cache app 2>&1 | grep -i "apt\|mirror"
```

### Verify PyPI Mirror

During build, check pip install output:

```bash
docker-compose build --no-cache app 2>&1 | grep -i "pypi\|index"
```

### Verify Frontend Assets

Access the application and check browser console:
- If using local assets: Should load from `/static/vendor/`
- If using custom CDN: Should load from your configured CDN URL

## Troubleshooting

### Docker Image Pull Failures

**Error**: `unauthorized: authentication required`

**Solution**: Configure Docker authentication for your private registry:

```bash
docker login nexus.company.com:5000
```

Or use Docker Compose secrets for authentication.

### APT Update Failures

**Error**: `Failed to fetch http://nexus.company.com/repository/debian-proxy/...`

**Solution**:
1. Verify APT repository is configured correctly in Nexus
2. Ensure the distribution (bookworm) matches your base image
3. Check network connectivity from build container to Nexus

### PyPI Install Failures

**Error**: `Could not find a version that satisfies the requirement...`

**Solution**:
1. Verify PyPI repository is configured correctly in Nexus
2. Check that Nexus has cached the required packages
3. Manually trigger package caching by accessing package URLs

### SSL/TLS Certificate Errors

If your Nexus uses self-signed certificates:

**For Docker Registry**:
```bash
# Add certificate to Docker daemon
sudo mkdir -p /etc/docker/certs.d/nexus.company.com:5000
sudo cp nexus-cert.crt /etc/docker/certs.d/nexus.company.com:5000/ca.crt
sudo systemctl restart docker
```

**For pip**:
```bash
# Add --trusted-host to pip install
PIP_TRUSTED_HOST=nexus.company.com
```

### Frontend Assets Not Loading

**Symptoms**: Topology or dashboard charts not displaying

**Check**:
1. Browser console for 404 errors
2. Verify `USE_LOCAL_VENDOR_ASSETS=true` if using local copies
3. Ensure vendor files exist in `app/static/vendor/`
4. Check CDN URLs if using custom CDN

## Best Practices

1. **Test in Staging First**: Set up a staging environment to test mirror configuration before production deployment

2. **Monitor Nexus Storage**: Ensure adequate storage for caching artifacts

3. **Regular Cache Updates**: Periodically update cached artifacts in Nexus to get security patches

4. **Network Segmentation**: Ensure build/deployment machines can access Nexus, even in air-gapped environments

5. **Documentation**: Document your specific mirror URLs and configurations for your team

6. **Backup Configuration**: Keep `.env` configuration in secure version control (with secrets redacted)

7. **Certificate Management**: If using HTTPS mirrors, ensure certificates are up-to-date and properly distributed

## Support

For issues specific to Mirror Maestro configuration, see the main [README.md](../README.md) and [CLAUDE.md](../CLAUDE.md).

For artifact repository configuration:
- Nexus: https://help.sonatype.com/repomanager3
- Artifactory: https://www.jfrog.com/confluence/display/JFROG/JFrog+Artifactory
- Harbor: https://goharbor.io/docs/
