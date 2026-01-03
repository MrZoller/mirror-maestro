# Changelog

All notable changes to this project will be documented in this file.

The format is based on **Keep a Changelog**, and this project adheres to **Semantic Versioning**.

## [Unreleased]

### Added
- **Automatic Token Management**: Project access tokens are now automatically created when mirrors are created and deleted when mirrors are deleted. No more manual group token configuration required.
- **Token Rotation**: New "Rotate Token" button on mirrors to manually rotate project access tokens when needed.
- **Token Status Display**: Mirrors now show token status (active, expiring soon, expired) in the UI.
- Help/User Manual page with comprehensive documentation.
- Project hygiene files for open-source collaboration (templates, policies, docs).

### Changed
- **BREAKING**: Removed Group Settings tab and group-level token/defaults management. Settings now use a simpler two-tier hierarchy (mirror → pair) instead of three-tier (mirror → group → pair).
- Simplified mirror creation flow - tokens are handled automatically behind the scenes.
- Instance tokens now require `api` scope to create project access tokens.

### Removed
- Group Access Tokens API (`/api/tokens`) - tokens are now managed automatically per-mirror.
- Group Mirror Defaults API (`/api/group-defaults`) - use per-mirror or per-pair settings instead.
- `GroupAccessToken` and `GroupMirrorDefaults` database models.
- Group Settings tab from the UI.

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
Links (optional):
[Unreleased]: https://github.com/<org>/<repo>/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/<org>/<repo>/releases/tag/v0.1.0
-->
