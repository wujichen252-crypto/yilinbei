# 报名特许字段：默认每所学校限报一支队伍、只能参加一个组别；
# can_report_twice=True 授予中小学合并办学的学校（小学组、中学组各一支）。
# 存量账号一律 False（只加带默认值的列，不搬迁任何数据；已报多支的存量学校
# 由管理员人工核对后勾选）。非 PostgreSQL 后端（测试用 sqlite）不支持 COMMENT ON，
# 直接跳过 —— 与 0007/0010 同一处理方式。
from django.db import migrations, models

COLUMN_COMMENT = (
    "报名特许：false=每所学校限报一支队伍、只能参加一个组别（默认）；"
    "true=允许报两支（中小学合并办学，每个组别仍限一支）"
)


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


def apply_comment(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    present = _present_columns(connection)
    if "users" not in present or "can_report_twice" not in present["users"]:
        return
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute("COMMENT ON COLUMN %s.%s IS %s" % (
            quote("users"), quote("can_report_twice"), _literal(COLUMN_COMMENT)))


def drop_comment(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    present = _present_columns(connection)
    if "users" not in present or "can_report_twice" not in present["users"]:
        return
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute("COMMENT ON COLUMN %s.%s IS NULL" % (
            quote("users"), quote("can_report_twice")))


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_update_type_comments"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="can_report_twice",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(apply_comment, drop_comment),
    ]
