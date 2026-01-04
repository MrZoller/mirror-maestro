# Database Migrations

This directory contains Alembic database migrations for Mirror Maestro.

## Quick Start

### Generate a new migration after model changes:
```bash
alembic revision --autogenerate -m "description of changes"
```

### Apply all pending migrations:
```bash
alembic upgrade head
```

### Rollback the last migration:
```bash
alembic downgrade -1
```

### View current migration status:
```bash
alembic current
```

### View migration history:
```bash
alembic history
```

## Best Practices

1. **Always review generated migrations** - Autogenerate may miss some changes or create incorrect migrations
2. **Test migrations on a copy of production data** before applying to production
3. **Keep migrations small and focused** - One logical change per migration
4. **Write reversible migrations** - Ensure `downgrade()` properly reverses `upgrade()`
5. **Never edit migrations that have been applied** to production databases

## Docker Usage

When running in Docker:
```bash
docker-compose exec app alembic upgrade head
```

## Initial Setup

For a fresh database, the application will create tables automatically via `Base.metadata.create_all()`.
However, once you start using Alembic, you should:

1. Create a baseline migration for the existing schema
2. Stamp the database with that revision: `alembic stamp head`
3. Use Alembic for all future schema changes
