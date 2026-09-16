# Test Report

## Commands run

```text
python --version                              # Python 3.10.11
python -m pip install -r requirements.txt
python manage.py check
python manage.py makemigrations core
python manage.py migrate --noinput             # SQLite smoke database
python manage.py test tests -v 1               # 31 tests passed
python manage.py export_openapi --output openapi.json
python -m compileall -q .
```

## Results

- Python 3.10.11 and Django 3.2.24 verified.
- Django Ninja 0.22.2 installed; OpenAPI generated successfully.
- SQLite migration smoke test passed.
- Model/API/login/role/person/file/scan/live/report/ticket/export/pagination/OpenAPI tests: 31 passed.
- OpenAPI contains 74 generated API paths plus docs/schema endpoints.
- PostgreSQL integration was not passed: local connection failed with `fe_sendauth: no password supplied`; no production database was changed.
- GaussDB compatibility and Laravel golden-file comparisons were not run because no target server/driver/fixture was supplied.

Export layouts, Qiniu uploads, SMTP, Celery/Redis, real PostgreSQL 9.6 behavior and large-file limits remain environment tests.
