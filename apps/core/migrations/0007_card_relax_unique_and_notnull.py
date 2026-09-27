# 身份证改造 · 第 1 步（共 2 步）：**只放松约束，不动列宽**。
#
# 为什么必须拆成两步（不要合并、不要调换顺序）：
#   person.card 现在既有 UNIQUE、又是 varchar(255)，而目标是不唯一 + varchar(6)。
#   如果一步到位，PostgreSQL/GaussDB 执行 `ALTER COLUMN card TYPE varchar(6)` 时，
#   只要表里还有一行 18 位身份证，就会直接报
#       value too long for type character varying(6)
#   整个迁移失败回滚。而**数据转换不能写在 migration 里**（见下），
#   所以列宽必须等到数据转换（management command `normalize_person_cards`）跑完后，
#   由 0008 单独收紧。
#
# 本迁移只做两件事，都可在有 18 位数据的表上安全执行：
#   1. 摘掉 person.card 的 UNIQUE —— 后 6 位必然碰撞，唯一约束在业务上已经不成立；
#      留着的直接后果是「不同的人撞号时第二条报名写不进去」，而不是数据变脏。
#   2. 摘掉 crew/leader/ticket_subscribe.card 的 NOT NULL 与 default=" "。
#      原来的 default=" " 是为了绕开 GaussDB「NOT NULL 列写空串被当成 NULL」的变通，
#      它留下的 " " 占位符既满足不了「必须是 6 位」的不变量，又会在导出里出现一个
#      看起来像数据的空格。改成允许 NULL 后，缺值就是 NULL，语义干净。
#      列宽仍保持 255，等 0008 收紧。
#
# 【本迁移不含任何数据删除/改写】
#   没有 RunPython，没有 delete()，没有 update()。migration 只改 schema。
#   业务数据是否清空是**独立的人工决策**，不属于迁移的一部分。
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_reportdraft_unique_editing"),
    ]

    operations = [
        # 1) person.card：去 UNIQUE、去 default=" "，列宽先不动
        migrations.AlterField(
            model_name="person",
            name="card",
            field=models.CharField(max_length=255),
        ),
        # 2) crew.card / leader.card / ticket_subscribe.card：去 NOT NULL、去 default
        migrations.AlterField(
            model_name="crew",
            name="card",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AlterField(
            model_name="leader",
            name="card",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AlterField(
            model_name="ticketsubscribe",
            name="card",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
