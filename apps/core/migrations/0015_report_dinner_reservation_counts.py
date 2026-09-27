# 用餐预约人数字段（第十二届新增）。
# report.dinner_reservation_counts：与 dinner_reservation 并行、按下标对齐 6 个
# 官方用餐时段（apps/api/registration_form.MEALS）的就餐人数数组，元素 null/
# 非负整数。通知附件2 备注：如果需在成都理工大学食堂购票用餐，请备注时间并在
# 对应位置写上就餐人数。counts[i]>0 = 第 i 时段订 N 人；0/NULL = 该时段不订
# 或没填，渲染层在 _meal_cells 与字符串勾选合并（人数优先）。不参与配额/校验。
# 编号 0015：0013 双叶子与 0014_merge（汇合迁移）已占用。
# 非 PostgreSQL 后端（如测试用的 sqlite）不支持 COMMENT ON，跳过 —— 同 0010/0012。
import apps.core.models
from django.db import migrations


def apply_comment(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    comment = ("用餐预约人数：与dinner_reservation并行、按下标对齐6个用餐时段"
               "的人数数组，空=未记录，渲染时与勾选合并且人数优先")
    quote = connection.ops.quote_name
    literal = "'" + comment.replace("'", "''") + "'"
    with connection.cursor() as cursor:
        cursor.execute("COMMENT ON COLUMN %s.%s IS %s" % (
            quote("report"), quote("dinner_reservation_counts"), literal))


def drop_comment(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute("COMMENT ON COLUMN %s.%s IS NULL" % (
            quote("report"), quote("dinner_reservation_counts")))


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_merge_0013_card_tail_0013_merge_twice"),
    ]

    operations = [
        migrations.AddField(
            model_name="report",
            name="dinner_reservation_counts",
            field=apps.core.models.LegacyJSONField(blank=True, default=list, null=True),
        ),
        migrations.RunPython(apply_comment, drop_comment),
    ]
