from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.config import settings
from app.database import init_db, migrate_mirrors_to_auto_tokens, drop_legacy_group_tables, AsyncSessionLocal
from app.api import instances, pairs, mirrors, export, topology, dashboard, backup, search, health, auth, users
from app.core.auth import verify_credentials, get_password_hash


async def _create_initial_admin():
    """Create initial admin user if multi-user mode is enabled and no users exist."""
    if not settings.multi_user_enabled:
        return

    from app.models import User

    async with AsyncSessionLocal() as db:
        # Check if any users exist
        result = await db.execute(select(User).limit(1))
        if result.scalar_one_or_none() is not None:
            logging.info("Users already exist, skipping initial admin creation")
            return

        # Create initial admin user
        admin = User(
            username=settings.initial_admin_username,
            email=settings.initial_admin_email or None,
            hashed_password=get_password_hash(settings.initial_admin_password),
            is_admin=True,
            is_active=True
        )
        db.add(admin)
        await db.commit()
        logging.info(f"Created initial admin user: {settings.initial_admin_username}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events for the application."""
    # Startup
    await init_db()

    # Create initial admin user if in multi-user mode
    try:
        await _create_initial_admin()
    except Exception as e:
        logging.error(f"Failed to create initial admin user: {e}", exc_info=True)

    # Migrate existing mirrors to use automatic project access tokens
    try:
        await migrate_mirrors_to_auto_tokens()
        # After successful migration, clean up legacy tables
        await drop_legacy_group_tables()
    except Exception as e:
        logging.error(f"Token migration failed: {e}")
        # Don't fail startup - mirrors without tokens can still work if
        # they were configured manually or use SSH

    yield
    # Shutdown (if needed)


app = FastAPI(
    title=settings.app_title,
    description=settings.app_description,
    version="0.1.0",
    lifespan=lifespan
)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Setup templates
templates = Jinja2Templates(directory="app/templates")

# Include API routers
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(dashboard.router)
app.include_router(instances.router)
app.include_router(pairs.router)
app.include_router(mirrors.router)
app.include_router(topology.router)
app.include_router(export.router)
app.include_router(backup.router)
app.include_router(search.router)
app.include_router(health.router)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """
    Serve the main web interface.

    The HTML page is served without authentication - the frontend handles
    showing the login modal when multi-user mode is enabled. This allows
    users to see the login form instead of getting a 401 error.
    """
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "title": settings.app_title,
            "auth_enabled": settings.auth_enabled,
            "multi_user_enabled": settings.multi_user_enabled,
        },
    )


@app.get("/health")
async def health_legacy():
    """
    Legacy health check endpoint for backward compatibility.

    For detailed health checks, use /api/health instead.
    For quick checks suitable for load balancers, use /api/health/quick.
    """
    from datetime import datetime
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/about")
async def about(_: str = Depends(verify_credentials)):
    """Return application version and project information."""
    return {
        "name": "Mirror Maestro",
        "version": app.version,
        "description": "Orchestrate GitLab mirrors across multiple instance pairs with precision",
        "repository": "https://github.com/MrZoller/mirror-maestro",
        "license": "MIT",
        "documentation": "https://github.com/MrZoller/mirror-maestro#readme"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True
    )
