#!/bin/sh
# Pulse (tracing) gets its own database so it can be reused by other projects or split out.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "CREATE DATABASE pulse"
