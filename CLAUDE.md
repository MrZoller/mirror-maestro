# Mirror Maestro - AI Assistant Guide

This document provides comprehensive guidance for AI assistants working on the Mirror Maestro codebase. It covers architecture, conventions, patterns, and best practices to maintain consistency and quality.

## Table of Contents

1. [Project Overview](#project-overview)
2. [Architecture](#architecture)
3. [Codebase Structure](#codebase-structure)
4. [Key Components](#key-components)
5. [Development Workflow](#development-workflow)
6. [Code Conventions](#code-conventions)
7. [Database Schema](#database-schema)
8. [API Patterns](#api-patterns)
9. [Frontend Patterns](#frontend-patterns)
10. [Security Considerations](#security-considerations)
11. [Testing Guidelines](#testing-guidelines)
12. [Common Tasks](#common-tasks)
13. [Troubleshooting](#troubleshooting)

## Project Overview

**Mirror Maestro** is a modern web application for managing GitLab mirrors across multiple instance pairs. It streamlines the process of viewing, creating, and maintaining a large set of GitLab mirrors with an intuitive web interface.

### Technology Stack

- **Backend**: Python 3.11+ with FastAPI
- **Database**: SQLite with async support (aiosqlite)
- **ORM**: SQLAlchemy 2.0+ (async)
- **Frontend**: Vanilla JavaScript (no frameworks), modern CSS with design tokens
- **Templates**: Jinja2
- **API Client**: python-gitlab
- **Encryption**: Fernet (symmetric encryption via cryptography library)
- **Authentication**: HTTP Basic Auth (optional)
- **Deployment**: Docker and Docker Compose

### Core Features

- Manage multiple GitLab instances and instance pairs
- Create and configure push/pull mirrors with minimal input
- Group access token management with rotation support
- Hierarchical configuration (per-mirror → group → pair defaults)
- Topology visualization with D3.js
- Import/export mirror configurations
- Encrypted token storage

## Architecture

### Design Patterns

1. **Async/Await**: All I/O operations (database, HTTP) are asynchronous
2. **Dependency Injection**: FastAPI's DI for database sessions and authentication
3. **Repository Pattern**: Database access through SQLAlchemy models
4. **Three-Tier Configuration**: Mirror settings resolved through hierarchy (mirror → group → pair)
5. **Encryption at Rest**: All GitLab tokens encrypted before database storage
6. **Single-Page Application**: Frontend is a SPA with tab-based navigation

### Application Layers

```
┌─────────────────────────────────────┐
│   Frontend (Vanilla JS + CSS)      │
│   - app.js (state management)       │
│   - topology.js (D3.js graph)       │
└──────────────┬──────────────────────┘
               │ HTTP/JSON
┌──────────────▼──────────────────────┐
│   API Layer (FastAPI Routers)      │
│   - instances.py                    │
│   - pairs.py                        │
│   - mirrors.py                      │
│   - tokens.py                       │
│   - group_defaults.py               │
│   - topology.py                     │
│   - export.py                       │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│   Core Business Logic               │
│   - gitlab_client.py (API wrapper)  │
│   - encryption.py (token security)  │
│   - auth.py (HTTP Basic)            │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│   Data Layer (SQLAlchemy)           │
│   - models.py (ORM models)          │
│   - database.py (session management)│
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│   SQLite Database (async)           │
│   - Encrypted tokens                │
│   - Configuration data              │
└─────────────────────────────────────┘
```

## Codebase Structure

```
mirror-maestro/
├── app/                              # Main application code
│   ├── api/                          # API route handlers (FastAPI routers)
│   │   ├── __init__.py
│   │   ├── instances.py              # GitLab instance CRUD
│   │   ├── pairs.py                  # Instance pair CRUD
│   │   ├── mirrors.py                # Mirror CRUD and sync operations
│   │   ├── tokens.py                 # Group access token management
│   │   ├── group_defaults.py         # Group-level mirror defaults
│   │   ├── topology.py               # Topology visualization API
│   │   └── export.py                 # Import/export functionality
│   │
│   ├── core/                         # Core business logic
│   │   ├── __init__.py
│   │   ├── auth.py                   # HTTP Basic authentication
│   │   ├── encryption.py             # Fernet encryption for tokens
│   │   └── gitlab_client.py          # GitLab API wrapper
│   │
│   ├── static/                       # Frontend assets
│   │   ├── css/
│   │   │   └── style.css             # Modern CSS with design tokens
│   │   └── js/
│   │       ├── app.js                # Main frontend logic
│   │       └── topology.js           # D3.js topology visualization
│   │
│   ├── templates/                    # Jinja2 templates
│   │   └── index.html                # Single-page application
│   │
│   ├── __init__.py                   # Package marker
│   ├── config.py                     # Pydantic Settings configuration
│   ├── database.py                   # SQLAlchemy async setup
│   ├── models.py                     # Database models
│   └── main.py                       # FastAPI application entry point
│
├── tests/                            # Test suite
│   ├── conftest.py                   # Pytest fixtures
│   ├── test_api_*.py                 # API endpoint tests
│   ├── test_core_*.py                # Core module tests
│   └── test_e2e_live_gitlab.py       # Live GitLab E2E tests (opt-in)
│
├── docs/                             # Documentation
│   └── screenshots/                  # Application screenshots
│
├── scripts/                          # Utility scripts
│   ├── seed_data.py                  # Sample data generation
│   └── take-screenshots.js           # Playwright screenshot automation
│
├── data/                             # Runtime data (gitignored)
│   ├── mirrors.db                    # SQLite database
│   └── encryption.key                # Fernet encryption key
│
├── .github/                          # GitHub configuration
│   ├── workflows/
│   │   ├── tests.yml                 # CI/CD pipeline
│   │   └── e2e-live-gitlab.yml       # Live GitLab E2E workflow
│   ├── ISSUE_TEMPLATE/
│   ├── pull_request_template.md
│   └── dependabot.yml
│
├── .env.example                      # Environment variable template
├── .gitignore                        # Git ignore rules
├── .editorconfig                     # Editor settings
├── docker-compose.yml                # Docker Compose configuration
├── Dockerfile                        # Container image definition
├── pyproject.toml                    # Project metadata and pytest config
├── requirements.txt                  # Production dependencies
├── requirements-dev.txt              # Development dependencies
├── LICENSE                           # MIT License
├── README.md                         # User documentation
└── CLAUDE.md                         # This file (AI assistant guide)
```

## Key Components

### Backend Components

#### 1. FastAPI Application (`app/main.py`)

**Entry Point Pattern**:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    await init_db()  # Initialize database
    yield
    # Cleanup (if needed)

app = FastAPI(
    title=settings.app_title,
    lifespan=lifespan
)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include routers with /api prefix
app.include_router(instances.router)
app.include_router(pairs.router)
# ... etc
```

**Key Features**:
- Async context manager for startup/shutdown
- Database initialization on startup
- Static file serving
- Jinja2 template rendering
- Health check endpoint at `/health`
- Root endpoint serves SPA with auth

#### 2. Database Models (`app/models.py`)

**Model Structure**:
```python
class GitLabInstance(Base):
    __tablename__ = "gitlab_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    # ... additional fields
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

**All Models Include**:
- Primary key: `id` (Integer, autoincrement)
- Timestamps: `created_at`, `updated_at` (auto-managed)
- Type hints using `Mapped[T]`
- Proper nullable handling

**Models**:
1. `GitLabInstance` - GitLab instance configuration
2. `InstancePair` - Pairs of instances for mirroring
3. `Mirror` - Individual mirror configurations
4. `GroupAccessToken` - Encrypted tokens for HTTPS mirroring
5. `GroupMirrorDefaults` - Group-level setting overrides

#### 3. Database Access (`app/database.py`)

**Session Management**:
```python
async_engine = create_async_engine(
    settings.database_url,
    echo=settings.log_level == "DEBUG"
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def get_db():
    """Dependency for FastAPI routes."""
    async with AsyncSessionLocal() as session:
        yield session
```

**Usage in Routes**:
```python
@router.get("/api/instances")
async def list_instances(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    result = await db.execute(select(GitLabInstance))
    instances = result.scalars().all()
    return instances
```

#### 4. GitLab Client (`app/core/gitlab_client.py`)

**Wrapper Pattern**:
```python
class GitLabClient:
    def __init__(self, url: str, encrypted_token: str):
        self.url = url
        self.token = encryption.decrypt(encrypted_token)
        self.gl = gitlab.Gitlab(url, private_token=self.token)

    def test_connection(self) -> dict:
        """Test GitLab connection and return user info."""
        self.gl.auth()
        user = self.gl.user
        return {"id": user.id, "username": user.username}

    def get_projects(self, limit: int = 100) -> List[dict]:
        """Fetch projects with pagination."""
        projects = self.gl.projects.list(
            get_all=False,
            per_page=limit,
            order_by="last_activity_at",
            sort="desc"
        )
        return [{"id": p.id, "path_with_namespace": p.path_with_namespace} for p in projects]
```

**Key Methods**:
- `test_connection()` - Validate credentials
- `get_projects()` / `get_groups()` - Fetch resources
- `create_mirror()` - Create push/pull mirrors
- `get_mirror_status()` - Check mirror sync status
- `trigger_mirror_update()` - Force sync
- `delete_mirror()` - Remove mirror

#### 5. Encryption (`app/core/encryption.py`)

**Singleton Pattern**:
```python
class Encryption:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        key = self._load_or_generate_key()
        self.fernet = Fernet(key)

    def encrypt(self, data: str) -> str:
        return self.fernet.encrypt(data.encode()).decode()

    def decrypt(self, encrypted_data: str) -> str:
        return self.fernet.decrypt(encrypted_data.encode()).decode()

encryption = Encryption()
```

**Usage**:
```python
# Store token
encrypted_token = encryption.encrypt(plaintext_token)
instance.encrypted_token = encrypted_token

# Retrieve token
plaintext_token = encryption.decrypt(instance.encrypted_token)
```

#### 6. Authentication (`app/core/auth.py`)

**HTTP Basic Auth**:
```python
security = HTTPBasic()

async def verify_credentials(credentials: HTTPAuthCredentials = Depends(security)) -> str:
    """Verify HTTP Basic Auth credentials."""
    if not settings.auth_enabled:
        return "anonymous"

    correct_username = secrets.compare_digest(credentials.username, settings.auth_username)
    correct_password = secrets.compare_digest(credentials.password, settings.auth_password)

    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"}
        )

    return credentials.username
```

**Usage in Routes**:
```python
@router.get("/api/instances")
async def list_instances(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)  # Auth dependency
):
    # Route implementation
```

### Frontend Components

#### 1. State Management (`app/static/js/app.js`)

**Global State Object**:
```javascript
const state = {
    instances: [],
    pairs: [],
    mirrors: [],
    tokens: [],
    groupDefaults: [],
    selectedPair: null,
    mirrorProjectInstances: { source: null, target: null },
    editing: {
        instanceId: null,
        pairId: null,
        mirrorId: null,
        tokenId: null
    }
};
```

**State Updates**:
```javascript
async function loadInstances() {
    const response = await fetch('/api/instances');
    state.instances = await response.json();
    renderInstances();
}
```

#### 2. Table Enhancement System

**Features**:
- Client-side sorting (click column headers)
- Search filtering (real-time)
- Row highlighting
- Inline editing

**Pattern**:
```javascript
function enhanceTable(table) {
    // Add sorting to headers
    const headers = table.querySelectorAll('th[data-sort]');
    headers.forEach(header => {
        header.addEventListener('click', () => sortTable(table, header));
    });

    // Add search filtering
    const searchInput = table.previousElementSibling.querySelector('input[type="search"]');
    if (searchInput) {
        searchInput.addEventListener('input', debounce(() => filterTable(table, searchInput.value), 300));
    }
}
```

#### 3. API Communication

**Fetch Pattern**:
```javascript
async function createInstance(data) {
    try {
        const response = await fetch('/api/instances', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail);
        }

        const instance = await response.json();
        showMessage('Instance created successfully', 'success');
        return instance;
    } catch (error) {
        showMessage(`Error: ${error.message}`, 'error');
        throw error;
    }
}
```

#### 4. Topology Visualization (`app/static/js/topology.js`)

**D3.js Force-Directed Graph**:
```javascript
const simulation = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id(d => d.id))
    .force("charge", d3.forceManyBody().strength(-400))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collision", d3.forceCollide().radius(40));

// Node selection and drilldown
node.on('click', (event, d) => {
    selectNode(d);
    showNodeDetails(d);
});

link.on('click', (event, d) => {
    selectLink(d);
    loadLinkMirrors(d);
});
```

## Development Workflow

### Setup

**Local Development**:
```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Create data directory
mkdir -p data

# Configure environment
cp .env.example .env
# Edit .env as needed

# Run application
uvicorn app.main:app --reload

# Run tests
pytest
```

**Docker Development**:
```bash
# Start services
docker-compose up

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### Testing

**Run All Tests**:
```bash
pytest
```

**Run Specific Test File**:
```bash
pytest tests/test_api_instances.py
```

**Run with Coverage**:
```bash
pytest --cov=app --cov-report=html
```

**Run Live GitLab Tests** (opt-in):
```bash
export E2E_LIVE_GITLAB=1
export E2E_GITLAB_URL="https://gitlab.example.com"
export E2E_GITLAB_TOKEN="glpat-..."
export E2E_GITLAB_GROUP_PATH="my-group/subgroup"
pytest -m live_gitlab -v
```

### Database Migrations

**Simple Migration Pattern** (SQLite only):
```python
# In app/database.py
async def _maybe_migrate_sqlite():
    """Add new columns to existing tables."""
    async with async_engine.begin() as conn:
        # Check if column exists
        result = await conn.execute(text("PRAGMA table_info(mirrors)"))
        columns = {row[1] for row in result}

        # Add column if missing
        if "enabled" not in columns:
            await conn.execute(text("ALTER TABLE mirrors ADD COLUMN enabled BOOLEAN DEFAULT 1"))
```

**Called During Startup**:
```python
async def init_db():
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _maybe_migrate_sqlite()
```

## Code Conventions

### Python Conventions

#### Naming

- **Variables/Functions**: `snake_case`
- **Classes**: `PascalCase`
- **Constants**: `UPPER_CASE`
- **Private Helpers**: `_leading_underscore`
- **Async Functions**: Always use `async def`

#### File Organization

```python
# 1. Standard library imports
from datetime import datetime
from typing import Optional, List

# 2. Third-party imports
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# 3. Local application imports
from app.database import get_db
from app.models import Mirror
from app.core.auth import verify_credentials

# 4. Module-level constants
DEFAULT_PAGE_SIZE = 100

# 5. Helper functions (private)
def _normalize_url(url: str) -> str:
    """Helper to normalize URLs."""
    pass

# 6. Router/API definitions
router = APIRouter(prefix="/api/resource", tags=["resource"])

# 7. Route handlers
@router.get("")
async def list_resources():
    pass
```

#### Type Hints

**Always use type hints**:
```python
# Good
async def get_instance(db: AsyncSession, instance_id: int) -> Optional[GitLabInstance]:
    result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == instance_id)
    )
    return result.scalar_one_or_none()

# Bad (no type hints)
async def get_instance(db, instance_id):
    result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == instance_id)
    )
    return result.scalar_one_or_none()
```

#### Pydantic Models

**Separate Create/Update/Response Models**:
```python
# Create model (all required fields)
class GitLabInstanceCreate(BaseModel):
    name: str
    url: str
    access_token: str
    description: Optional[str] = None

# Update model (partial updates)
class GitLabInstanceUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    access_token: Optional[str] = None
    description: Optional[str] = None

# Response model (what API returns)
class GitLabInstanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    api_user_id: Optional[int]
    api_username: Optional[str]
    description: Optional[str]
    created_at: datetime
    updated_at: datetime

    # Never include encrypted_token in responses!
```

#### Error Handling

**Use HTTPException with Descriptive Messages**:
```python
# Good
if not instance:
    raise HTTPException(
        status_code=404,
        detail=f"GitLab instance with ID {instance_id} not found"
    )

# Good (with context)
try:
    client = GitLabClient(instance.url, instance.encrypted_token)
    client.test_connection()
except Exception as e:
    raise HTTPException(
        status_code=400,
        detail=f"Failed to connect to GitLab instance: {str(e)}"
    )

# Bad (vague error)
raise HTTPException(status_code=400, detail="Error")
```

#### Async/Await

**All I/O Operations Must Be Async**:
```python
# Good
async def create_mirror(db: AsyncSession, mirror_data: MirrorCreate) -> Mirror:
    # Database operation - async
    result = await db.execute(select(InstancePair).where(InstancePair.id == mirror_data.instance_pair_id))
    pair = result.scalar_one_or_none()

    # GitLab API call - use sync wrapper in async context
    client = GitLabClient(instance.url, instance.encrypted_token)
    gitlab_mirror = client.create_mirror(...)  # Sync call wrapped in async function

    # Database operation - async
    await db.commit()
    await db.refresh(mirror)
    return mirror

# Bad (blocking I/O in async function)
async def create_mirror(db: AsyncSession, mirror_data: MirrorCreate) -> Mirror:
    time.sleep(1)  # Don't use blocking sleep!
    # Use: await asyncio.sleep(1)
```

### JavaScript Conventions

#### Naming

- **Variables/Functions**: `camelCase`
- **Constants**: `UPPER_CASE`
- **Event Handlers**: `handle<Action>` or `on<Event>`
- **DOM Elements**: Descriptive names like `instanceTable`, `createButton`

#### Code Organization

```javascript
// 1. Constants
const API_BASE = '/api';
const DEBOUNCE_DELAY = 300;

// 2. State
const state = {
    instances: [],
    selectedId: null
};

// 3. Utility functions
function debounce(fn, delay) {
    // Implementation
}

// 4. API functions
async function fetchInstances() {
    // Implementation
}

// 5. Render functions
function renderInstances() {
    // Implementation
}

// 6. Event handlers
function handleCreateInstance(event) {
    event.preventDefault();
    // Implementation
}

// 7. Initialization
document.addEventListener('DOMContentLoaded', init);
```

#### Async/Await Pattern

**Use async/await for API Calls**:
```javascript
// Good
async function loadInstances() {
    try {
        const response = await fetch('/api/instances');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const instances = await response.json();
        state.instances = instances;
        renderInstances();
    } catch (error) {
        console.error('Failed to load instances:', error);
        showMessage('Failed to load instances', 'error');
    }
}

// Bad (promise chains)
function loadInstances() {
    fetch('/api/instances')
        .then(response => response.json())
        .then(instances => {
            state.instances = instances;
            renderInstances();
        })
        .catch(error => console.error(error));
}
```

### CSS Conventions

#### Design Tokens

**Use CSS Variables for Consistency**:
```css
:root {
    /* Colors */
    --primary-color: #fc6d26;
    --secondary-color: #1f75cb;
    --background: #f6f7f9;
    --text-primary: #111827;

    /* Spacing */
    --spacing-xs: 0.25rem;
    --spacing-sm: 0.5rem;
    --spacing-md: 1rem;
    --spacing-lg: 1.5rem;

    /* Borders */
    --radius-sm: 0.25rem;
    --radius-md: 0.5rem;
    --border-color: #e5e7eb;
}

/* Usage */
.button {
    background-color: var(--primary-color);
    padding: var(--spacing-md);
    border-radius: var(--radius-md);
}
```

#### Component Classes

**Use Descriptive, Semantic Class Names**:
```css
/* Good */
.instance-table { }
.mirror-status-badge { }
.topology-graph-container { }

/* Bad */
.tbl { }
.badge1 { }
.container2 { }
```

## Database Schema

### Relationships

```
GitLabInstance (1)
    ├──> InstancePair (N) via source_instance_id
    ├──> InstancePair (N) via target_instance_id
    └──> GroupAccessToken (N) via gitlab_instance_id

InstancePair (1)
    ├──> Mirror (N) via instance_pair_id
    └──> GroupMirrorDefaults (N) via instance_pair_id

GroupAccessToken
    - Logical key: (gitlab_instance_id, group_path)

GroupMirrorDefaults
    - Logical key: (instance_pair_id, group_path)
```

### Cascade Delete Behavior

**Application-Layer Cascades**:
```python
# Deleting an instance deletes its pairs and mirrors
async def delete_instance(db: AsyncSession, instance_id: int):
    # 1. Find all pairs using this instance
    pairs_result = await db.execute(
        select(InstancePair).where(
            (InstancePair.source_instance_id == instance_id) |
            (InstancePair.target_instance_id == instance_id)
        )
    )
    pairs = pairs_result.scalars().all()

    # 2. Delete all mirrors for these pairs
    for pair in pairs:
        await db.execute(delete(Mirror).where(Mirror.instance_pair_id == pair.id))
        await db.execute(delete(GroupMirrorDefaults).where(GroupMirrorDefaults.instance_pair_id == pair.id))

    # 3. Delete the pairs
    await db.execute(delete(InstancePair).where(InstancePair.id.in_([p.id for p in pairs])))

    # 4. Delete group tokens
    await db.execute(delete(GroupAccessToken).where(GroupAccessToken.gitlab_instance_id == instance_id))

    # 5. Delete the instance
    await db.execute(delete(GitLabInstance).where(GitLabInstance.id == instance_id))

    await db.commit()
```

### Settings Resolution Hierarchy

**Three-Tier Configuration**:
```python
def resolve_mirror_settings(mirror: Mirror, group_defaults: Optional[GroupMirrorDefaults], pair: InstancePair) -> dict:
    """
    Resolve effective mirror settings from three sources:
    1. Per-mirror overrides (highest priority)
    2. Group-level defaults
    3. Pair defaults (lowest priority)
    """
    effective = {}

    for setting in ["mirror_direction", "mirror_protected_branches", "mirror_overwrite_diverged"]:
        # Try mirror-level override
        mirror_value = getattr(mirror, setting)
        if mirror_value is not None:
            effective[setting] = mirror_value
            continue

        # Try group-level default
        if group_defaults:
            group_value = getattr(group_defaults, setting)
            if group_value is not None:
                effective[setting] = group_value
                continue

        # Fall back to pair default
        effective[setting] = getattr(pair, setting)

    return effective
```

### Timestamps

**All Models Include Automatic Timestamps**:
```python
created_at: Mapped[datetime] = mapped_column(
    DateTime,
    default=datetime.utcnow
)

updated_at: Mapped[datetime] = mapped_column(
    DateTime,
    default=datetime.utcnow,
    onupdate=datetime.utcnow
)
```

**Note**: SQLite doesn't automatically update `updated_at`, so explicit updates may be needed:
```python
instance.updated_at = datetime.utcnow()
await db.commit()
```

## API Patterns

### Standard CRUD Endpoints

**Consistent Pattern for All Resources**:
```python
# List all
@router.get("", response_model=List[ResourceResponse])
async def list_resources(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    result = await db.execute(select(Resource))
    return result.scalars().all()

# Create
@router.post("", response_model=ResourceResponse, status_code=201)
async def create_resource(
    resource_data: ResourceCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    resource = Resource(**resource_data.model_dump())
    db.add(resource)
    await db.commit()
    await db.refresh(resource)
    return resource

# Get one
@router.get("/{id}", response_model=ResourceResponse)
async def get_resource(
    id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    result = await db.execute(select(Resource).where(Resource.id == id))
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail=f"Resource {id} not found")
    return resource

# Update
@router.put("/{id}", response_model=ResourceResponse)
async def update_resource(
    id: int,
    resource_data: ResourceUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    result = await db.execute(select(Resource).where(Resource.id == id))
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail=f"Resource {id} not found")

    # Update only provided fields
    for field, value in resource_data.model_dump(exclude_unset=True).items():
        setattr(resource, field, value)

    await db.commit()
    await db.refresh(resource)
    return resource

# Delete
@router.delete("/{id}", status_code=204)
async def delete_resource(
    id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    result = await db.execute(select(Resource).where(Resource.id == id))
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail=f"Resource {id} not found")

    await db.delete(resource)
    await db.commit()
```

### Request/Response Patterns

**Use Pydantic Models for Validation**:
```python
# Request body
class MirrorCreate(BaseModel):
    instance_pair_id: int
    source_project_id: int
    source_project_path: str
    target_project_id: int
    target_project_path: str
    mirror_protected_branches: Optional[bool] = None
    mirror_overwrite_diverged: Optional[bool] = None
    # ... other optional overrides

# Response body
class MirrorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    instance_pair_id: int
    source_project_id: int
    source_project_path: str
    target_project_id: int
    target_project_path: str
    # ... all fields including timestamps

    # Computed fields
    effective_direction: Optional[str] = None
    effective_protected_branches: Optional[bool] = None
```

### Error Responses

**HTTP Status Codes**:
- `200 OK` - Successful GET, PUT
- `201 Created` - Successful POST
- `204 No Content` - Successful DELETE
- `400 Bad Request` - Validation error, business rule violation
- `404 Not Found` - Resource doesn't exist
- `409 Conflict` - Resource already exists
- `500 Internal Server Error` - Unexpected failure

**Error Detail Formats**:
```python
# Simple string error
raise HTTPException(
    status_code=400,
    detail="Mirror direction cannot be changed after creation"
)

# Complex error with context
raise HTTPException(
    status_code=409,
    detail={
        "message": "Mirror already exists",
        "existing_mirror_id": existing_mirror.id
    }
)
```

### Field Presence Tracking

**Distinguish Between Null and Unset**:
```python
# Update endpoint
@router.put("/{id}")
async def update_mirror(id: int, mirror_data: MirrorUpdate, db: AsyncSession = Depends(get_db)):
    # Only update explicitly provided fields
    update_data = mirror_data.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(mirror, field, value)

    # This allows clearing overrides by sending null:
    # {"mirror_protected_branches": null}  -> clears override, inherits from group/pair
```

## Frontend Patterns

### State Management

**Centralized State Object**:
```javascript
const state = {
    // Data collections
    instances: [],
    pairs: [],
    mirrors: [],

    // UI state
    selectedPair: null,
    editing: {
        instanceId: null,
        pairId: null
    },

    // Cached data
    projectCache: new Map()
};

// Update state and re-render
function updateState(key, value) {
    state[key] = value;
    render();  // Trigger re-render
}
```

### Event Handling

**Event Delegation Pattern**:
```javascript
// Good (event delegation)
document.getElementById('instance-table').addEventListener('click', (e) => {
    if (e.target.matches('.edit-button')) {
        handleEdit(e.target.dataset.id);
    } else if (e.target.matches('.delete-button')) {
        handleDelete(e.target.dataset.id);
    }
});

// Bad (individual listeners)
document.querySelectorAll('.edit-button').forEach(button => {
    button.addEventListener('click', handleEdit);
});
```

### Form Handling

**Prevent Default and Validate**:
```javascript
async function handleCreateInstance(event) {
    event.preventDefault();

    const formData = new FormData(event.target);
    const data = {
        name: formData.get('name'),
        url: formData.get('url'),
        access_token: formData.get('access_token'),
        description: formData.get('description') || null
    };

    // Client-side validation
    if (!data.name || !data.url || !data.access_token) {
        showMessage('Please fill in all required fields', 'error');
        return;
    }

    try {
        const instance = await createInstance(data);
        event.target.reset();
        showMessage('Instance created successfully', 'success');
        await loadInstances();
    } catch (error) {
        // Error already shown by createInstance()
    }
}
```

### Debouncing

**Debounce Expensive Operations**:
```javascript
function debounce(fn, delay) {
    let timeoutId;
    return function(...args) {
        clearTimeout(timeoutId);
        timeoutId = setTimeout(() => fn.apply(this, args), delay);
    };
}

// Usage
searchInput.addEventListener('input', debounce((e) => {
    filterTable(table, e.target.value);
}, 300));
```

### Table Enhancement

**Sort and Filter Tables**:
```javascript
function enhanceTable(table) {
    // Add sorting
    const headers = table.querySelectorAll('th[data-sort]');
    headers.forEach(header => {
        header.style.cursor = 'pointer';
        header.addEventListener('click', () => {
            const sortKey = header.dataset.sort;
            const direction = header.classList.contains('sort-asc') ? 'desc' : 'asc';
            sortTable(table, sortKey, direction);

            // Update UI
            headers.forEach(h => h.classList.remove('sort-asc', 'sort-desc'));
            header.classList.add(`sort-${direction}`);
        });
    });
}
```

### Message Display

**User Feedback System**:
```javascript
function showMessage(message, type = 'info') {
    const container = document.getElementById('message-container');
    const messageEl = document.createElement('div');
    messageEl.className = `message message-${type}`;
    messageEl.textContent = message;

    container.appendChild(messageEl);

    // Auto-dismiss after 5 seconds
    setTimeout(() => {
        messageEl.classList.add('fade-out');
        setTimeout(() => messageEl.remove(), 300);
    }, 5000);
}
```

## Security Considerations

### Token Encryption

**Always Encrypt Before Storage**:
```python
# Good
instance = GitLabInstance(
    name=data.name,
    url=data.url,
    encrypted_token=encryption.encrypt(data.access_token)  # Encrypt!
)

# Bad
instance = GitLabInstance(
    name=data.name,
    url=data.url,
    encrypted_token=data.access_token  # Plain text - DON'T DO THIS!
)
```

**Never Expose Encrypted Tokens**:
```python
# Good (exclude from response)
class GitLabInstanceResponse(BaseModel):
    id: int
    name: str
    url: str
    # encrypted_token is NOT included

# Bad
class GitLabInstanceResponse(BaseModel):
    id: int
    name: str
    url: str
    encrypted_token: str  # DON'T expose this!
```

### URL Safety

**Prevent URL Injection**:
```python
from urllib.parse import quote

# Good (percent-encode credentials)
username = quote(token_name, safe="")
password = quote(token_value, safe="")
url = f"https://{username}:{password}@{hostname}/{project_path}.git"

# Bad (raw credentials)
url = f"https://{token_name}:{token_value}@{hostname}/{project_path}.git"
```

### Input Validation

**Validate All User Input**:
```python
# Pydantic provides automatic validation
class MirrorCreate(BaseModel):
    instance_pair_id: int  # Must be int
    source_project_id: int  # Must be int
    mirror_direction: Optional[str] = None

    @field_validator('mirror_direction')
    def validate_direction(cls, v):
        if v is not None and v not in ['push', 'pull']:
            raise ValueError('Direction must be "push" or "pull"')
        return v
```

### Authentication

**Constant-Time Comparison**:
```python
import secrets

# Good (prevents timing attacks)
correct_username = secrets.compare_digest(
    credentials.username,
    settings.auth_username
)

# Bad (vulnerable to timing attacks)
correct_username = (credentials.username == settings.auth_username)
```

### Safety Locks

**Prevent Breaking Changes**:
```python
# Example: Prevent changing instance URL if pairs exist
@router.put("/{id}")
async def update_instance(id: int, data: InstanceUpdate, db: AsyncSession = Depends(get_db)):
    instance = await get_instance_or_404(db, id)

    # Check if URL is being changed
    if data.url is not None and data.url != instance.url:
        # Check if instance is used by any pairs
        pairs_result = await db.execute(
            select(InstancePair).where(
                (InstancePair.source_instance_id == id) |
                (InstancePair.target_instance_id == id)
            ).limit(1)
        )
        if pairs_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Cannot change URL of instance used by existing pairs"
            )

    # Safe to update
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(instance, field, value)

    await db.commit()
    return instance
```

## Testing Guidelines

### Test Structure

**Use pytest with async support**:
```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_create_instance(client: AsyncClient):
    """Test creating a GitLab instance."""
    response = await client.post("/api/instances", json={
        "name": "Test Instance",
        "url": "https://gitlab.example.com",
        "access_token": "test-token",
        "description": "Test description"
    })

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Instance"
    assert data["url"] == "https://gitlab.example.com"
    assert "encrypted_token" not in data  # Never expose tokens
```

### Fixtures

**Use Fixtures for Common Setup**:
```python
@pytest.fixture
async def sample_instance(db_session):
    """Create a sample GitLab instance for testing."""
    from app.models import GitLabInstance
    from app.core.encryption import encryption

    instance = GitLabInstance(
        name="Test Instance",
        url="https://gitlab.example.com",
        encrypted_token=encryption.encrypt("test-token")
    )
    db_session.add(instance)
    await db_session.commit()
    await db_session.refresh(instance)
    return instance

# Usage
async def test_get_instance(client: AsyncClient, sample_instance):
    response = await client.get(f"/api/instances/{sample_instance.id}")
    assert response.status_code == 200
```

### Mocking GitLab Client

**Mock External API Calls**:
```python
from unittest.mock import MagicMock, patch

@pytest.mark.asyncio
async def test_create_mirror_success(client: AsyncClient, sample_pair):
    """Test successful mirror creation."""

    # Mock GitLab client
    with patch('app.api.mirrors.GitLabClient') as MockClient:
        mock_client = MagicMock()
        mock_client.create_mirror.return_value = {"id": 123}
        MockClient.return_value = mock_client

        response = await client.post("/api/mirrors", json={
            "instance_pair_id": sample_pair.id,
            "source_project_id": 1,
            "source_project_path": "group/project",
            "target_project_id": 2,
            "target_project_path": "group/project-mirror"
        })

        assert response.status_code == 201
        assert mock_client.create_mirror.called
```

### Testing Patterns

**Test Success and Failure Cases**:
```python
@pytest.mark.asyncio
async def test_update_instance_success(client, sample_instance):
    """Test successful instance update."""
    response = await client.put(f"/api/instances/{sample_instance.id}", json={
        "description": "Updated description"
    })
    assert response.status_code == 200
    assert response.json()["description"] == "Updated description"

@pytest.mark.asyncio
async def test_update_instance_not_found(client):
    """Test updating non-existent instance."""
    response = await client.put("/api/instances/9999", json={
        "description": "Updated"
    })
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_update_instance_url_locked(client, sample_instance, sample_pair):
    """Test that URL cannot be changed when pairs exist."""
    response = await client.put(f"/api/instances/{sample_instance.id}", json={
        "url": "https://new-gitlab.example.com"
    })
    assert response.status_code == 400
    assert "Cannot change URL" in response.json()["detail"]
```

### Database Cleanup

**Each Test Gets Fresh Database**:
```python
# In conftest.py
@pytest.fixture
async def db_session(engine, session_maker):
    """Provide a clean database session for each test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as session:
        yield session
```

### Test Organization

**Group Related Tests**:
```python
class TestInstanceCRUD:
    """Tests for GitLab instance CRUD operations."""

    async def test_create_instance(self, client):
        """Test instance creation."""
        pass

    async def test_list_instances(self, client, sample_instance):
        """Test listing instances."""
        pass

    async def test_update_instance(self, client, sample_instance):
        """Test instance update."""
        pass

    async def test_delete_instance(self, client, sample_instance):
        """Test instance deletion."""
        pass
```

## Common Tasks

### Adding a New API Endpoint

**Step-by-Step Process**:

1. **Define Pydantic Models** (in the router file or separate `schemas.py`):
```python
class MyResourceCreate(BaseModel):
    name: str
    value: int

class MyResourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    value: int
    created_at: datetime
```

2. **Create Database Model** (in `app/models.py`):
```python
class MyResource(Base):
    __tablename__ = "my_resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

3. **Create Router** (in `app/api/my_resource.py`):
```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.database import get_db
from app.models import MyResource
from app.core.auth import verify_credentials

router = APIRouter(prefix="/api/my-resources", tags=["my-resources"])

@router.get("", response_model=List[MyResourceResponse])
async def list_resources(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    result = await db.execute(select(MyResource))
    return result.scalars().all()

@router.post("", response_model=MyResourceResponse, status_code=201)
async def create_resource(
    resource_data: MyResourceCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    resource = MyResource(**resource_data.model_dump())
    db.add(resource)
    await db.commit()
    await db.refresh(resource)
    return resource
```

4. **Register Router** (in `app/main.py`):
```python
from app.api import my_resource

app.include_router(my_resource.router)
```

5. **Add Migration** (if adding to existing database - in `app/database.py`):
```python
async def _maybe_migrate_sqlite():
    async with async_engine.begin() as conn:
        # Check if table exists
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='my_resources'"
        ))
        if not result.scalar_one_or_none():
            # Table will be created by Base.metadata.create_all
            pass
```

6. **Write Tests** (in `tests/test_api_my_resource.py`):
```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_create_resource(client: AsyncClient):
    response = await client.post("/api/my-resources", json={
        "name": "Test Resource",
        "value": 42
    })
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Resource"
    assert data["value"] == 42
```

### Adding a Database Column

**Process**:

1. **Update Model** (in `app/models.py`):
```python
class Mirror(Base):
    # ... existing fields ...
    new_field: Mapped[Optional[str]] = mapped_column(String(100))
```

2. **Add Migration** (in `app/database.py`):
```python
async def _maybe_migrate_sqlite():
    async with async_engine.begin() as conn:
        # Check if column exists
        result = await conn.execute(text("PRAGMA table_info(mirrors)"))
        columns = {row[1] for row in result}

        if "new_field" not in columns:
            await conn.execute(text(
                "ALTER TABLE mirrors ADD COLUMN new_field VARCHAR(100)"
            ))
```

3. **Update Pydantic Models**:
```python
class MirrorResponse(BaseModel):
    # ... existing fields ...
    new_field: Optional[str] = None

class MirrorUpdate(BaseModel):
    # ... existing fields ...
    new_field: Optional[str] = None
```

4. **Test Migration**:
```bash
# Stop application
docker-compose down

# Start application (migration runs automatically)
docker-compose up

# Check logs for migration
docker-compose logs app
```

### Adding Frontend Functionality

**Example: Adding a New Tab**:

1. **Update HTML** (in `app/templates/index.html`):
```html
<!-- Add tab button -->
<div class="tabs">
    <!-- ... existing tabs ... -->
    <button class="tab-button" data-tab="my-feature">My Feature</button>
</div>

<!-- Add tab content -->
<div id="my-feature-tab" class="tab-content">
    <h2>My Feature</h2>
    <div id="my-feature-container">
        <!-- Content here -->
    </div>
</div>
```

2. **Add JavaScript** (in `app/static/js/app.js`):
```javascript
// Add to state
state.myFeatureData = [];

// Add load function
async function loadMyFeature() {
    try {
        const response = await fetch('/api/my-feature');
        state.myFeatureData = await response.json();
        renderMyFeature();
    } catch (error) {
        showMessage('Failed to load feature', 'error');
    }
}

// Add render function
function renderMyFeature() {
    const container = document.getElementById('my-feature-container');
    container.innerHTML = state.myFeatureData
        .map(item => `<div>${item.name}</div>`)
        .join('');
}

// Add to tab change handler
function handleTabChange(tabName) {
    // ... existing code ...
    if (tabName === 'my-feature') {
        loadMyFeature();
    }
}
```

3. **Add CSS** (in `app/static/css/style.css`):
```css
#my-feature-container {
    padding: var(--spacing-lg);
}

.my-feature-item {
    background: var(--card-background);
    padding: var(--spacing-md);
    margin-bottom: var(--spacing-sm);
    border-radius: var(--radius-md);
}
```

### Modifying Mirror Settings

**Example: Adding a New Mirror Setting**:

1. **Add to All Models** (in `app/models.py`):
```python
class InstancePair(Base):
    # ... existing fields ...
    new_setting: Mapped[bool] = mapped_column(Boolean, default=False)

class Mirror(Base):
    # ... existing fields ...
    new_setting: Mapped[Optional[bool]] = mapped_column(Boolean)

class GroupMirrorDefaults(Base):
    # ... existing fields ...
    new_setting: Mapped[Optional[bool]] = mapped_column(Boolean)
```

2. **Add Migration**:
```python
async def _maybe_migrate_sqlite():
    async with async_engine.begin() as conn:
        for table in ["instance_pairs", "mirrors", "group_mirror_defaults"]:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            columns = {row[1] for row in result}
            if "new_setting" not in columns:
                default_value = "0" if table != "instance_pairs" else "0"
                await conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN new_setting BOOLEAN DEFAULT {default_value}"
                ))
```

3. **Update Settings Resolution** (in `app/api/mirrors.py`):
```python
MIRROR_SETTING_FIELDS = [
    "mirror_direction",
    "mirror_protected_branches",
    # ... existing fields ...
    "new_setting"  # Add here
]
```

4. **Update Frontend Forms**:
```html
<label>
    <input type="checkbox" name="new_setting" />
    Enable new setting
</label>
```

## Troubleshooting

### Common Issues

#### Database Migration Errors

**Problem**: Column already exists error
```
sqlite3.OperationalError: duplicate column name: new_field
```

**Solution**: Migration already ran. Either:
1. Remove the migration code
2. Add proper existence check:
```python
result = await conn.execute(text("PRAGMA table_info(table_name)"))
columns = {row[1] for row in result}
if "new_field" not in columns:
    # Add column
```

#### Encryption Key Issues

**Problem**: `cryptography.fernet.InvalidToken` error

**Solution**: Encryption key changed or corrupted
1. Check that `data/encryption.key` exists
2. Don't change the key after storing encrypted data
3. For development, delete database and key, restart app

#### CORS Errors

**Problem**: CORS errors when accessing API

**Solution**: Add CORS middleware (if needed for external access):
```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

#### GitLab API Errors

**Problem**: 401 Unauthorized from GitLab

**Solution**:
1. Check token hasn't expired
2. Verify token has required scopes (`api` for instance tokens, `read_repository`/`write_repository` for group tokens)
3. Test token manually: `curl -H "PRIVATE-TOKEN: xxx" https://gitlab.example.com/api/v4/user`

#### Database Lock Errors

**Problem**: `sqlite3.OperationalError: database is locked`

**Solution**:
1. SQLite doesn't handle high concurrency well
2. Ensure async operations complete before starting new ones
3. Consider PostgreSQL for production if needed

### Debugging Tips

**Enable Debug Logging**:
```bash
# In .env
LOG_LEVEL=DEBUG
```

**Database Inspection**:
```bash
# Connect to SQLite database
sqlite3 data/mirrors.db

# List tables
.tables

# View schema
.schema mirrors

# Query data
SELECT * FROM mirrors;

# Exit
.quit
```

**API Testing**:
```bash
# List instances
curl -u admin:changeme http://localhost:8000/api/instances

# Create instance
curl -u admin:changeme -X POST http://localhost:8000/api/instances \
  -H "Content-Type: application/json" \
  -d '{"name":"Test","url":"https://gitlab.com","access_token":"xxx"}'
```

**Frontend Debugging**:
```javascript
// Add to app.js for debugging
window.debugState = () => console.log(state);

// In browser console
debugState()
```

### Performance Optimization

**Slow Database Queries**:
```python
# Add indexes (in model definition)
class Mirror(Base):
    __tablename__ = "mirrors"
    __table_args__ = (
        Index('idx_instance_pair', 'instance_pair_id'),
        Index('idx_source_project', 'source_project_id'),
    )
```

**Slow API Responses**:
```python
# Use pagination for large lists
@router.get("/api/mirrors")
async def list_mirrors(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Mirror).offset(skip).limit(limit)
    )
    return result.scalars().all()
```

## Best Practices Summary

### DO

✅ Use async/await for all I/O operations
✅ Encrypt all tokens before storage
✅ Use type hints everywhere
✅ Write tests for new features
✅ Use descriptive error messages
✅ Follow the three-tier settings hierarchy
✅ Validate user input with Pydantic
✅ Use constant-time comparison for auth
✅ Document complex logic with comments
✅ Use CSS variables for styling
✅ Handle errors gracefully
✅ Use dependency injection

### DON'T

❌ Expose encrypted tokens in API responses
❌ Use blocking I/O in async functions
❌ Store plaintext tokens
❌ Change database schema without migrations
❌ Modify instance URLs when pairs exist
❌ Use vague error messages
❌ Skip input validation
❌ Hardcode colors/spacing in CSS
❌ Use timing-vulnerable comparisons for auth
❌ Add database constraints that break cascades
❌ Mutate state without re-rendering (frontend)
❌ Skip testing

---

## Quick Reference

### Running the Application

```bash
# Local development
uvicorn app.main:app --reload

# Docker
docker-compose up

# Run tests
pytest

# Run with coverage
pytest --cov=app
```

### Common Commands

```bash
# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Format code
black app/ tests/

# Type checking (if using mypy)
mypy app/

# Database shell
sqlite3 data/mirrors.db
```

### File Locations

- **API Routes**: `app/api/*.py`
- **Models**: `app/models.py`
- **Database**: `app/database.py`
- **Config**: `app/config.py`
- **Frontend JS**: `app/static/js/app.js`
- **Frontend CSS**: `app/static/css/style.css`
- **Tests**: `tests/test_*.py`

### Key Patterns

**Create DB Object**:
```python
obj = MyModel(**data.model_dump())
db.add(obj)
await db.commit()
await db.refresh(obj)
```

**Query DB**:
```python
result = await db.execute(select(MyModel).where(MyModel.id == id))
obj = result.scalar_one_or_none()
```

**API Response**:
```python
if not obj:
    raise HTTPException(status_code=404, detail="Not found")
return obj
```

**Frontend Fetch**:
```javascript
const response = await fetch('/api/resource');
const data = await response.json();
state.resources = data;
renderResources();
```

---

This guide should serve as your comprehensive reference when working on the GitLab Mirror Wizard codebase. Follow these patterns and conventions to maintain consistency and quality across the project.
