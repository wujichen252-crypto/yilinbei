# GaussDB Deployment

## Validation status

No production GaussDB server, exact edition/version, compatibility mode, or vendor driver was supplied. Production GaussDB validation is therefore **not complete**. This document is a checklist, not a claim of verification.

## Environment

Set values outside source control:

```text
DB_ENGINE=<vendor-validated Django GaussDB backend>
GAUSSDB_ENGINE=<vendor backend path>
GAUSSDB_HOST=<private endpoint>
GAUSSDB_PORT=<vendor port>
GAUSSDB_NAME=<database>
GAUSSDB_USER=<service account>
GAUSSDB_PASSWORD=<secret manager value>
GAUSSDB_OPTIONS=<JSON driver options>
GAUSSDB_COMPATIBILITY_MODE=<vendor value>
```

The PostgreSQL backend in `.env.example` is development-only. Do not deploy it against GaussDB until the vendor driver and mode are approved.

## Initialization and verification

1. Install Python 3.10.11 and the vendor-supported GaussDB driver while keeping Django 3.2.24/Ninja 0.22.2 pinned.
2. Back up and run `python manage.py migrate --noinput` in a maintenance window.
3. Run `python manage.py seed_tickets` only after checking reference-row duplicates.
4. Import anonymized Laravel data with `python manage.py import_laravel_data <csv>` and synchronize sequences using vendor-approved SQL.
5. Verify bigint identity allocation, JSON read/write, Chinese collation/search, timezone, pagination/order, unique constraints, logical links, soft deletes, transaction isolation, and ticket row locks.
6. Record driver/version/mode, dataset, SQL logs, result and rollback point for every check. A failed check blocks cutover.
