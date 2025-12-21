# GitLab Mirror Wizard

A modern web application for managing GitLab mirrors across multiple instance pairs. Streamline the process of viewing, creating, and maintaining a large set of GitLab mirrors with an intuitive web interface.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green.svg)

## Screenshots

### Dashboard
![Dashboard](docs/screenshots/01-dashboard.png)
*Main dashboard with quick stats and getting started guide*

### GitLab Instances
![GitLab Instances](docs/screenshots/02-instances.png)
*Manage GitLab instances with their access tokens*

### Instance Pairs
![Instance Pairs](docs/screenshots/03-pairs.png)
*Configure pairs of GitLab instances for mirroring*

### Mirrors Management
![Mirrors](docs/screenshots/04-mirrors.png)
*View and manage mirrors with real-time status updates*

> **Note**: To generate screenshots with sample data, see [docs/screenshots/README.md](docs/screenshots/README.md)

## Features

### Core Functionality
- **Multiple Instance Pairs**: Define and manage mirrors across multiple pairs of GitLab instances (e.g., A↔B, B↔C)
- **Easy Mirror Creation**: Create mirrors with minimal user input - project information is fetched automatically via the GitLab API
- **Push & Pull Mirrors**: Support for both push and pull mirroring configurations
- **HTTPS Mirroring**: Uses HTTPS URLs with group access tokens for secure authentication
- **Flexible Configuration**: Define default mirror settings at the instance pair level, with optional per-mirror overrides

### Mirror Management
- **View Mirrors**: See all configured mirrors and their current status at a glance
- **Create Mirrors**: Quickly set up new mirrors between projects with dropdown selection
- **Update Mirrors**: Force immediate mirror synchronization with a single click
- **Edit/Remove Mirrors**: Modify or delete mirror configurations as needed
- **Import/Export**: Bulk import and export mirror settings for specified groups

### Modern Web Interface
- Clean, responsive design with tabbed navigation
- Real-time status updates
- Intuitive workflow for managing mirrors
- Similar look and feel to [issue-bridge](https://github.com/MrZoller/issue-bridge)

## Architecture

### Technology Stack
- **Backend**: Python 3.11+ with FastAPI
- **Database**: SQLite (async with aiosqlite)
- **Frontend**: Vanilla JavaScript with modern CSS
- **API Integration**: python-gitlab library
- **Deployment**: Docker and Docker Compose
- **Authentication**: HTTP Basic Auth (optional)
- **Security**: Encrypted storage of GitLab tokens using Fernet encryption

### Project Structure
```
gitlab-mirror-wizard/
├── app/
│   ├── api/              # API route handlers
│   │   ├── instances.py  # GitLab instance management
│   │   ├── pairs.py      # Instance pair management
│   │   ├── mirrors.py    # Mirror CRUD operations
│   │   └── export.py     # Import/export functionality
│   ├── core/             # Core functionality
│   │   ├── auth.py       # Authentication
│   │   ├── encryption.py # Token encryption
│   │   └── gitlab_client.py # GitLab API wrapper
│   ├── static/           # Frontend assets
│   │   ├── css/
│   │   └── js/
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
   git clone https://github.com/MrZoller/gitlab-mirror-wizard.git
   cd gitlab-mirror-wizard
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
APP_TITLE=GitLab Mirror Wizard
APP_DESCRIPTION=Manage GitLab mirrors across multiple instance pairs
```

### GitLab Access Tokens

You'll need GitLab access tokens with the following scopes:
- `api` - Full API access
- `read_api` - Read API access
- `write_repository` - Write access to repositories

**Recommended**: Use Group Access Tokens for better security and management.

## Usage Guide

### 1. Add GitLab Instances

First, configure the GitLab instances you want to mirror between:

1. Go to the **GitLab Instances** tab
2. Click **Add Instance**
3. Provide:
   - Name (e.g., "Production GitLab")
   - URL (e.g., "https://gitlab.example.com")
   - Access Token (Personal or Group Access Token)
   - Description (optional)

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
   - **Update**: Force an immediate mirror synchronization
   - **Delete**: Remove the mirror configuration

### 4. Import/Export

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

## Security

### Token Encryption
All GitLab access tokens are encrypted using Fernet (symmetric encryption) before being stored in the database. The encryption key is automatically generated and stored in `data/encryption.key`.

**Important**: Keep the `data/encryption.key` file secure and backed up. Without it, you won't be able to decrypt stored tokens.

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

- [ ] Support for scheduled mirror synchronization
- [ ] Mirror status monitoring dashboard
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
- GitHub Issues: https://github.com/MrZoller/gitlab-mirror-wizard/issues
- Documentation: This README and inline code documentation

## Acknowledgments

- Built with [FastAPI](https://fastapi.tiangolo.com/)
- GitLab integration via [python-gitlab](https://python-gitlab.readthedocs.io/)
- Inspired by [issue-bridge](https://github.com/MrZoller/issue-bridge)
