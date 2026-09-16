# API Compatibility

## Response and authentication contract

- Success: HTTP 200, `{ "code": 0, "msg": "...", "data": ... }`.
- Business failure: normally HTTP 200, `{ "code": 1, "msg": "...", "data": null }`.
- Pagination: `{ "data": [...], "count": N, "code": 0, "msg": "" }`.
- Role denial: HTTP 403, `{ "error": "无该页面操作权限！" }`.
- Missing/invalid bearer: HTTP 401.

The returned token is an `id|random` bearer value; only its SHA-256 digest is stored. Laravel bcrypt password hashes are accepted by Django's `check_password`.

## Preserved endpoint behavior

- Report lists accept `limit`, `page`, `keyword`, `status`, and `group`; role and current-user scopes match the source controllers.
- Report create/update accepts `person[]`; people are upserted by `card` and reject a card/name mismatch. Updates reset status to `0` and replace links transactionally.
- Province creates enforce eight non-deleted reports per user; city/school/province recommend endpoints upsert by user.
- Live updates require ownership and replace leader/crew child rows in one transaction.
- Ticket endpoints remain public; duplicate `(ticket_id,card)`, capacity, and source cutoff checks are transactional.
- Export endpoints preserve the Laravel column headings, report-code/time/status transformations, instrument totals, draw workbook sheet titles and fixed Chinese attachment names.
- Admin/committee review accepts scalar or list `id`, `status`, and `remark`.
- Committee user lists only types `0/4` and committee-created users are forced to type `0`, matching Laravel's separate controller.

## Known compatibility decisions

1. The source ticket cutoff is fixed at `2024-11-14 20:00:00`; the migrated endpoint rejects requests before that instant and permits them afterwards, exactly as source code does.
2. Source `school/index/percent` and `province/index/percent` routes have no Laravel method. Django returns an explicit empty rule payload and records this discrepancy in `MIGRATION_GAPS.md`.
3. Runtime fields `origin`, `territory`, `group_type`, and `table_id` are referenced by source code but absent from checked-in migrations. Django does not invent them in the physical schema; province response mapping is applied when available.
4. PDF/XLSX binary layouts need golden-file comparison with the consuming frontend/authority templates.

OpenAPI is generated at `openapi.json` with `python manage.py export_openapi --output openapi.json`.
