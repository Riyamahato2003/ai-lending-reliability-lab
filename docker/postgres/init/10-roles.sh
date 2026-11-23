#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v runtime_password="$APP_DB_PASSWORD" \
  -v lab_password="$LAB_DB_PASSWORD" <<'SQL'
CREATE ROLE lending_runtime LOGIN PASSWORD :'runtime_password';
CREATE ROLE lending_lab_operator LOGIN PASSWORD :'lab_password';
SQL
