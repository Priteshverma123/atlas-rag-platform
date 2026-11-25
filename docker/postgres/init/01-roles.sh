#!/bin/sh
# Runtime role for the application. It does NOT own the tables, so row-level security policies
# always apply to it. Migrations run as the owner role (POSTGRES_USER).
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
  DO \$\$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'atlas_app') THEN
      CREATE ROLE atlas_app LOGIN PASSWORD '${ATLAS_APP_DB_PASSWORD}';
    END IF;
  END \$\$;
  GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO atlas_app;
SQL
