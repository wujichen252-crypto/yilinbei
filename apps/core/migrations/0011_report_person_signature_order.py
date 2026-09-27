# 指导教师「署名排序」字段（第十二届新增）。
# report_person.signature_order：学校可改的署名序号，报名信息表 PDF / 后台导出的
# 指导老师先后顺序按它排；NULL=未填，排在已填序号之后、按关系行 id（提交顺序）。
# 教师当指挥固定占第一署名位（附件2口径），由导出逻辑 adviser_instructors 保证，
# 不落库。与 0009 的人数上限规则互不影响（那边只数 position，不看顺序）。
# 非 PostgreSQL 后端（如测试用的 sqlite）不支持 COMMENT ON，跳过 —— 同 0010。
from django.db import migrations, models


def apply_comment(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    comment = ("署名排序：指导老师导出顺序依据"
               "（教师指挥固定第一；其余按本列升序，空=按提交顺序）")
    quote = connection.ops.quote_name
    literal = "'" + comment.replace("'", "''") + "'"
    with connection.cursor() as cursor:
        cursor.execute("COMMENT ON COLUMN %s.%s IS %s" % (
            quote("report_person"), quote("signature_order"), literal))


def drop_comment(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute("COMMENT ON COLUMN %s.%s IS NULL" % (
            quote("report_person"), quote("signature_order")))


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_update_type_comments"),
    ]

    operations = [
        migrations.AddField(
            model_name="reportperson",
            name="signature_order",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.RunPython(apply_comment, drop_comment),
    ]
