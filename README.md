# YLB Django Ninja Migration

The original Laravel project remains unchanged. The migrated application is in `django_backend/` and preserves the `/api` paths and response envelopes.

```powershell
cd django_backend
& C:\Users\Administrator\.pyenv\pyenv-win\versions\3.10.11\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Set SECRET_KEY and PostgreSQL values.
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_tickets
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

OpenAPI is available at `/api/docs` and `/api/openapi.json`. Read `MIGRATION_INVENTORY.md`, `API_COMPATIBILITY.md`, `DATABASE_MAPPING.md`, `GAUSSDB_DEPLOYMENT.md`, and `MIGRATION_GAPS.md` before attaching an existing database.

Project structure: `django_config/` settings/URLs/WSGI/ASGI; `apps/core/` ORM, token and audit services; `apps/api/` Ninja routes and management commands; `tests/` compatibility tests. Secrets stay in environment variables; `.env` must not be committed.
