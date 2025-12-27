# Mirror Maestro

Orchestrate GitLab mirrors across multiple instance pairs with precision. A modern web application that streamlines the process of viewing, creating, and maintaining a large set of GitLab mirrors with an intuitive web interface.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green.svg)

## Screenshots

### Dashboard
![Dashboard](docs/screenshots/01-dashboard.png)
*Modern dashboard with live statistics, health charts, recent activity timeline, and quick actions*

### Dashboard (Dark Mode)
![Dashboard Dark Mode](docs/screenshots/07-dashboard-dark.png)
*Beautiful dark mode with comprehensive theming across all components*

### GitLab Instances
![GitLab Instances](docs/screenshots/02-instances.png)
*Manage GitLab instances and rotate their access tokens (tokens are never displayed)*

### Instance Pairs
![Instance Pairs](docs/screenshots/03-pairs.png)
*Configure pairs of GitLab instances for mirroring*

### Group Settings
![Group Tokens](docs/screenshots/04-tokens.png)
*Manage group access tokens (rotate/update in place) and group-level mirror default overrides*

### Mirrors Management
![Mirrors](docs/screenshots/05-mirrors.png)
*View and manage mirrors with real-time status updates and safe per-mirror edits*

### Topology
![Topology](docs/screenshots/06-topology.png)
*Interactive topology visualization with animated data flows, zoom controls, and hover highlighting - click nodes or links to drill down into mirror details*

> **Note**: To generate screenshots with sample data, see [docs/screenshots/README.md](docs/screenshots/README.md)

## Features

### Core Functionality
- **Multiple Instance Pairs**: Define and manage mirrors across multiple pairs of GitLab instances (e.g., A↔B, B↔C)
- **Easy Mirror Creation**: Create mirrors with minimal user input - project information is fetched automatically via the GitLab API
- **Push & Pull Mirrors**: Support for both push and pull mirroring configurations
- **HTTPS Mirroring**: Uses HTTPS URLs with group access tokens for secure authentication
- **Flexible Configuration**: Define default mirror settings at the instance pair level, override them per group, and optionally override per mirror
- **Safe Inline Editing**: Edit instances/pairs/mirrors in-table; fields that could break existing mirrors are locked/greyed out
- **Token Rotation**: Rotate instance access tokens and group access tokens without deleting configuration

### Mirror Management
- **View Mirrors**: See all configured mirrors and their current status at a glance
- **Create Mirrors**: Quickly set up new mirrors between projects with dropdown selection
- **Sync Mirrors**: Force immediate mirror synchronization with a single click
- **Edit/Remove Mirrors**: Modify safe mirror settings (and revert overrides back to “inherit”), or delete mirror configurations as needed
- **Import/Export**: Bulk import and export mirror settings for specified groups

### Modern Web Interface
- **Comprehensive Dashboard**: Live statistics cards, health distribution charts (Chart.js), recent activity timeline, and quick actions
- **Dark Mode**: Beautiful dark theme with smooth transitions and localStorage persistence - toggle anytime with the sun/moon button
- **Live Status Polling**: Real-time updates every 30 seconds with pulsing indicators for actively syncing mirrors
- **Enhanced Topology**: Animated particle system showing data flow, zoom controls (+/−/reset), and smart hover highlighting
- **Clean, Responsive Design**: Modern card-based layout with smooth animations and tabbed navigation
- **Intuitive Workflow**: Straightforward mirror management with visual feedback and status indicators
- Similar look and feel to [issue-bridge](https://github.com/MrZoller/issue-bridge)

## Architecture

### Technology Stack
- **Backend**: Python 3.11+ with FastAPI
- **Database**: SQLite (async with aiosqlite)
- **Frontend**: Vanilla JavaScript with modern CSS
- **Visualization**: Chart.js for charts, D3.js for topology graphs
- **API Integration**: python-gitlab library
- **Deployment**: Docker and Docker Compose
- **Authentication**: HTTP Basic Auth (optional)
- **Security**: Encrypted storage of GitLab tokens using Fernet encryption

### Project Structure
```
mirror-maestro/
├── app/
│   ├── api/              # API route handlers
│   │   ├── dashboard.py  # Dashboard metrics
│   │   ├── instances.py  # GitLab instance management
│   │   ├── pairs.py      # Instance pair management
│   │   ├── mirrors.py    # Mirror CRUD operations
│   │   ├── topology.py   # Topology visualization
│   │   └── export.py     # Import/export functionality
│   ├── core/             # Core functionality
│   │   ├── auth.py       # Authentication
│   │   ├── encryption.py # Token encryption
│   │   └── gitlab_client.py # GitLab API wrapper
│   ├── static/           # Frontend assets
│   │   ├── css/          # Modern CSS with design tokens
│   │   └── js/           # Vanilla JS with D3.js & Chart.js
│   ├── templates/        # HTML templates
│   ├── config.py         # Application configuration
│   ├── database.py       # Database setup
│   ├── models.py         # SQLAlchemy models
│   └── main.py           # FastAPI application
├── data/                 # Database and encryption keys
├── docker-compose.yml    # Docker Compose configuration
├── Dockerfile            # Docker image definition
├── requirements.txt      # Python dependencies
└── README.md             # This file
```

## Quick Start

### Using Docker (Recommended)

1. **Clone the repository**
   ```bash
   git clone https://github.com/MrZoller/mirror-maestro.git
   cd mirror-maestro
   ```

2. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your preferred settings
   ```

3. **Start the application**
   ```bash
   docker-compose up -d
   ```

4. **Access the web interface**
   Open your browser to `http://localhost:8000`

   Default credentials (if auth is enabled):
   - Username: `admin`
   - Password: `changeme`

### Local Development

1. **Install dependencies**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Create data directory**
   ```bash
   mkdir -p data
   ```

3. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env as needed
   ```

4. **Run the application**
   ```bash
   python -m app.main
   # Or use uvicorn directly:
   uvicorn app.main:app --reload
   ```

5. **Access the web interface**
   Open your browser to `http://localhost:8000`

## Configuration

### Environment Variables

Create a `.env` file with the following variables:

```bash
# Server Configuration
HOST=0.0.0.0
PORT=8000

# Database Configuration
DATABASE_URL=sqlite+aiosqlite:///./data/mirrors.db

# Authentication (optional but recommended)
AUTH_ENABLED=true
AUTH_USERNAME=admin
AUTH_PASSWORD=changeme

# Logging
LOG_LEVEL=INFO

# Application Settings
APP_TITLE=Mirror Maestro
APP_DESCRIPTION=Orchestrate GitLab mirrors across multiple instance pairs with precision
```

### GitLab Access Tokens

This app uses **two kinds of tokens**:

- **Instance Access Token** (used to call the GitLab API)
  - Recommended scope: `api`
  - You can **rotate** this token from the **GitLab Instances** table (Edit → paste new token → Save).

- **Group Access Token** (embedded into HTTPS clone URLs for mirrors)
  - Recommended scopes:
    - `read_repository`
    - `write_repository` (needed for push mirrors)
  - You can **rotate** these tokens from the **Group Settings** table (Update token → paste new token → Save).

**Recommended**: Use Group Access Tokens for better security and management.

## Usage Guide

### 1. Add GitLab Instances

First, configure the GitLab instances you want to mirror between:

1. Go to the **GitLab Instances** tab
2. Fill the **Add Instance** form
3. Provide:
   - Name (e.g., "Production GitLab")
   - URL (e.g., "https://gitlab.example.com")
   - Access Token (Personal or Group Access Token)
   - Description (optional)

#### Rotating instance access tokens
You can rotate the stored instance access token without changing the instance URL:
- Click **Edit** on an instance row
- Paste a new **Access Token**
- Click **Save**

> The token value is **never displayed** in the UI (only a masked placeholder is shown).

#### Deletion behavior (important)
To prevent broken configurations, the app performs **cascading deletes**:
- Deleting a **GitLab instance** also deletes any **instance pairs** that reference it and any **mirrors** belonging to those pairs (plus related group defaults/tokens).
- Deleting an **instance pair** also deletes any **mirrors** belonging to that pair (plus related group defaults).

The UI shows a warning and requires confirmation before performing these actions.

### 2. Create Instance Pairs

Define pairs of instances where mirrors will be created:

1. Go to the **Instance Pairs** tab
2. Click **Create Pair**
3. Configure:
   - Pair name (e.g., "Prod to Backup")
   - Source instance
   - Target instance
   - Mirror direction (push or pull)
   - Default mirror settings:
     - Mirror protected branches
     - Overwrite divergent branches
     - Trigger builds on update
     - Only mirror protected branches

### 3. Configure Group Settings

**Important**: Group access tokens are required for mirrors to authenticate via HTTPS.

1. In GitLab, create a group access token for each group that contains projects you want to mirror:
   - Go to your GitLab group → Settings → Access Tokens
   - Create a token with the following scopes:
     - `read_repository` - Read access to repositories
     - `write_repository` - Write access to repositories (for push mirrors)
   - Save the token value (you won't be able to see it again)

2. In Mirror Maestro:
   - Go to the **Group Settings** tab
   - Click **Add Group Token**
   - Provide:
     - GitLab Instance (select from configured instances)
     - Group Path (e.g., "platform", "frontend", "infrastructure", or "platform/core" for subgroups)
     - Token Name (e.g., "mirror-token")
     - Token Value (the token you created in GitLab)

#### Rotating group access tokens
Because tokens expire, the UI supports **updating tokens in place**:
- In **Configured Group Settings**, click **Update token**
- Paste the new token value
- Click **Update Token**

**Multi-Level Group Support**: The application supports multi-level group paths. For a project at `platform/core/api-gateway`, you can create a token for either:
- `platform/core` (subgroup level) - most specific
- `platform` (top-level group) - will be used for all projects in platform/* if no more specific token exists

The application automatically searches from most specific to least specific, so you can organize tokens at any level of your group hierarchy.

#### Group-level mirror default overrides
You can optionally define group-level overrides for mirror defaults (direction, overwrite divergent branches, only protected branches, and pull-only options like trigger builds / regex / mirror user).

Resolution order for mirror settings is:
1. Per-mirror overrides (set during mirror creation)
2. Group defaults (most specific matching group path)
3. Instance pair defaults

### 4. Manage Mirrors

Create and manage mirrors between projects:

1. Go to the **Mirrors** tab
2. Select an instance pair from the dropdown
3. To create a new mirror:
   - Select source project (auto-populated from GitLab)
   - Select target project (auto-populated from GitLab)
   - Click **Create Mirror**
4. To manage existing mirrors:
   - **Edit**: Update safe per-mirror overrides (and optionally clear them back to “inherit”)
   - **Sync**: Force an immediate mirror synchronization
   - **Delete**: Remove the mirror configuration

### 5. Import/Export

Bulk manage mirror configurations:

- **Export**: Download mirror configurations as JSON for backup or sharing
- **Import**: Upload JSON file to create multiple mirrors at once

## API Documentation

The application provides a RESTful API. Once running, visit:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

### Key Endpoints

#### GitLab Instances
- `GET /api/instances` - List all instances
- `POST /api/instances` - Create new instance
- `GET /api/instances/{id}` - Get instance details
- `PUT /api/instances/{id}` - Update instance
- `DELETE /api/instances/{id}` - Delete instance
- `GET /api/instances/{id}/projects` - Get projects for instance

#### Instance Pairs
- `GET /api/pairs` - List all pairs
- `POST /api/pairs` - Create new pair
- `GET /api/pairs/{id}` - Get pair details
- `PUT /api/pairs/{id}` - Update pair
- `DELETE /api/pairs/{id}` - Delete pair

#### Group Access Tokens
- `GET /api/tokens` - List all group access tokens
- `POST /api/tokens` - Create new group access token
- `GET /api/tokens/{id}` - Get token details
- `PUT /api/tokens/{id}` - Update token
- `DELETE /api/tokens/{id}` - Delete token

#### Group mirror defaults
- `GET /api/group-defaults` - List all group mirror defaults (optionally filter by `instance_pair_id`)
- `POST /api/group-defaults` - Create/update group mirror defaults (upsert)
- `DELETE /api/group-defaults/{id}` - Delete group mirror defaults

#### Mirrors
- `GET /api/mirrors` - List all mirrors
- `POST /api/mirrors` - Create new mirror
- `GET /api/mirrors/{id}` - Get mirror details
- `PUT /api/mirrors/{id}` - Update mirror
- `DELETE /api/mirrors/{id}` - Delete mirror
- `POST /api/mirrors/{id}/update` - Trigger mirror update

#### Import/Export
- `GET /api/export/pair/{id}` - Export mirrors for a pair
- `POST /api/export/pair/{id}` - Import mirrors for a pair

#### Topology
- `GET /api/topology` - Aggregated instance/link graph (supports staleness thresholds and "never succeeded" handling)
- `GET /api/topology/link-mirrors` - Drill down: list mirrors behind a topology link

#### Dashboard
- `GET /api/dashboard/metrics` - Dashboard metrics and statistics (total mirrors, health %, recent activity, charts)
- `GET /api/dashboard/quick-stats` - Quick stats for live polling (syncing count, recent failures)

## Security

### Token Encryption
All GitLab access tokens (both instance tokens and group access tokens) are encrypted using Fernet (symmetric encryption) before being stored in the database. The encryption key is automatically generated and stored in `data/encryption.key`.

**Important**: Keep the `data/encryption.key` file secure and backed up. Without it, you won't be able to decrypt stored tokens.

### Mirror Authentication
Mirrors use group access tokens for HTTPS authentication. When creating a mirror, the application automatically:
1. Extracts the group path from the project path (e.g., "platform/core/api-gateway" → tries "platform/core", then "platform")
2. Looks up the stored group access token, checking from most specific to least specific group level
3. Constructs an authenticated URL like: `https://token_name:token@gitlab.example.com/group/project.git`
4. Passes this URL to GitLab for mirror configuration

**Multi-Level Group Support**: For nested groups like `platform/core/api-gateway`, the application searches for tokens in this order:
- `platform/core` (subgroup) - if a token exists here, it's used
- `platform` (parent group) - fallback if no subgroup token exists

This ensures secure, token-based authentication without storing credentials in GitLab mirror configurations, and provides flexibility in token management across complex group hierarchies.

### Authentication
HTTP Basic Authentication can be enabled to protect the web interface. Configure credentials in the `.env` file:

```bash
AUTH_ENABLED=true
AUTH_USERNAME=your_username
AUTH_PASSWORD=your_secure_password
```

### Best Practices
1. Always use HTTPS in production
2. Use Group Access Tokens instead of Personal Access Tokens
3. Regularly rotate access tokens
4. Keep the encryption key secure
5. Use strong passwords for HTTP Basic Auth
6. Restrict network access to the application
7. Regularly backup the database and encryption key

## Troubleshooting

### Common Issues

**Connection Refused**
- Ensure the GitLab URL is correct and accessible
- Verify the access token has the required scopes
- Check firewall/network settings

**Mirror Creation Fails**
- Verify both source and target projects exist
- Ensure the access token has write permissions
- Check that the projects are not already being mirrored

**Import Fails**
- Validate JSON format matches export format
- Ensure projects exist in the selected instance pair
- Check for duplicate mirrors

### Logs

View application logs:
```bash
# Docker
docker-compose logs -f

# Local development
# Logs will be printed to console
```

Set log level in `.env`:
```bash
LOG_LEVEL=DEBUG  # Options: DEBUG, INFO, WARNING, ERROR
```

## Development

### Running Tests
```bash
pip install -e ".[dev]"
pytest
```

### Live GitLab End-to-End Tests (opt-in)

The project includes comprehensive E2E tests that provision temporary projects and configure mirrors against **real GitLab instances**. These tests create realistic project content (files, branches, tags) and verify that mirroring actually works.

#### Test Categories

| Marker | Description | Test Files |
|--------|-------------|------------|
| `live_gitlab` | All live GitLab tests | All `test_e2e_*.py` files |
| `multi_project` | Multiple projects in one group | `test_e2e_multi_project.py` |
| `multi_group` | Group hierarchy with defaults | `test_e2e_multi_group.py` |
| `dual_instance` | Cross-instance mirroring | `test_e2e_cross_instance.py` |

#### Environment Variables

**Required for all E2E tests:**
```bash
export E2E_LIVE_GITLAB=1                          # opt-in guard (must be set)
export E2E_GITLAB_URL="https://gitlab.example.com"
export E2E_GITLAB_TOKEN="glpat-..."               # must be able to create/delete projects, groups, and mirrors
export E2E_GITLAB_GROUP_PATH="my-group/subgroup"  # group where test resources will be created
```

**Additional variables for dual-instance tests:**
```bash
export E2E_GITLAB_URL_2="https://gitlab2.example.com"
export E2E_GITLAB_TOKEN_2="glpat-..."
export E2E_GITLAB_GROUP_PATH_2="target-group"
```

**Optional tuning:**
```bash
export E2E_GITLAB_HTTP_USERNAME="oauth2"          # username for HTTPS clone auth (default: oauth2)
export E2E_GITLAB_MIRROR_TIMEOUT_S="120"          # timeout for mirror sync (default: 120)
```

#### Running E2E Tests

```bash
# Run all single-instance E2E tests
pytest -m "live_gitlab and not dual_instance" -v

# Run only multi-project tests
pytest -m multi_project -v

# Run only multi-group hierarchy tests
pytest -m multi_group -v

# Run cross-instance tests (requires two GitLab instances)
pytest -m dual_instance -v

# Run all E2E tests (single + dual instance)
pytest -m live_gitlab -v
```

#### What the Tests Do

**Multi-Project Tests** (`test_e2e_multi_project.py`):
- Creates 3 projects with different content (Python, JavaScript, Go templates)
- Each project has multiple files, branches (main, develop, feature/*), and tags
- Tests both push and pull mirroring
- Verifies content (commits, branches, tags, files) syncs correctly

**Multi-Group Tests** (`test_e2e_multi_group.py`):
- Creates a group hierarchy: `e2e-root/{frontend,backend,shared}`
- Sets different group-level defaults for each subgroup
- Verifies defaults inheritance works correctly
- Tests token inheritance across group hierarchy

**Cross-Instance Tests** (`test_e2e_cross_instance.py`):
- Creates source projects on instance 1
- Creates empty target projects on instance 2
- Sets up push/pull mirrors between instances
- Verifies content propagates across GitLab instances

#### Cleanup

All tests automatically clean up created resources (projects, groups, mirrors) in a `finally` block, even if tests fail. Resources are deleted in reverse creation order to respect dependencies.

### Run Live GitLab E2E via GitHub Actions (manual)

This repo includes a manual workflow: `.github/workflows/e2e-live-gitlab.yml`.

**Repository Secrets (add in Settings → Secrets):**
- `E2E_GITLAB_TOKEN` (required)
- `E2E_GITLAB_URL` (recommended)
- `E2E_GITLAB_GROUP_PATH` (recommended)
- `E2E_GITLAB_TOKEN_2` (for dual-instance tests)
- `E2E_GITLAB_URL_2` (for dual-instance tests)
- `E2E_GITLAB_GROUP_PATH_2` (for dual-instance tests)

**Trigger the workflow:**
1. Go to Actions → **Live GitLab E2E (manual)** → Run workflow
2. Select test scope: `single`, `dual`, `multi-project`, `multi-group`, or `all`
3. Optionally override URLs and group paths in the dispatch inputs

### Code Style
The project follows standard Python conventions:
- Use Black for code formatting
- Follow PEP 8 guidelines
- Write descriptive commit messages

### Contributing
Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Roadmap

- [x] Mirror status monitoring dashboard (Live dashboard with charts and real-time updates ✨)
- [x] Dark mode support (Beautiful theme with smooth transitions ✨)
- [x] Enhanced topology visualization (Animated particles, zoom controls, hover highlighting ✨)
- [ ] Support for scheduled mirror synchronization
- [ ] Email notifications for mirror failures
- [ ] Support for SSH-based mirroring
- [ ] Multi-user support with role-based access
- [ ] Advanced filtering and search
- [ ] Mirror health checks and diagnostics
- [ ] PostgreSQL database support

## Related Projects

- [issue-bridge](https://github.com/MrZoller/issue-bridge) - Synchronize GitLab issues across instances

## License

MIT License - see LICENSE file for details

## Support

For issues, questions, or contributions:
- GitHub Issues: [MrZoller/mirror-maestro issues](https://github.com/MrZoller/mirror-maestro/issues)
- Documentation: This README and inline code documentation

## Acknowledgments

- Built with [FastAPI](https://fastapi.tiangolo.com/)
- GitLab integration via [python-gitlab](https://python-gitlab.readthedocs.io/)
- Inspired by [issue-bridge](https://github.com/MrZoller/issue-bridge)
