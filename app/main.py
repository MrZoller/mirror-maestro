from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

from app.config import settings
from app.database import init_db
from app.api import instances, pairs, mirrors, export, tokens, group_defaults, topology, dashboard
from app.core.auth import verify_credentials


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events for the application."""
    # Startup
    await init_db()
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
app.include_router(tokens.router)
app.include_router(group_defaults.router)
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True
    )
