#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DB_NAME="${LOCAL_DEMO_DB:-lending_lab_showcase}"
if [[ ! "$DB_NAME" =~ ^[a-z_][a-z0-9_]*$ ]]; then
  echo "LOCAL_DEMO_DB must be a lowercase PostgreSQL identifier" >&2
  exit 2
fi
for command_name in psql createdb npm; do
  if ! command -v "$command_name" >/dev/null; then
    echo "Missing required command: $command_name" >&2
    exit 2
  fi
done
if [[ ! -x .venv/bin/python || ! -x .venv/bin/uvicorn ]]; then
  echo "Create the project's Python virtual environment and install requirements first." >&2
  exit 2
fi
if [[ "$(psql -d postgres -Atqc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")" != "1" ]]; then
  echo "Creating dedicated synthetic demo database: $DB_NAME"
  createdb -O lending_migrator "$DB_NAME"
fi
export APP_MODE=local_demo
export DATABASE_URL="postgresql://lending_runtime@localhost:5432/$DB_NAME"
export MIGRATION_DATABASE_URL="postgresql://lending_migrator@localhost:5432/$DB_NAME"
export LAB_DATABASE_URL="postgresql://lending_lab_operator@localhost:5432/$DB_NAME"
.venv/bin/python -m lending_lab.migrate
(
  cd frontend
  [[ -d node_modules ]] || npm ci
  npm run build
)
echo "Verity is available locally at http://127.0.0.1:8000"
echo "Press Ctrl+C to stop the local demo."
exec .venv/bin/uvicorn lending_lab.api:app --host 127.0.0.1 --port 8000
