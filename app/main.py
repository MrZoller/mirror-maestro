from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

from app.config import settings
from app.database import init_db, migrate_mirrors_to_auto_tokens, drop_legacy_group_tables
from app.api import instances, pairs, mirrors, export, topology, dashboard
from app.core.auth import verify_credentials


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events for the application."""
    # Startup
    await init_db()

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
app.include_router(dashboard.router)
app.include_router(instances.router)
app.include_router(pairs.router)
app.include_router(mirrors.router)
app.include_router(topology.router)
app.include_router(export.router)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request, _: str = Depends(verify_credentials)):
    """Serve the main web interface."""
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "title": settings.app_title,
            "auth_enabled": settings.auth_enabled,
        },
    )


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


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
