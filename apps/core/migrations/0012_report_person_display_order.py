# 指导教师表内行下标字段（第十二届新增）。
# report_person.display_order：指挥在报名表单「指导教师」表里的行下标（0 起）。
# 表单里「教师+指挥」那行是自动带入的，它在表里的序号取决于进表那一刻已填了
# 几位指导老师，这个信息没有别处可推；存下来前端重进编辑页才能原样还原，
# 否则会退回默认（排最后），「灰行1/手填2」会翻成「灰行2/手填1」。
# 只有「教师+指挥」那一行有值，其余 NULL；NULL = 没记过（老数据），前端按原有
# 默认处理，不回填。本列不参与任何导出/校验/排序（导出用 signature_order，
# 人数用 position），不改变任何现有输出。见 0011 的 signature_order。
# 非 PostgreSQL 后端（如测试用的 sqlite）不支持 COMMENT ON，跳过 —— 同 0010。
from django.db import migrations, models


def apply_comment(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    comment = ("指导教师表内行下标（0 起）：仅「教师+指挥」带入行有值，"
               "空=未记录（老数据），前端按默认排最后")
    quote = connection.ops.quote_name
    literal = "'" + comment.replace("'", "''") + "'"
    with connection.cursor() as cursor:
        cursor.execute("COMMENT ON COLUMN %s.%s IS %s" % (
            quote("report_person"), quote("display_order"), literal))


def drop_comment(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute("COMMENT ON COLUMN %s.%s IS NULL" % (
            quote("report_person"), quote("display_order")))


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_report_person_signature_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="reportperson",
            name="display_order",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.RunPython(apply_comment, drop_comment),
    ]
