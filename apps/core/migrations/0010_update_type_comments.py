# 中小学端（type=5）落地后同步数据库列注释。
# 0007_db_comments 里的 users.type / report_draft.scope 注释只写到 4；该迁移可能
# 已在远端库应用，不便回改，这里只对这两列重发 COMMENT ON（覆盖旧注释）。
# 不读写、不改任何表数据。非 PostgreSQL 后端（如测试用的 sqlite）不支持
# COMMENT ON，直接跳过 —— 与 0007_db_comments 同一处理方式。
from django.db import migrations

COLUMN_COMMENTS = {
    "users": {
        "type": "账号类型：0=学校 1=市州（只读，报名功能已移出） 2=组委会 3=管理员 4=省级 5=中小学端",
    },
    "report_draft": {
        "scope": "草稿业务线，取值同账号类型：0=学校 1=市州（存量只读） 4=省级 5=中小学端",
    },
}


def _literal(text):
    return "'" + text.replace("'", "''") + "'"


def _present_columns(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema()"
        )
        present = {}
        for table, column in cursor.fetchall():
            present.setdefault(table, set()).add(column)
    return present


def apply_comments(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    quote = connection.ops.quote_name
    present = _present_columns(connection)
    with connection.cursor() as cursor:
        for table, columns in COLUMN_COMMENTS.items():
            if table not in present:
                continue
            for column, comment in columns.items():
                if column not in present[table]:
                    continue
                cursor.execute("COMMENT ON COLUMN %s.%s IS %s" % (
                    quote(table), quote(column), _literal(comment)))


def drop_comments(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    quote = connection.ops.quote_name
    present = _present_columns(connection)
    with connection.cursor() as cursor:
        for table, columns in COLUMN_COMMENTS.items():
            if table not in present:
                continue
            for column in columns:
                if column not in present[table]:
                    continue
                cursor.execute("COMMENT ON COLUMN %s.%s IS NULL" % (quote(table), quote(column)))


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_report_person_conductor_instructor_rule"),
    ]

    operations = [
        migrations.RunPython(apply_comments, drop_comments),
    ]
