from contextlib import asynccontextmanager
import logging
import time
import uuid
from importlib.metadata import version, PackageNotFoundError
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import select

from app.config import settings
from app.database import init_db, migrate_mirrors_to_auto_tokens, drop_legacy_group_tables, AsyncSessionLocal
from app.api import instances, pairs, mirrors, export, topology, dashboard, backup, search, health, auth, users, issue_mirrors
from app.core.auth import verify_credentials, get_password_hash
from app.core.issue_scheduler import scheduler
from app.core.api_rate_limiter import limiter, rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded


def configure_logging():
    """Configure Python logging based on settings."""
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Set specific loggers
    logging.getLogger("uvicorn.access").setLevel(log_level)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.WARNING if log_level > logging.DEBUG else logging.INFO
    )

    logging.info(f"Logging configured at level: {settings.log_level.upper()}")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # XSS protection (legacy but still useful)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Content Security Policy
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "frame-ancestors 'none'"
        )

        # HSTS header (only in production with SSL)
        if settings.environment == "production" and settings.ssl_enabled:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log all HTTP requests with timing and correlation IDs."""

    async def dispatch(self, request: Request, call_next):
        # Generate request ID for correlation
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id

        # Skip logging for health checks and static files
        path = request.url.path
        if path.startswith("/static") or path in ("/health", "/api/health/quick"):
            return await call_next(request)

        start_time = time.time()
        method = request.method
        client_ip = request.client.host if request.client else "unknown"

        try:
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000

            # Log request details
            logging.info(
                f"[{request_id}] {method} {path} -> {response.status_code} "
                f"({duration_ms:.1f}ms) from {client_ip}"
            )

            # Add request ID to response headers for debugging
            response.headers["X-Request-ID"] = request_id

            return response

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logging.error(
                f"[{request_id}] {method} {path} -> ERROR "
                f"({duration_ms:.1f}ms) from {client_ip}: {e}"
            )
            raise


# Get version from package metadata (pyproject.toml)
try:
    __version__ = version("mirror-maestro")
except PackageNotFoundError:
    __version__ = "1.0.0"  # Fallback for development


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


def _check_default_credentials():
    """Warn if default credentials are still in use."""
    warnings = []

    if settings.auth_enabled and settings.auth_password == "changeme":
        warnings.append("AUTH_PASSWORD is set to default 'changeme'")

    if settings.multi_user_enabled and settings.initial_admin_password == "changeme":
        warnings.append("INITIAL_ADMIN_PASSWORD is set to default 'changeme'")

    if warnings:
        logging.warning("=" * 60)
        logging.warning("SECURITY WARNING: Default credentials detected!")
        for warning in warnings:
            logging.warning(f"  - {warning}")
        logging.warning("Please change these before deploying to production.")
        logging.warning("=" * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events for the application."""
    # Configure logging first
    configure_logging()

    # Log startup information
    logging.info(f"Starting {settings.app_title} v{__version__}")
    logging.info(f"Environment: {settings.environment}")

    # Check for default credentials
    _check_default_credentials()

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

    # Start issue sync scheduler
    try:
        await scheduler.start()
        logging.info("Issue sync scheduler started successfully")
    except Exception as e:
        logging.error(f"Failed to start issue sync scheduler: {e}", exc_info=True)

    yield

    # Shutdown - wait for all sync jobs to complete gracefully
    try:
        # Stop scheduler (waits for scheduled sync jobs)
        await scheduler.stop()
        logging.info("Issue sync scheduler stopped")

        # Wait for manual sync jobs
        from app.api.issue_mirrors import wait_for_manual_syncs
        await wait_for_manual_syncs(timeout=settings.sync_shutdown_timeout)
        logging.info("Manual sync tasks completed")

    except Exception as e:
        logging.error(f"Error during graceful shutdown: {e}", exc_info=True)


app = FastAPI(
    title=settings.app_title,
    description=settings.app_description,
    version=__version__,
    lifespan=lifespan
)

# Add middleware (order matters - first added = outermost)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestLoggingMiddleware)

# Configure rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

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
app.include_router(issue_mirrors.router)
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


@app.get("/favicon.ico")
@app.get("/favicon-128x128.png")
async def favicon_128():
    """Serve the 128x128 favicon for Retina displays."""
    return FileResponse("app/static/images/favicon-128x128.png", media_type="image/png")


@app.get("/favicon-64x64.png")
async def favicon_64():
    """Serve the 64x64 favicon for Retina displays."""
    return FileResponse("app/static/images/favicon-64x64.png", media_type="image/png")


@app.get("/favicon-32x32.png")
async def favicon_32():
    """Serve the 32x32 favicon."""
    return FileResponse("app/static/images/favicon-32x32.png", media_type="image/png")


@app.get("/favicon-16x16.png")
async def favicon_16():
    """Serve the 16x16 favicon."""
    return FileResponse("app/static/images/favicon-16x16.png", media_type="image/png")


@app.get("/apple-touch-icon.png")
async def apple_touch_icon():
    """Serve the Apple touch icon for iOS/Safari."""
    return FileResponse("app/static/images/apple-touch-icon.png", media_type="image/png")


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
