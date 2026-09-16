# Laravel to Django Ninja Inventory

## Source baseline

- Laravel framework: `v7.30.6` from `composer.lock`; PHP requirement is `^7.2.5 || ^8.0`.
- Sanctum: `v2.15.1`; API prefix is `/api`; source middleware removes inactive tokens after four hours.
- Source project was inspected read-only. The original PHP project and production data were not changed.
- Target code is in this directory and pins Python 3.10.11, Django 3.2.24 and Django Ninja 0.22.2.

## Modules

| Module | Source | Target | State |
| --- | --- | --- | --- |
| Authentication | `Api/AuthController`, Sanctum | `apps/api/views.py`, `PersonalAccessToken` | Implemented/tested |
| Roles | Admin/Committee/City/Province/School middleware | bearer auth + `role_error()` | Implemented/tested |
| Programme reports | role ReportControllers | report service and scoped routers | Implemented |
| People | `PersonController` trait | `store_people()` and report links | Implemented/tested |
| Live reports | Online/Committee controllers | live endpoint and nested row replacement | Implemented |
| Files/scans | File/ScanFiles controllers | metadata, JSON lists, Qiniu token | Implemented; credentials required |
| Recommendations | role RecommendControllers | scope/admin endpoints | Implemented |
| Statistics | role IndexControllers | compatible status/group counters | Implemented |
| Tickets | TicketController | atomic row-lock booking | Implemented/tested |
| Draw/exports | ChouQian/Export controllers | `apps/api/export_services.py`, openpyxl/reportlab responses | Implemented; golden files pending |
| Admin/audit | users, persons, logs, reviews | Ninja routes, Django admin, Logs | Implemented |
| Jobs/events | no active source registration found | Celery boundary and management commands | No active source behavior |

## Route and controller mapping

All source routes in `routes/api.php` are mounted under `/api` in `django_config/urls.py`. The generated OpenAPI document is `openapi.json`; interactive documentation is `/api/docs`.

| Source route family | Target |
| --- | --- |
| `/login`, `/logout`, `/user`, `/qiniu/token` | `login`, `logout`, `user_update`, `qiniu_token` |
| `/file/*`, `/scan/*` | file and scan operations |
| `/export/*`, `/live/*` | export and live operations |
| `/admin/*` | admin statistics/review/user/person/log/draw/export operations |
| `/committee/*` | committee statistics/review/recommend/live/user operations |
| `/city/*`, `/school/*`, `/province/*` | scoped statistics/reports/recommend operations |
| `/ticket/*` | public ticket list/message/make/my operations |

The response contract is preserved: success/failure envelopes are `{code,msg,data}`, pagination is `{data,count,code,msg}`, and role failures are HTTP 403 with `{error:"无该页面操作权限！"}`.

## Models and data behavior

`apps/core/models.py` maps the Laravel tables without renaming. `User`, `Report`, and `ReportPerson` implement soft-delete-aware default managers. `Report`, `ScanFiles`, `LiveReport`, and `Recommend` expose JSON values through Django ORM fields. Logical `*_id` links remain integers because the source migrations declared no foreign-key constraints.

Source Artisan utilities are represented by `seed_tickets`, `import_laravel_data`, and `export_openapi`. Qiniu, SMTP, Redis/Celery and PDF/image integrations are environment-driven.
