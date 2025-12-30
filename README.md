# Mirror Maestro

Orchestrate GitLab mirrors across multiple instance pairs with precision. A modern web application that streamlines the process of viewing, creating, and maintaining a large set of GitLab mirrors with an intuitive web interface.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green.svg)

## Screenshots

<details>
<summary>Click to view screenshots</summary>

### Dashboard
![Dashboard](docs/screenshots/01-dashboard.png?v=3)
*Modern dashboard with live statistics, health charts, recent activity timeline, and quick actions*

### GitLab Instances
![GitLab Instances](docs/screenshots/02-instances.png?v=3)
*Manage GitLab instances and rotate their access tokens (tokens are never displayed)*

### Instance Pairs
![Instance Pairs](docs/screenshots/03-pairs.png?v=3)
*Configure pairs of GitLab instances for mirroring*

### Mirrors Management
![Mirrors](docs/screenshots/04-mirrors.png?v=3)
*View and manage mirrors with pagination, smart path truncation, tree view for nested groups, group filtering, sorting, token status, real-time sync status, and safe per-mirror edits*

### Topology
![Topology](docs/screenshots/05-topology.png?v=3)
*Interactive topology visualization with animated data flows, zoom controls, and hover highlighting - click nodes or links to drill down into mirror details*

### Backup & Restore
![Backup](docs/screenshots/06-backup.png?v=4)
*Complete database backups with one-click creation and secure restore functionality*

### Settings (Multi-User Mode)
![Settings](docs/screenshots/07-settings.png?v=4)
*User management with admin and regular user roles, active/inactive status, and secure password management*

### About
![About](docs/screenshots/08-about.png?v=4)
*Project information with version details, links to documentation, and technology stack*

### Help
![Help](docs/screenshots/09-help.png?v=4)
*Comprehensive help documentation with setup guides, troubleshooting tips, and best practices*

> **Note**: To generate screenshots with sample data, see [docs/screenshots/README.md](docs/screenshots/README.md)

</details>

## Features

### Core Functionality
- **Multiple Instance Pairs**: Define and manage mirrors across multiple pairs of GitLab instances (e.g., A↔B, B↔C)
- **Easy Mirror Creation**: Create mirrors with minimal user input - project information is fetched automatically via the GitLab API
- **Push & Pull Mirrors**: Support for both push and pull mirroring configurations
- **Bidirectional Mirroring**: Create pairs in both directions (A→B and B→A) for two-way sync with independent settings per direction
- **Automatic Token Management**: Project access tokens are automatically created and managed for each mirror - no manual token configuration needed
- **Flexible Configuration**: Define default mirror settings at the instance pair level, optionally override per mirror
- **Safe Inline Editing**: Edit instances/pairs/mirrors in-table; fields that could break existing mirrors are locked/greyed out
- **Token Rotation**: Rotate instance access tokens or individual mirror tokens without deleting configuration

### Mirror Management
- **View Mirrors**: See all configured mirrors and their current status at a glance
- **Pagination & Scalability**: Handle thousands of mirrors efficiently with paginated views (25/50/100/200 per page)
- **Smart Path Display**: Nested group paths automatically truncated (e.g., `... / services / api-gateway`) with full path on hover
- **Tree View**: Hierarchical collapsible view of mirrors grouped by path structure - perfect for navigating deeply nested groups
- **Advanced Filtering**: Filter mirrors by group path prefix (e.g., `platform/core` shows all mirrors in that tree)
- **Flexible Sorting**: Sort by created date, updated date, source path, target path, or status - ascending or descending
- **Create Mirrors**: Quickly set up new mirrors between projects with dropdown selection
- **Sync Mirrors**: Force immediate mirror synchronization with a single click
- **Batch Sync**: Sync all mirrors in an instance pair with one click - perfect for resuming after outages
- **Edit/Remove Mirrors**: Modify safe mirror settings (and revert overrides back to "inherit"), or delete mirror configurations as needed
- **Import/Export**: Bulk import and export mirror settings with automatic rate limiting for large operations
- **Backup & Restore**: Create complete backups of your database and encryption key; restore from backups to recover or migrate
- **Rate Limiting**: Intelligent API rate limiting prevents overwhelming GitLab instances during batch operations

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
- **Database**: PostgreSQL (async with asyncpg)
- **Frontend**: Vanilla JavaScript with modern CSS
- **Visualization**: Chart.js for charts, D3.js for topology graphs
- **API Integration**: python-gitlab library
- **Deployment**: Docker and Docker Compose
- **Authentication**: HTTP Basic Auth (single-user) or JWT tokens (multi-user)
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
│   │   ├── gitlab_client.py # GitLab API wrapper
│   │   └── rate_limiter.py # Rate limiting for batch operations
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

# Database Configuration (PostgreSQL)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/mirror_maestro

# PostgreSQL credentials (used by docker-compose)
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=mirror_maestro

# Authentication (optional but recommended)
AUTH_ENABLED=true
AUTH_USERNAME=admin
AUTH_PASSWORD=changeme

# Multi-User Mode (optional)
# Set to true to enable JWT-based authentication with multiple users
MULTI_USER_ENABLED=false
# Initial admin user (only used when multi-user mode is first enabled)
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=changeme
INITIAL_ADMIN_EMAIL=
# JWT Settings (auto-generated secret if not provided)
JWT_SECRET_KEY=your-secret-key-here
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24

# Logging
LOG_LEVEL=INFO

# Application Settings
APP_TITLE=Mirror Maestro
APP_DESCRIPTION=Orchestrate GitLab mirrors across multiple instance pairs with precision

# SSL/TLS Configuration
SSL_ENABLED=false
SSL_CERT_PATH=/etc/nginx/ssl/cert.pem
SSL_KEY_PATH=/etc/nginx/ssl/key.pem

# Optional: Customize ports
HTTP_PORT=80
HTTPS_PORT=443

# Rate Limiting (for batch operations and imports)
# Delay between GitLab API operations to avoid overwhelming instances
GITLAB_API_DELAY_MS=200  # 200ms = ~300 ops/min (well under 600/min limit)
GITLAB_API_MAX_RETRIES=3  # Retries on rate limit errors
```

### GitLab Access Tokens

The app uses **Instance Access Tokens** to call the GitLab API:

- **Instance Access Token** (stored per GitLab instance)
  - Required scope: `api` (needed to manage mirrors and create project access tokens)
  - You can **rotate** this token from the **GitLab Instances** table (Edit → paste new token → Save)

**Automatic Mirror Tokens**: When you create a mirror, the app automatically creates a project access token on the appropriate project (source for pull mirrors, target for push mirrors). These tokens are managed automatically:
- Created when you create a mirror
- Deleted when you delete a mirror
- Can be manually rotated via the "Rotate Token" button in the mirrors table

### Multi-User Mode

Mirror Maestro supports two authentication modes:

#### Single-User Mode (Default)
- Uses HTTP Basic Auth with a single username/password
- Configured via `AUTH_ENABLED`, `AUTH_USERNAME`, `AUTH_PASSWORD`
- Good for personal use or small teams

#### Multi-User Mode
- JWT-based authentication with individual user accounts
- Admin users can create and manage other users
- Each user has their own login credentials
- Enabled by setting `MULTI_USER_ENABLED=true`

**Enabling Multi-User Mode:**

1. Set the following environment variables:
   ```bash
   MULTI_USER_ENABLED=true
   INITIAL_ADMIN_USERNAME=admin
   INITIAL_ADMIN_PASSWORD=your-secure-password
   JWT_SECRET_KEY=your-jwt-secret-key
   ```

2. Restart the application. An initial admin user will be created automatically.

3. Log in with the admin credentials and go to the **Settings** tab to manage users.

**User Management (Admin only):**
- Create new users with username, email (optional), and password
- Assign admin privileges to users
- Deactivate users without deleting them
- Delete users (except yourself and the last admin)

**User Features:**
- Change your own password via the user menu
- View your username and role in the top-right user menu

### SSL/TLS Configuration

Mirror Maestro supports optional SSL/TLS encryption for secure HTTPS connections. This is handled by an nginx reverse proxy that sits in front of the FastAPI application.

#### Quick Start with Self-Signed Certificate (Development)

For testing and development environments, you can quickly generate a self-signed certificate:

```bash
# 1. Generate self-signed certificate
./scripts/generate-self-signed-cert.sh

# 2. Enable SSL in your .env file
echo "SSL_ENABLED=true" >> .env

# 3. Configure nginx
./scripts/setup-ssl.sh

# 4. Start the application
docker-compose up -d

# 5. Access via HTTPS
# Open https://localhost (your browser will warn about the self-signed cert)
```

**Note:** Self-signed certificates will trigger browser security warnings. They are suitable for development only.

#### Production Setup with Valid Certificates

For production deployments, use certificates from a trusted Certificate Authority (CA) like Let's Encrypt:

1. **Obtain SSL certificates** from your CA (e.g., using certbot for Let's Encrypt)

2. **Copy certificates to the ssl directory:**
   ```bash
   mkdir -p ssl
   cp /path/to/your/fullchain.pem ssl/cert.pem
   cp /path/to/your/privkey.pem ssl/key.pem
   ```

3. **Enable SSL in your .env file:**
   ```bash
   SSL_ENABLED=true
   SSL_CERT_PATH=/etc/nginx/ssl/cert.pem
   SSL_KEY_PATH=/etc/nginx/ssl/key.pem
   ```

4. **Configure nginx:**
   ```bash
   ./scripts/setup-ssl.sh
   ```

5. **Optional: Customize ports** in your .env file:
   ```bash
   HTTP_PORT=80      # HTTP port (redirects to HTTPS when SSL is enabled)
   HTTPS_PORT=443    # HTTPS port
   ```

6. **Start the application:**
   ```bash
   docker-compose up -d
   ```

#### SSL Configuration Details

When SSL is enabled (`SSL_ENABLED=true`):
- HTTP requests on port 80 are automatically redirected to HTTPS on port 443
- The nginx reverse proxy handles SSL termination
- Modern TLS protocols (TLSv1.2, TLSv1.3) and secure cipher suites are used
- Security headers are automatically added (HSTS, X-Frame-Options, etc.)

When SSL is disabled (`SSL_ENABLED=false`):
- The application is served over HTTP only
- No SSL certificates are required
- Suitable for development or when SSL is handled by external infrastructure (load balancer, reverse proxy, etc.)

#### Certificate Renewal

For production certificates that expire (e.g., Let's Encrypt certificates expire every 90 days):

1. Renew your certificates using your CA's renewal process
2. Copy the new certificates to the `ssl/` directory (same filenames)
3. Reload nginx: `docker-compose restart nginx`

No need to restart the entire application stack.

#### Troubleshooting SSL

**"SSL certificates not found" error:**
- Ensure `ssl/cert.pem` and `ssl/key.pem` exist
- Check file permissions (cert should be readable, key should be 600)
- Run `./scripts/generate-self-signed-cert.sh` for development

**Browser shows "connection not secure":**
- Normal for self-signed certificates
- Click "Advanced" → "Proceed anyway" for testing
- For production, use certificates from a trusted CA

**nginx fails to start:**
- Check nginx logs: `docker-compose logs nginx`
- Verify certificate files are valid: `openssl x509 -in ssl/cert.pem -text -noout`
- Ensure ports 80 and 443 are not already in use

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
To prevent broken configurations, the app performs **cascading deletes with GitLab cleanup**:

- Deleting a **GitLab instance** also deletes any **instance pairs** that reference it and any **mirrors** belonging to those pairs.
  - **GitLab cleanup**: Before database deletion, the app removes all mirrors and project access tokens from GitLab using rate-limited API calls
  - **Rate limiting**: For instances with many mirrors, cleanup operations are throttled to avoid overwhelming GitLab (configurable delay between operations)
  - **Best-effort**: If GitLab cleanup fails for some mirrors (e.g., network errors, token expired), the database deletion still proceeds, and warnings are returned

- Deleting an **instance pair** also deletes any **mirrors** belonging to that pair.
  - **GitLab cleanup**: Removes all mirrors and tokens from GitLab before database deletion
  - **Rate limiting**: Applied when deleting pairs with multiple mirrors
  - **Progress tracking**: The operation returns detailed metrics including success/failure counts and operation duration

- Deleting a **mirror** also deletes its automatically-created project access token from GitLab.
  - **Best-effort**: If token deletion fails, the mirror is still removed from the database with a warning

**Rate Limiting Configuration:**
- Default: 200ms delay between GitLab API operations (~300 operations/minute)
- Configurable via `GITLAB_API_DELAY_MS` environment variable
- Automatic retry with exponential backoff on rate limit errors (429 responses)

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

### 3. Manage Mirrors

Create and manage mirrors between projects:

1. Go to the **Mirrors** tab
2. Select an instance pair from the dropdown
3. To create a new mirror:
   - Select source project (auto-populated from GitLab)
   - Select target project (auto-populated from GitLab)
   - Click **Create Mirror**
4. To manage existing mirrors:
   - **Edit**: Update safe per-mirror overrides (and optionally clear them back to "inherit")
   - **Sync**: Force an immediate mirror synchronization
   - **Rotate Token**: Create a new access token for the mirror (revokes the old one)
   - **Delete**: Remove the mirror configuration (also deletes the access token)

### 4. Batch Mirror Sync

Sync all mirrors in an instance pair with one click - particularly useful after GitLab outages or maintenance.

#### When to Use Batch Sync

- **After outages**: When a GitLab instance goes down temporarily, all mirrors may stop syncing
- **Post-maintenance**: After scheduled maintenance or upgrades
- **Large-scale updates**: When you need to sync hundreds of mirrors at once

#### How to Use

1. Go to the **Instance Pairs** tab
2. Click the **Sync All** button for the desired pair
3. Confirm the operation (shows mirror count and estimated duration)
4. View detailed results with success/failure counts and timing metrics

#### Rate Limiting Protection

To prevent overwhelming GitLab instances with too many API requests, batch sync uses intelligent rate limiting:

- **Configurable delay**: Default 200ms between operations (~300 requests/minute, well under GitLab's typical 600/min limit)
- **Automatic retry**: If GitLab returns a 429 "Too Many Requests" error, operations are retried with exponential backoff
- **Progress tracking**: Real-time progress with detailed reporting
- **Continue on failure**: Processing continues even if some mirrors fail

**Example**: Syncing 100 mirrors with default settings takes ~20 seconds and processes at a safe rate of ~300 operations/minute.

### 5. Import/Export

Bulk manage mirror configurations with portable JSON files.

#### How to Use

1. **Select an instance pair** from the Mirrors tab
2. Click **Export** to download all mirrors for that pair as JSON
3. Click **Import** to upload a JSON file and create mirrors for the selected pair

#### Export Format

Exports are **portable across environments** (dev/staging/prod):

- **Project paths** (not IDs) - e.g., `group/subgroup/project`
- **Mirror settings** - All configuration options (overwrite diverged, protected branches, etc.)
- **Metadata** (informational only) - Source instance, target instance, direction, export timestamp

Example export structure:
```json
{
  "metadata": {
    "exported_at": "2024-01-15T10:30:00Z",
    "pair_name": "GitLab.com → Self-hosted",
    "source_instance_name": "GitLab.com",
    "source_instance_url": "https://gitlab.com",
    "target_instance_name": "Self-hosted",
    "target_instance_url": "https://gitlab.example.com",
    "mirror_direction": "push",
    "total_mirrors": 2
  },
  "mirrors": [
    {
      "source_project_path": "mygroup/project1",
      "target_project_path": "mirrors/project1",
      "mirror_overwrite_diverged": false,
      "only_mirror_protected_branches": true,
      "enabled": true
    }
  ]
}
```

#### Import Process

When you import mirrors, Mirror Maestro:

1. **Looks up project IDs** from paths via GitLab API (2 API calls per mirror)
2. **Creates project access tokens** in GitLab (1 API call per mirror)
3. **Creates actual mirrors** in GitLab - push or pull (1 API call per mirror)
4. **Stores mirror records** in the database
5. **Applies rate limiting** - Waits 200ms before processing the next mirror

The result is **identical to creating mirrors via the UI**.

**Rate Limiting**: Each mirror import requires ~4 GitLab API calls. With default settings (200ms delay), importing 100 mirrors takes approximately 40-60 seconds and processes at a safe rate of ~200-300 API requests/minute. This prevents overwhelming your GitLab instances while ensuring reliable imports.

#### Import Results

After import completes, you'll see a detailed summary:

- **Imported count** - Successfully created mirrors
- **Skipped count** - Mirrors that already exist
- **Errors** - Detailed list of failures with specific project paths
- **Skipped details** - Which mirrors were skipped and why

Example:
```
Import complete: 8 imported, 2 skipped

Skipped (2):
  • [1/10] group/existing → mirror/existing: Already exists in database
  • [5/10] group/duplicate → mirror/duplicate: Already exists in database
```

#### Important Notes

- **Select the correct pair** before importing - the import creates mirrors for the currently selected pair
- **Metadata is ignored** on import - only the `mirrors` array is used
- **Projects must exist** - Both source and target projects must exist in their respective GitLab instances
- **Duplicates are skipped** - If a mirror already exists (same source/target paths), it won't be created again
- **Import continues on errors** - If some mirrors fail, others will still be imported

### 5. Backup & Restore

Protect your Mirror Maestro configuration with complete database backups:

1. Go to the **Backup** tab
2. **Creating a Backup**:
   - Review current statistics (instances, pairs, mirrors, database size)
   - Click **Create & Download Backup**
   - Save the `.tar.gz` file in a secure location
3. **Restoring from a Backup**:
   - Click **Select Backup File** and choose your backup archive
   - Optionally enable "Create backup before restore" (recommended)
   - Click **Restore Backup** and confirm the action
   - The application will reload with the restored data

#### Backup Contents

Each backup archive includes:
- **Database export** - All GitLab instances, instance pairs, and mirrors (JSON format)
- **Encryption key** - Required to decrypt stored GitLab tokens
- **Metadata** - Backup timestamp, version, and file manifest

#### Security Warning

⚠️ **Important**: Backup files contain your encryption key and can decrypt all stored GitLab tokens. Always:
- Store backups in a secure location
- Use encryption or access controls on backup storage
- Never share backups publicly
- Keep backups separate from your Mirror Maestro server

#### Best Practices

- **Regular backups**: Create backups daily or weekly depending on change frequency
- **Test restores**: Periodically verify backups can be restored successfully
- **Version retention**: Keep multiple backup versions in case of corruption
- **Migration**: Use backups to migrate between servers or Docker hosts
- **Pre-restore safety**: Always enable "Create backup before restore" when restoring

#### Backup Format

Backups are compressed tar archives (`.tar.gz`) with the naming format:
```
mirror-maestro-backup-YYYYMMDD-HHMMSS.tar.gz
```

Archives are portable across Mirror Maestro versions and can be restored on any compatible server.

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
- `POST /api/pairs/{id}/sync-mirrors` - Batch sync all enabled mirrors in a pair (with rate limiting)

#### Mirrors
- `GET /api/mirrors` - List all mirrors
- `POST /api/mirrors` - Create new mirror
- `GET /api/mirrors/{id}` - Get mirror details
- `PUT /api/mirrors/{id}` - Update mirror
- `DELETE /api/mirrors/{id}` - Delete mirror
- `POST /api/mirrors/{id}/update` - Trigger mirror update
- `POST /api/mirrors/{id}/rotate-token` - Rotate the mirror's access token

#### Import/Export
- `GET /api/export/pair/{id}` - Export mirrors for a pair
- `POST /api/export/pair/{id}` - Import mirrors for a pair

#### Topology
- `GET /api/topology` - Aggregated instance/link graph (supports staleness thresholds and "never succeeded" handling)
- `GET /api/topology/link-mirrors` - Drill down: list mirrors behind a topology link

#### Dashboard
- `GET /api/dashboard/metrics` - Dashboard metrics and statistics (total mirrors, health %, recent activity, charts)
- `GET /api/dashboard/quick-stats` - Quick stats for live polling (syncing count, recent failures)

#### Backup & Restore
- `GET /api/backup/stats` - Get backup statistics (instance/pair/mirror counts, database size)
- `GET /api/backup/create` - Create and download a complete backup archive
- `POST /api/backup/restore` - Restore from a backup archive (multipart form upload)

#### Health Check
- `GET /api/health/quick` - Quick health check for load balancers (no auth required)
- `GET /api/health` - Detailed health check with component status, mirror stats, and token expiration
- `GET /api/health?check_instances=true` - Extended health check with GitLab instance connectivity tests
- `GET /health` - Legacy health endpoint for backward compatibility

#### Search
- `GET /api/search?q={query}` - Global search across instances, pairs, and mirrors

## Security

### Token Encryption
All GitLab access tokens (instance tokens and mirror tokens) are encrypted using Fernet (symmetric encryption) before being stored in the database. The encryption key is automatically generated and stored in `data/encryption.key`.

**Important**: Keep the `data/encryption.key` file secure and backed up. Without it, you won't be able to decrypt stored tokens.

### Automatic Mirror Token Management
Mirrors use automatically-created project access tokens for HTTPS authentication. When creating a mirror:

1. The app determines which project needs the token:
   - **Pull mirrors**: Token is created on the source project (allows reading from it)
   - **Push mirrors**: Token is created on the target project (allows pushing to it)

2. A project access token is created with appropriate scopes:
   - `read_repository` for pull mirrors
   - `write_repository` for push mirrors

3. The token is used to construct an authenticated URL: `https://token_name:token@gitlab.example.com/project.git`

4. The token is encrypted and stored with the mirror record

**Token Lifecycle**:
- Tokens are automatically created when mirrors are created
- Tokens are automatically deleted when mirrors are deleted
- Tokens can be manually rotated via the "Rotate Token" button (creates new token, revokes old one)
- Tokens expire after 1 year by default

### Authentication
HTTP Basic Authentication can be enabled to protect the web interface. Configure credentials in the `.env` file:

```bash
AUTH_ENABLED=true
AUTH_USERNAME=your_username
AUTH_PASSWORD=your_secure_password
```

### Best Practices
1. Always use HTTPS in production
2. Use instance tokens with appropriate `api` scope for GitLab API access
3. Monitor token expiration - mirror tokens expire after 1 year
4. Use the "Rotate Token" feature when tokens are about to expire
5. Keep the encryption key secure
6. Use strong passwords for HTTP Basic Auth
7. Restrict network access to the application
8. Regularly backup the database and encryption key

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

**Import Fails or Has Errors**
- **Check the detailed error messages** - The import results show exactly which mirrors failed and why (e.g., `[3/10] group/bad → mirror/bad: Project not found`)
- **Validate JSON format** - Must have `mirrors` array with `source_project_path` and `target_project_path` fields
- **Ensure projects exist** - Both source and target projects must exist in their respective GitLab instances
- **Check project paths** - Must use full paths like `namespace/project` or `group/subgroup/project` (not just project names)
- **Verify pair selection** - Make sure you have the correct instance pair selected before importing
- **Check GitLab tokens** - Instance tokens need `api` scope to look up projects and create mirrors

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
export E2E_KEEP_RESOURCES=1                       # skip cleanup - keep projects/groups for manual inspection
```

#### Running E2E Tests

```bash
# Run all single-instance E2E tests
pytest -m "live_gitlab and not dual_instance" -v

# Run only multi-project tests
pytest -m multi_project -v

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

**Cross-Instance Tests** (`test_e2e_cross_instance.py`):
- Creates source projects on instance 1
- Creates empty target projects on instance 2
- Sets up push/pull mirrors between instances
- Verifies content propagates across GitLab instances

#### Cleanup

All tests automatically clean up created resources (projects, groups, mirrors) in a `finally` block, even if tests fail. Resources are deleted in reverse creation order to respect dependencies.

**Keep resources for manual inspection:**
```bash
E2E_KEEP_RESOURCES=1 pytest -m multi_project -v
```

When `E2E_KEEP_RESOURCES=1` is set, the test will:
- Skip deleting GitLab projects and groups
- Print a summary of all created resources with their IDs
- Leave mirrors configured on the projects so you can inspect them in GitLab

This is useful for debugging or manually exploring the test setup. Remember to delete the resources manually when done (delete projects first, then groups).

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
2. Select test scope: `single`, `dual`, `multi-project`, or `all`
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
- [x] Advanced filtering and search
- [x] Mirror health checks and diagnostics
- [x] PostgreSQL database support

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
