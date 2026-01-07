# GitLab CI/CD Guide

This guide explains how to use GitLab CI/CD pipelines with Mirror Maestro, including how it coexists with GitHub Actions.

## Overview

Mirror Maestro supports both **GitHub Actions** and **GitLab CI/CD** pipelines:

- **GitHub Actions**: Configured in `.github/workflows/*.yml`
- **GitLab CI/CD**: Configured in `.gitlab-ci.yml`

Both systems can coexist peacefully:
- GitHub ignores `.gitlab-ci.yml`
- GitLab ignores `.github/workflows/` directory

This allows you to mirror your GitHub repository to GitLab and build/test in both environments without conflicts.

## Pipeline Stages

The GitLab CI/CD pipeline mirrors the functionality of GitHub Actions with three stages:

### 1. Test Stage

**Automatic tests** run on every push and merge request:

- `test:python-3.11` - Run pytest on Python 3.11
- `test:python-3.12` - Run pytest on Python 3.12

**Manual E2E tests** against live GitLab instances:

- `test:e2e-live` - End-to-end tests (manual trigger)

### 2. Build Stage

**Docker image build** (only on version tags):

- `build:docker` - Build multi-architecture images (amd64, arm64) and push to GitLab Container Registry

### 3. Release Stage

**GitLab release creation** (only on version tags):

- `release:gitlab` - Create release with auto-generated changelog

## Setup Instructions

### 1. Mirror GitHub Repository to GitLab

If you're hosting the primary repository on GitHub:

```bash
# On your GitLab instance, create a new project
# Settings > Repository > Mirroring repositories
# Add GitHub repository URL
# Use a GitHub personal access token for authentication
```

Or use Mirror Maestro itself to create the mirror! 🎉

### 2. Configure CI/CD Variables (for E2E Tests)

For E2E tests to work, configure these variables in GitLab:

**Settings > CI/CD > Variables**:

| Variable | Type | Protected | Masked | Description |
|----------|------|-----------|--------|-------------|
| `E2E_GITLAB_URL` | Variable | Yes | No | GitLab instance 1 URL (e.g., `https://gitlab.com`) |
| `E2E_GITLAB_GROUP_PATH` | Variable | Yes | No | GitLab instance 1 group path (e.g., `mygroup/subgroup`) |
| `E2E_GITLAB_TOKEN` | Variable | Yes | Yes | GitLab instance 1 access token (scope: `api`) |
| `E2E_GITLAB_URL_2` | Variable | Yes | No | GitLab instance 2 URL (optional, for dual-instance tests) |
| `E2E_GITLAB_GROUP_PATH_2` | Variable | Yes | No | GitLab instance 2 group path (optional) |
| `E2E_GITLAB_TOKEN_2` | Variable | Yes | Yes | GitLab instance 2 access token (optional) |
| `E2E_GITLAB_HTTP_USERNAME` | Variable | No | No | HTTP username for PAT auth (default: `oauth2`) |
| `E2E_GITLAB_MIRROR_TIMEOUT_S` | Variable | No | No | Seconds to wait for mirror sync (default: `120`) |
| `E2E_TEST_SCOPE` | Variable | No | No | Test scope: `single`, `dual`, `multi-project`, `multi-group`, `all` |

**Note**: Protected variables are only available on protected branches/tags.

### 3. Enable Container Registry (Optional)

If your GitLab instance has the Container Registry disabled:

**Settings > General > Visibility, project features, permissions**:
- Enable "Container Registry"

The pipeline will push Docker images to `$CI_REGISTRY_IMAGE` (e.g., `registry.gitlab.com/yourname/mirror-maestro`).

### 4. Air-Gapped / Restricted Network Configuration

If your GitLab runners **cannot access the internet**, you need to configure local mirrors for all external dependencies. The pipeline supports this through CI/CD variables.

#### Required Mirrors

The pipeline needs to pull from these external sources:

1. **Docker Images** (for CI jobs):
   - `python:3.11-alpine`, `python:3.12-alpine` (Alpine-based Python images)
   - `docker:24`, `docker:24-dind` (Alpine-based Docker images)
   - `registry.gitlab.com/gitlab-org/release-cli:latest` (Alpine-based)

2. **Python Packages** (for pip install):
   - All packages from `requirements.txt` and `requirements-dev.txt`

3. **Docker Build Dependencies** (during image build):
   - Ubuntu base image (`ubuntu:22.04`)
   - APT packages (python3.11, gcc, libpq-dev, etc.)
   - Python packages (from PyPI)

**Important**: The pipeline uses **Alpine-based images** for CI jobs (test/build/release) to minimize mirror requirements. Only the application build uses Ubuntu. This means you need:

- **Alpine APK mirrors** (for CI job images: python, docker, release-cli)
- **Ubuntu APT mirrors** (for application Dockerfile build only)
- **PyPI mirrors** (for Python packages)

**Alternative**: If you prefer to use Debian/Ubuntu for CI jobs (e.g., for better compatibility), set the `CI_PYTHON_IMAGE` variable to `python:3.11-slim` or `python:3.12-slim`. This will require Debian APT mirrors instead of Alpine APK mirrors.

#### Configure Local Mirrors

**Settings > CI/CD > Variables** - Add these variables:

| Variable | Example Value | Description |
|----------|---------------|-------------|
| **CI Job Configuration** | | |
| `CI_DOCKER_REGISTRY` | `harbor.company.com/proxy/` | Docker registry mirror for CI job images (include trailing slash) |
| `CI_PIP_INDEX_URL` | `http://nexus.company.com/repository/pypi-proxy/simple` | PyPI mirror for pip install in CI jobs |
| `CI_PIP_TRUSTED_HOST` | `nexus.company.com` | Trusted host for HTTP PyPI mirror (no `http://` prefix) |
| `CI_RELEASE_CLI_IMAGE` | `harbor.company.com/gitlab-org/release-cli:latest` | Local mirror of GitLab release-cli image |
| `CI_PYTHON_IMAGE` | `python:3.11-alpine` | Python image variant (default: `python:3.11-alpine`; use `python:3.11-slim` for Debian) |
| **Docker Build Configuration** | | |
| `DOCKER_REGISTRY` | `harbor.company.com/proxy/` | Docker registry for base images in Dockerfile (include trailing slash) |
| `APT_MIRROR` | `http://nexus.company.com/repository/ubuntu-proxy/ubuntu` | Ubuntu APT mirror (must include `/ubuntu` path) |
| `PIP_INDEX_URL` | `http://nexus.company.com/repository/pypi-proxy/simple` | PyPI mirror for pip install during Docker build |
| `PIP_TRUSTED_HOST` | `nexus.company.com` | Trusted host for HTTP PyPI mirror |

**Important Notes**:
- `CI_*` variables configure the CI jobs (test/build/release stages)
- Non-prefixed variables (`DOCKER_REGISTRY`, `APT_MIRROR`, etc.) are passed to the Dockerfile build
- All variables are **optional** - if not set, defaults to public registries/mirrors
- Docker registry variables should include trailing slash: `harbor.company.com/proxy/`
- APT mirror must include full path: `http://nexus.company.com/repository/ubuntu-proxy/ubuntu`

**Alpine APK Packages**: The CI jobs install a few Alpine packages (`gcc`, `musl-dev`, `libffi-dev`) via `apk add`. For air-gapped environments, you need either:
1. **Caching HTTP proxy** for `dl-cdn.alpinelinux.org` (simplest)
2. **Local Alpine mirror** (set `/etc/apk/repositories` in custom image)
3. **Pre-baked Docker images** with packages already installed (most secure)

If Alpine APK access is problematic, use `CI_PYTHON_IMAGE=python:3.11-slim` to switch to Debian-based images (requires Debian APT mirrors instead).

#### Example: Nexus Configuration

**1. Create Nexus Repositories**:

```bash
# Docker proxy repository
Type: docker (proxy)
Name: docker-proxy
Remote URL: https://registry-1.docker.io
Docker Index: Use Docker Hub

# PyPI proxy repository
Type: pypi (proxy)
Name: pypi-proxy
Remote URL: https://pypi.org

# APT proxy repository
Type: apt (proxy)
Name: ubuntu-proxy
Distribution: jammy
Remote URL: http://archive.ubuntu.com/ubuntu

# GitLab Container Registry proxy (optional, for release-cli)
Type: docker (proxy)
Name: gitlab-registry-proxy
Remote URL: https://registry.gitlab.com
```

**2. Set GitLab CI/CD Variables**:

```bash
CI_DOCKER_REGISTRY=nexus.company.com:5000/docker-proxy/
CI_PIP_INDEX_URL=http://nexus.company.com:8081/repository/pypi-proxy/simple
CI_PIP_TRUSTED_HOST=nexus.company.com
CI_RELEASE_CLI_IMAGE=nexus.company.com:5000/gitlab-registry-proxy/gitlab-org/release-cli:latest

DOCKER_REGISTRY=nexus.company.com:5000/docker-proxy/
APT_MIRROR=http://nexus.company.com:8081/repository/ubuntu-proxy/ubuntu
PIP_INDEX_URL=http://nexus.company.com:8081/repository/pypi-proxy/simple
PIP_TRUSTED_HOST=nexus.company.com
```

**3. Pre-populate Mirrors** (from internet-connected machine):

```bash
# Pull Docker images through Nexus
docker pull nexus.company.com:5000/docker-proxy/python:3.11-alpine
docker pull nexus.company.com:5000/docker-proxy/python:3.12-alpine
docker pull nexus.company.com:5000/docker-proxy/docker:24
docker pull nexus.company.com:5000/docker-proxy/docker:24-dind
docker pull nexus.company.com:5000/docker-proxy/ubuntu:22.04

# Mirror GitLab release-cli (if using separate GitLab registry proxy)
docker pull nexus.company.com:5000/gitlab-registry-proxy/gitlab-org/release-cli:latest

# Pre-download Python packages (uploads to Nexus on first request)
pip install --index-url http://nexus.company.com:8081/repository/pypi-proxy/simple \
  -r requirements.txt -r requirements-dev.txt
```

#### Verification

Test the configuration by triggering a pipeline:

```bash
# Check CI job logs for:
# - "Using local registry: harbor.company.com/proxy/"
# - "Installing packages from: http://nexus.company.com/..."

# Check Docker build logs for:
# - "Build args: --build-arg DOCKER_REGISTRY=... --build-arg APT_MIRROR=..."
```

If you see errors about unreachable hosts, verify:
1. Runner can reach your Nexus/Harbor server
2. Registry URLs are correct (including trailing slashes and paths)
3. Nexus/Harbor has cached the required images/packages

#### Alternative: Harbor Configuration

Harbor is often preferred over Nexus for Docker registries:

**1. Create Harbor Projects**:

```bash
# Create proxy projects in Harbor
# Project: docker-hub-proxy (Proxy for Docker Hub)
# Project: gitlab-registry-proxy (Proxy for registry.gitlab.com)
```

**2. Set GitLab CI/CD Variables**:

```bash
CI_DOCKER_REGISTRY=harbor.company.com/docker-hub-proxy/
CI_RELEASE_CLI_IMAGE=harbor.company.com/gitlab-registry-proxy/gitlab-org/release-cli:latest

DOCKER_REGISTRY=harbor.company.com/docker-hub-proxy/
# ... (use Nexus for PyPI and APT as Harbor doesn't support those)
```

#### Handling Alpine APK Packages

The Alpine-based CI job images need to install build dependencies (`gcc`, `musl-dev`, `libffi-dev`) via `apk add`. Three options:

**Option 1: HTTP Caching Proxy (Simplest)**

Configure your network to cache `dl-cdn.alpinelinux.org` traffic:

```bash
# Example with Squid proxy (not Nexus/Harbor)
http_proxy=http://proxy.company.com:3128 apk add gcc musl-dev libffi-dev
```

**Option 2: Custom Alpine Mirror**

Set up a local Alpine mirror and configure GitLab runners to use it. Create a custom base image:

```dockerfile
# custom-python-alpine.Dockerfile
FROM python:3.11-alpine
RUN echo "http://alpine-mirror.company.com/v3.18/main" > /etc/apk/repositories && \
    echo "http://alpine-mirror.company.com/v3.18/community" >> /etc/apk/repositories && \
    apk add --no-cache gcc musl-dev libffi-dev
```

Then use `CI_PYTHON_IMAGE=registry.company.com/custom-python-alpine:3.11`.

**Option 3: Switch to Debian (If Alpine is Problematic)**

If managing Alpine APK packages is too complex, use Debian-based images:

```bash
# Set this CI/CD variable
CI_PYTHON_IMAGE=python:3.11-slim
```

This switches from Alpine to Debian, requiring Debian APT mirrors instead. You'll need to configure:
- Debian APT mirror in addition to Ubuntu APT mirror
- Or use the same Ubuntu mirror (Debian packages often work)

**Recommendation**: For most air-gapped environments, **Option 1** (HTTP caching proxy) or **Option 3** (switch to Debian) is simplest.

## Usage

### Running Automatic Tests

Tests run automatically on every push and merge request:

```bash
# Push code to any branch
git push origin my-feature-branch

# Tests run automatically
# - test:python-3.11
# - test:python-3.12
```

**View results**: CI/CD > Pipelines > Click on pipeline

### Running Manual E2E Tests

E2E tests run against live GitLab instances (requires configured variables):

1. Go to **CI/CD > Pipelines**
2. Click **Run Pipeline**
3. Select branch (e.g., `main`)
4. (Optional) Add/override variables:
   - `E2E_TEST_SCOPE`: `single`, `dual`, `multi-project`, `multi-group`, or `all`
5. Click **Run pipeline**
6. In the pipeline view, click the **play button** (▶️) next to `test:e2e-live`

**Test scopes**:
- `single` (default): Single-instance tests only
- `dual`: Dual-instance tests (requires instance 2 variables)
- `multi-project`: Multi-project tests
- `multi-group`: Multi-group tests
- `all`: All E2E tests

### Creating a Release

Releases are triggered by pushing version tags:

```bash
# 1. Update version in pyproject.toml
# [project]
# version = "1.2.3"

# 2. Commit the version bump
git add pyproject.toml
git commit -m "chore: bump version to 1.2.3"
git push origin main

# 3. Create and push the tag
git tag v1.2.3
git push origin v1.2.3
```

**Pipeline execution**:
1. ✅ Run tests (Python 3.11 and 3.12)
2. 🐳 Build multi-arch Docker images (amd64, arm64)
3. 📦 Push to GitLab Container Registry with tags:
   - `1.2.3` (exact version)
   - `1.2` (minor version)
   - `1` (major version)
   - `latest` (latest stable)
4. 📋 Create GitLab release with auto-generated changelog

**View release**: Deployments > Releases

### Using Published Docker Images

Pull from GitLab Container Registry:

```bash
# Latest stable
docker pull registry.gitlab.com/yourname/mirror-maestro:latest

# Specific version
docker pull registry.gitlab.com/yourname/mirror-maestro:1.2.3

# Minor version (gets latest patch)
docker pull registry.gitlab.com/yourname/mirror-maestro:1.2
```

**Update docker-compose.yml**:

```yaml
services:
  app:
    # Replace 'build: .' with published image
    image: registry.gitlab.com/yourname/mirror-maestro:latest
    # ... rest of configuration
```

## Pipeline Configuration

### Caching

The pipeline caches pip packages to speed up subsequent runs:

```yaml
.cache_template: &cache_template
  cache:
    key: "${CI_JOB_NAME}"
    paths:
      - .cache/pip
```

Each job has its own cache based on job name.

### Multi-Architecture Builds

Docker images are built for multiple architectures:

- `linux/amd64` - Standard x86_64 servers
- `linux/arm64` - ARM servers (AWS Graviton, Apple Silicon, Raspberry Pi 4+)

Uses Docker Buildx with `docker:24-dind` service.

### Release Changelog

The release job auto-generates a changelog from git commits:

- Compares current tag with previous tag
- Extracts commit messages
- Formats as markdown list
- Includes Docker pull instructions

## Comparison: GitLab CI/CD vs GitHub Actions

| Feature | GitHub Actions | GitLab CI/CD |
|---------|----------------|--------------|
| **Config File** | `.github/workflows/*.yml` | `.gitlab-ci.yml` |
| **Container Registry** | GitHub Container Registry (`ghcr.io`) | GitLab Container Registry (`$CI_REGISTRY`) |
| **Image Tags** | `ghcr.io/owner/repo:tag` | `registry.gitlab.com/owner/repo:tag` |
| **Secrets** | Repository Secrets | CI/CD Variables |
| **Manual Trigger** | `workflow_dispatch` | Pipeline with manual job |
| **Matrix Builds** | `strategy.matrix` | Parallel jobs |
| **Release** | `softprops/action-gh-release` | `release-cli` |

Both systems are **fully independent** and can run in parallel without conflicts.

## Troubleshooting

### Pipeline Fails on Tag Push

**Problem**: Release pipeline fails because tests didn't run first.

**Solution**: Ensure tests are included as dependencies:

```yaml
build:docker:
  needs:
    - test:python-3.11
    - test:python-3.12
```

This is already configured in `.gitlab-ci.yml`.

### E2E Tests Fail with "Variable not set"

**Problem**: E2E tests can't find required variables.

**Solution**: Configure CI/CD variables in GitLab:
- Settings > CI/CD > Variables
- Add `E2E_GITLAB_URL`, `E2E_GITLAB_TOKEN`, etc.
- Mark as "Protected" if running on protected branches

### Docker Build Fails with "Insufficient permissions"

**Problem**: Can't push to GitLab Container Registry.

**Solution**:
1. Enable Container Registry in project settings
2. Ensure `$CI_REGISTRY_PASSWORD` is set (automatic in GitLab)
3. Check Docker login step in build job

### Cannot Pull Docker Image

**Problem**: `docker pull registry.gitlab.com/yourname/repo:latest` fails with authentication error.

**Solution**:

For **public projects**, the Container Registry should be accessible without authentication.

For **private projects**:

```bash
# Login with GitLab access token
echo "$GITLAB_TOKEN" | docker login -u "$GITLAB_USERNAME" --password-stdin registry.gitlab.com

# Or login with deploy token (recommended for CI/CD)
# Settings > Repository > Deploy Tokens
echo "$DEPLOY_TOKEN" | docker login -u "$DEPLOY_USERNAME" --password-stdin registry.gitlab.com
```

## Best Practices

### 1. Use Protected Variables for Secrets

Always mark sensitive variables (tokens) as:
- **Protected**: Only available on protected branches/tags
- **Masked**: Hidden in job logs

### 2. Pin Docker Image Versions

For production, use specific version tags:

```yaml
# Good (pinned version)
image: registry.gitlab.com/yourname/mirror-maestro:1.2.3

# Risky (may break on updates)
image: registry.gitlab.com/yourname/mirror-maestro:latest
```

### 3. Test Before Tagging

Run manual E2E tests before creating a release:

```bash
# 1. Run E2E tests on main branch
# 2. Verify all tests pass
# 3. Then create release tag
git tag v1.2.3
git push origin v1.2.3
```

### 4. Use Semantic Versioning

Follow [Semantic Versioning](https://semver.org/):
- **Major** (`v2.0.0`): Breaking changes
- **Minor** (`v1.1.0`): New features, backwards compatible
- **Patch** (`v1.0.1`): Bug fixes

### 5. Monitor Pipeline Performance

Check pipeline duration in CI/CD > Pipelines:
- Tests should complete in < 5 minutes
- Docker builds should complete in < 10 minutes
- Optimize by reviewing cache usage

## Advanced Configuration

### Custom Test Scope

Override E2E test scope when running pipeline:

**CI/CD > Pipelines > Run Pipeline**:

Add variable:
- Key: `E2E_TEST_SCOPE`
- Value: `all` (or `single`, `dual`, `multi-project`, `multi-group`)

### Custom Registry

To push to a different container registry (e.g., Docker Hub):

```yaml
build:docker:
  variables:
    IMAGE_NAME: docker.io/yourname/mirror-maestro
  before_script:
    - echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USERNAME" --password-stdin
```

Add `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` as CI/CD variables.

### Scheduled Pipelines

Run E2E tests on a schedule:

**CI/CD > Schedules > New schedule**:
- Description: "Nightly E2E Tests"
- Interval: `0 2 * * *` (2 AM daily)
- Target branch: `main`
- Variables: `E2E_TEST_SCOPE=all`

## Integration with GitHub

### Dual Registry Setup

Push to both GitHub and GitLab registries:

**On GitHub** (via GitHub Actions):
- Builds push to `ghcr.io/owner/mirror-maestro`

**On GitLab** (via GitLab CI/CD):
- Builds push to `registry.gitlab.com/owner/mirror-maestro`

Users can pull from either registry.

### Sync Releases

To keep releases in sync:

1. GitHub creates release with tag
2. GitLab mirrors tag automatically
3. GitLab pipeline builds and creates release

Both release pages will have the same version tags but different container registry URLs.

## Support

For issues with GitLab CI/CD:

1. Check job logs: **CI/CD > Pipelines > Click pipeline > Click job**
2. Review this documentation
3. Check `.gitlab-ci.yml` comments
4. Consult [GitLab CI/CD documentation](https://docs.gitlab.com/ee/ci/)

For Mirror Maestro issues:
- GitHub: https://github.com/MrZoller/mirror-maestro/issues
- GitLab: Issues in your mirrored repository
