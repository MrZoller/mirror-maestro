# Changelog

All notable changes to this project will be documented in this file.

The format is based on **Keep a Changelog**, and this project adheres to **Semantic Versioning**.

## [Unreleased]

## [1.2.1] - 2026-02-19

### Fixed
- Documentation updates: corrected project structure, roadmap, and model listings across README.md and CLAUDE.md
- Fixed support URLs in issue mirroring documentation (pointed to correct GitHub repository)
- Fixed "GitLab Mirror Wizard" typo in CLAUDE.md (should be "Mirror Maestro")

## [1.2.0] - 2026-01-15

### Added

#### Automatic Token Management
- **Automatic Token Management**: Project access tokens are now automatically created when mirrors are created and deleted when mirrors are deleted. No more manual group token configuration required.
- **Token Rotation**: New "Rotate Token" button on mirrors to manually rotate project access tokens when needed.
- **Token Status Display**: Mirrors now show token status (active, expiring soon, expired) in the UI.

#### Issue Mirroring
- **Issue Sync Engine**: Automatically sync issues, comments, labels, attachments, and PM fields between GitLab instances.
- One-way sync with bidirectional support via dual mirrors (A→B and B→A).
- Configurable sync intervals (5-1440 minutes) with automatic scheduling via APScheduler.
- Smart change detection using content hashing to avoid unnecessary updates.
- PM field conversion (milestones, iterations, epics, assignees → informational labels).
- Attachment download/upload with URL rewriting.
- Time tracking sync (estimates and time spent).
- Loop prevention via "Mirrored-From" labels with hostname-based identification.
- Incremental syncing (only processes changed issues since last sync).
- Concurrent sync protection prevents race conditions in bidirectional setups.
- Stale job cleanup recovers from stuck sync jobs.
- **Production-Ready Robustness**:
  - Circuit breaker pattern prevents cascading failures.
  - Retry logic with exponential backoff for transient errors.
  - Progress checkpointing for large syncs (resumable on interruption).
  - Configurable attachment size limits (default 100MB).
  - Graceful shutdown waits for active syncs to complete.
  - Batched processing prevents memory exhaustion.
  - Rate limiting prevents API quota exhaustion.

#### Multi-User Authentication
- **JWT-based multi-user authentication**: Individual user accounts with admin/regular roles.
- User management API (`/api/users`) for creating, updating, and deleting users.
- Login/logout endpoints with JWT token validation.
- Auto-generated JWT secret key persisted to `data/jwt_secret.key`.
- Password hashing with bcrypt.
- Settings tab for admin user management.

#### Enterprise Deployment
- **Local artifact mirror support**: Deploy in air-gapped environments using Nexus, Artifactory, or Harbor.
- Configurable Docker registry, APT mirror, PyPI mirror, and CDN URLs via environment variables.
- Local vendor asset support (Chart.js, D3.js) for air-gapped deployments.
- Custom CA certificate support for internal GitLab instances.
- SSRF protection override for private IP ranges (`ALLOW_PRIVATE_IPS`).
- Enterprise deployment documentation (`docs/ENTERPRISE_DEPLOYMENT.md`).

#### Mirror Management Robustness
- **MirrorGitLabService**: All GitLab API calls for mirror operations routed through resilience layer.
- Per-instance circuit breakers for mirror operations.
- Rate limiting with configurable delays between operations.
- Exponential backoff retry for rate limit (429) and transient errors.

#### Additional Features
- **Backup & Restore**: Complete database backups with encryption key bundling and one-click restore.
- **Global Search**: Search across instances, pairs, and mirrors via `/api/search`.
- **Enhanced Health Checks**: Detailed health endpoint with component status, mirror stats, and token expiration checks.
- **TLS Keep-Alive**: Persistent connections for environments with firewall idle timeouts.
- **Dark Mode**: Beautiful dark theme with smooth transitions and localStorage persistence.
- **Enhanced Dashboard**: Chart.js health distribution charts, recent activity timeline, and quick actions.
- Help/User Manual page with comprehensive documentation.
- About page with version details and technology stack.
- Project hygiene files for open-source collaboration (CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md).

#### Testing
- 30 test files covering unit, integration, E2E, and live GitLab tests.
- Cross-instance E2E tests for mirror verification.
- Issue mirroring E2E tests with attachment and comment sync verification.
- Multi-project E2E tests with varied content templates.
- Mirror GitLab service robustness tests.

### Changed
- **BREAKING**: Removed Group Settings tab and group-level token/defaults management. Settings now use a simpler two-tier hierarchy (mirror → pair) instead of three-tier (mirror → group → pair).
- Simplified mirror creation flow - tokens are handled automatically behind the scenes.
- Instance tokens now require `api` scope to create project access tokens.

### Removed
- Group Access Tokens API (`/api/tokens`) - tokens are now managed automatically per-mirror.
- Group Mirror Defaults API (`/api/group-defaults`) - use per-mirror or per-pair settings instead.
- `GroupAccessToken` and `GroupMirrorDefaults` database models.
- Group Settings tab from the UI.

### Dependencies
- Added `bcrypt>=4.0.0` for password hashing
- Added `python-jose[cryptography]>=3.3.0` for JWT token handling
- Added `email-validator>=2.1.0` for user email validation
- Added `httpx>=0.27.2` for HTTP client operations

## [1.1.0] - 2026-01-03

### Added

#### Security Hardening
- **Production credential validation**: Application fails to start in production mode if default passwords (`changeme`) are used for `AUTH_PASSWORD`, `INITIAL_ADMIN_PASSWORD`, or `DATABASE_URL`.
- **Security headers middleware**: All responses include security headers:
  - `X-Frame-Options: DENY` (clickjacking protection)
  - `X-Content-Type-Options: nosniff` (MIME-sniffing protection)
  - `X-XSS-Protection: 1; mode=block` (XSS filter)
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Permissions-Policy` (restricts browser features)
- **HTTP rate limiting**: Protects against brute force attacks:
  - Auth endpoints: 5 requests/minute
  - Write operations: 30 requests/minute
  - Read operations: 100 requests/minute
  - Sync operations: 10 requests/minute
- **Docker non-root user**: Container runs as `appuser` (UID 1000) instead of root for improved security.
- **Encryption key permission hardening**: Key files are set to `0o600` with warning logs if permissions cannot be verified.

#### Operational Improvements
- **Alembic database migrations**: Set up migration framework for schema versioning. Use `alembic revision` to create migrations and `alembic upgrade head` to apply.
- **Python logging configuration**: `LOG_LEVEL` environment variable now properly configures Python's logging module (DEBUG, INFO, WARNING, ERROR).
- **Request logging middleware**: All requests are logged with:
  - Correlation IDs (`X-Request-ID` header)
  - Request method and path
  - Response status code
  - Request duration
- **Docker resource limits**: `docker-compose.yml` includes CPU and memory limits:
  - Database: 2 CPU, 1GB memory
  - Application: 2 CPU, 2GB memory
  - Nginx: 1 CPU, 256MB memory
- **Circuit breaker gradual recovery**: Requires 3 consecutive successes before closing (prevents oscillation between OPEN and CLOSED states).
- **SQL credential protection**: DEBUG SQL logging only enabled in development environment to prevent credential exposure.

#### Testing
- Comprehensive Dashboard API tests (7 new tests)
- Extended encryption edge case tests (4 new tests)
- Updated circuit breaker tests for gradual recovery behavior

### Changed
- **Environment variable**: New `ENVIRONMENT` setting (development, staging, production) controls security validation and SQL logging behavior.
- Encryption module now catches `binascii.Error` for invalid base64 input, converting to proper `ValueError`.

### Dependencies
- Added `slowapi>=0.1.9` for HTTP rate limiting
- Added `alembic>=1.13.1` for database migrations

## [0.1.0] - 2025-12-21

### Added
- Initial release of Mirror Maestro.

<!--
Links:
[Unreleased]: https://github.com/MrZoller/mirror-maestro/compare/v1.2.1...HEAD
[1.2.1]: https://github.com/MrZoller/mirror-maestro/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/MrZoller/mirror-maestro/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/MrZoller/mirror-maestro/compare/v0.1.0...v1.1.0
[0.1.0]: https://github.com/MrZoller/mirror-maestro/releases/tag/v0.1.0
-->
