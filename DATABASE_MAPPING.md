# Database Mapping

## Compatibility rules

- Existing Laravel table/column names are retained with `db_table` and explicit fields.
- Laravel migrations contain logical integer relationships but no database foreign keys. Django keeps `*_id` integers and resolves relations in services, avoiding destructive constraints and width changes.
- `$table->id()` and `bigIncrements()` map to `BigAutoField`.
- Soft-delete fields are explicit; default managers hide deleted `User`, `Report`, and `ReportPerson` rows while `all_objects` supports restore/import.
- The source decoded JSON stored in varchar columns. Target JSON fields require a reviewed legacy-data normalization before changing physical column types.

## Table map

| Laravel table | Django model | Key constraints/notes |
| --- | --- | --- |
| `users` | `User` | unique username; soft delete; nullable compatibility `parent_id` |
| `password_resets` | `PasswordReset` | source has no primary key; target ORM id requires review before legacy attach |
| `failed_jobs` | framework/Celery boundary | source framework table; no active job was found |
| `personal_access_tokens` | `PersonalAccessToken` | unique digest; tokenable morph index; four-hour cleanup |
| `report` | `Report` | user index; soft delete; logical file/spectrum IDs |
| `report_person` | `ReportPerson` | person index; soft delete; logical report/person IDs |
| `person` | `Person` | unique identity card; name/card consistency is service-enforced |
| `logs` | `Logs` | content index; actor/type/content audit record |
| `files` | `Files` | external object URL metadata |
| `scan_files` | `ScanFiles` | JSON file list; `(user_id,type)` application upsert key |
| `recommend` | `Recommend` | application upsert by user |
| `live_report` | `LiveReport` | logical child links to leader/crew |
| `crew`, `leader` | `Crew`, `Leader` | nullable dates, position/type/linkman fields |
| `draw` | `Draw` | nullable ordering `index` |
| `ticket` | `Ticket` | capacity checked under row lock |
| `ticket_subscribe` | `TicketSubscribe` | duplicate `(ticket_id,card)` service rule |
| `students`, `statistics` | `Student`, `Statistics` | models referenced by source but no migrations supplied |

## PostgreSQL 9.2.4 and GaussDB risks

1. JSON/JSONB support and indexes vary by GaussDB edition/mode; validate every JSON field with the vendor driver.
2. Synchronize bigint sequences after importing Laravel IDs using vendor-approved SQL; never reset production sequences without a backup.
3. Test Chinese collation, `icontains`/LIKE behavior, deterministic ordering, and timezone conversion (`Asia/Shanghai` vs Laravel `PRC`).
4. Pagination uses ORM offset/limit and explicit ordering. Core code uses no PostgreSQL-only function, array operator, or raw SQL.
5. Validate unique constraints, logical links, soft deletes, transaction isolation, and `select_for_update()` capacity behavior on both engines.
