# Migration Gaps

These items are not marked complete because the checked-in source or infrastructure is incomplete.

## Source/schema gaps

- No production SQL dump/schema snapshot was supplied; only 16 Laravel migration files were available.
- `users.parent_id` is used by controllers but absent from the users migration.
- `report.origin`, `report.territory`, `report.group_type`, and `report_person.table_id` are referenced at runtime but absent from migrations.
- `students`, `statistics`, `Team_Report`, `ReportFile` and several older activity models have no complete checked-in migration/route surface.
- `school/index/percent` and `province/index/percent` are registered routes with no Laravel controller method.
- No active source event/listener, scheduled task, queue job, notification or mail class was found; only framework configuration and Artisan import/export utilities exist.
- The compiled frontend references additional paths (`/api/rules`, `/api/files/`, `/api/chouqian/school/*`, `/api/chouqian/jiemu/*`, and `/api/v2/*`) that are not registered in `routes/api.php`. These must be confirmed against the live frontend before adding compatibility shims.

## Infrastructure gaps

- GaussDB type, version, compatibility mode and supported Django driver were not supplied. Production validation is outstanding.
- PostgreSQL 9.6 credentials/service were not supplied; the connection check failed with `fe_sendauth: no password supplied`.
- Qiniu, SMTP, Redis/Celery and wkhtmltopdf credentials/binaries were not supplied.

## Validation gaps

- Export binaries need golden-file comparison with the existing government frontend.
- Existing Sanctum token rows cannot be recovered from one-way hashes; users must log in again or receive a reviewed migration strategy.
- Legacy JSON-in-varchar values require a dry-run parser/quarantine report before type conversion.
- Identity-card/phone/address masking rules require data-owner confirmation; target responses currently follow the Laravel fields.

## Required human confirmations

1. Confirm GaussDB edition/version/mode and driver support.
2. Confirm physical schema for missing runtime columns/tables.
3. Run PostgreSQL and GaussDB integration suites with anonymized production-like data.
4. Approve ticket cutoff policy and export templates before cutover.
