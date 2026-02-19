#!/usr/bin/env bash
# Create the external Docker volume used by PostgreSQL.
# This must be run once before the first 'docker-compose up'.
#
# The volume is marked as external in docker-compose.yml so that
# 'docker-compose down -v' and 'docker volume prune' cannot
# accidentally destroy the database.
#
# Usage:
#   ./scripts/create-db-volume.sh                   # default project name
#   ./scripts/create-db-volume.sh my-project-name   # custom project name

set -euo pipefail

PROJECT="${1:-${COMPOSE_PROJECT_NAME:-mirror-maestro}}"
VOLUME_NAME="${PROJECT}_postgres_data"

if docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
    echo "Volume '$VOLUME_NAME' already exists."
else
    docker volume create "$VOLUME_NAME"
    echo "Volume '$VOLUME_NAME' created."
fi
