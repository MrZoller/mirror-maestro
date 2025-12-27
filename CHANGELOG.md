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

## [0.1.0] - 2025-12-21

### Added
- Initial release of Mirror Maestro.

<!--
Links (optional):
[Unreleased]: https://github.com/<org>/<repo>/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/<org>/<repo>/releases/tag/v0.1.0
-->
