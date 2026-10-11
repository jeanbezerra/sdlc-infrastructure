\getenv app_password SDLC_APP_PASSWORD
\getenv migrator_password SDLC_MIGRATOR_PASSWORD

SELECT 'CREATE ROLE sdlc_owner NOLOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sdlc_owner') \gexec
SELECT format('CREATE ROLE sdlc_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sdlc_app') \gexec
SELECT format('CREATE ROLE sdlc_migrator LOGIN NOINHERIT PASSWORD %L', :'migrator_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sdlc_migrator') \gexec
GRANT sdlc_owner TO sdlc_migrator;
SELECT 'CREATE DATABASE sdlc OWNER sdlc_owner'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'sdlc') \gexec

\connect sdlc
REVOKE ALL ON DATABASE sdlc FROM PUBLIC;
GRANT CONNECT ON DATABASE sdlc TO sdlc_app, sdlc_migrator;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION sdlc_owner;
GRANT USAGE ON SCHEMA app TO sdlc_app;
ALTER DEFAULT PRIVILEGES FOR ROLE sdlc_owner IN SCHEMA app
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sdlc_app;
ALTER DEFAULT PRIVILEGES FOR ROLE sdlc_owner IN SCHEMA app
    GRANT USAGE, SELECT ON SEQUENCES TO sdlc_app;
ALTER ROLE sdlc_app IN DATABASE sdlc SET search_path = app, pg_catalog;
ALTER ROLE sdlc_migrator IN DATABASE sdlc SET search_path = app, pg_catalog;
