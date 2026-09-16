# Deployment

## Local PostgreSQL 9.6

```powershell
cd django_backend
& C:\Users\Administrator\.pyenv\pyenv-win\versions\3.10.11\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Set SECRET_KEY and DB_* in .env.
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_tickets
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Interactive API docs are `/api/docs`; OpenAPI JSON is `/api/openapi.json`.

## Production

Use a secret manager for `SECRET_KEY`, database/Qiniu/SMTP credentials. Set `DEBUG=False`, explicit `ALLOWED_HOSTS`, HTTPS redirect/HSTS and secure cookies. Run migrations from a reviewed image after a backup, serve `django_config.wsgi:application` behind a reverse proxy, restrict `/admin/`, and put media on durable storage.

## Rollback

Stop writes, record the release/migration state, route traffic to the unchanged Laravel release where possible, and restore a reviewed database snapshot or reverse only an approved reversible migration. Never run `flush`, `reset`, `DROP`, or unreviewed `--fake` operations in production.

Django 3.2.24 and Laravel 7 are old security-maintenance baselines. They are pinned as requested; schedule a separate CVE review and contract-tested upgrade project.
