# Deployment

## Local PostgreSQL 9.2.4

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

## CORS (frontend dev server)

The vite dev server (`http://localhost:8080`) calls this API cross-origin, so
every deployment that the frontend develops against must whitelist its origin.
`corsheaders` is wired up already (`CorsMiddleware` sits before
`CommonMiddleware`); only the origin list is deployment-specific.

In the server's `.env` (loaded by `load_dotenv` from the project root) or the
process environment:

```
CORS_ALLOWED_ORIGINS=http://localhost:8080,http://127.0.0.1:8080
```

Restart the app process afterwards. Verify with a preflight request — the
response must contain `access-control-allow-origin`:

```bash
curl -s -o /dev/null -D - -X OPTIONS http://<server>:9999/api/login \
  -H "Origin: http://localhost:8080" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type,authorization" \
  | grep -i access-control
```

`CORS_ALLOW_HEADERS`, `CORS_ALLOW_METHODS` and `CORS_ALLOW_CREDENTIALS` keep
their library defaults (headers already include `authorization`/`content-type`;
the API uses Bearer tokens, not cookies).

Phones on the LAN reach the dev server by IP, whose origin is
`http://<dev-ip>:8080` — whitelist those with `CORS_ALLOWED_ORIGIN_REGEXES`
(see `.env.example`) rather than enumerating IPs.

Public deployments (API on a public IP, frontend served through a natappfree
tunnel) additionally need the server IP and the tunnel origin accepted. Free
natapp tunnels get a random subdomain on every restart, so whitelist the
suffix — a leading dot in `ALLOWED_HOSTS`, a full-match regex in
`CORS_ALLOWED_ORIGIN_REGEXES` — instead of the current subdomain:

```
ALLOWED_HOSTS=localhost,127.0.0.1,47.108.29.34,.natappfree.cc
CORS_ALLOWED_ORIGINS=http://localhost:8080,http://localhost:5173,http://47.108.29.34
CORS_ALLOWED_ORIGIN_REGEXES=^http://[a-z0-9-]+\.natappfree\.cc$
CSRF_TRUSTED_ORIGINS=http://localhost:8080,http://localhost:5173,http://wa9b8afb.natappfree.cc
```

`CSRF_TRUSTED_ORIGINS` has no regex support, so update its tunnel entry
manually when the subdomain changes. Restart the process, then verify
preflight and the actual GET from the frontend's origin:

```bash
curl -s -o /dev/null -D - -X OPTIONS http://47.108.29.34/api/school/index/total \
  -H "Origin: http://wa9b8afb.natappfree.cc" \
  -H "Access-Control-Request-Method: GET" | grep -i access-control
curl -s -o /dev/null -D - http://47.108.29.34/api/school/index/total \
  -H "Origin: http://wa9b8afb.natappfree.cc" | grep -iE "HTTP/|access-control"
```

A GET that returns `400` without any CORS header means `ALLOWED_HOSTS` is
still rejecting the `Host` header — fix that first; corsheaders never
overrides a 400, so the browser will report it as a bare "CORS error".

Never set `CORS_ALLOW_ALL_ORIGINS=True`: the `/api/ticket/*` endpoints are
unauthenticated and return subscribers' ID numbers and phone numbers, so any
website would be able to read them. (`CORS_ALLOW_ALL_ORIGINS` is also not
wired to an env var in `settings.py`, so a value in `.env` would silently
do nothing.) The same applies to `ALLOWED_HOSTS=*`: host-header validation
is a real defense, and the dot-suffix form above covers every tunnel
subdomain without opening the API to arbitrary hosts.

## Production

Use a secret manager for `SECRET_KEY`, database/Qiniu/SMTP credentials. Set `DEBUG=False`, explicit `ALLOWED_HOSTS`, HTTPS redirect/HSTS and secure cookies. Run migrations from a reviewed image after a backup, serve `django_config.wsgi:application` behind a reverse proxy, restrict `/admin/`, and put media on durable storage.

## Rollback

Stop writes, record the release/migration state, route traffic to the unchanged Laravel release where possible, and restore a reviewed database snapshot or reverse only an approved reversible migration. Never run `flush`, `reset`, `DROP`, or unreviewed `--fake` operations in production.

Django 3.2.24 and Laravel 7 are old security-maintenance baselines. They are pinned as requested; schedule a separate CVE review and contract-tested upgrade project.
