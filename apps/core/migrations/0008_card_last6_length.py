# 身份证改造 · 第 2 步（共 2 步）：收紧列宽到 6 位，并挂上格式校验器。
#
# 【本迁移有前置条件，执行顺序不许颠倒】
#     0007（放松约束） → `manage.py normalize_person_cards`（数据转换） → 0008（本迁移）
#
# 原因：`ALTER COLUMN TYPE varchar(6)` 在表里还有 18 位/7 位/带空格的值时**必然失败**
# （value too long）。0007 只是把「唯一」和「非空」这两条约束摘掉，列宽仍是 255，
# 因此 0007 之后、数据转换之前，系统处于「列宽 255、值已是后 6 位」的中间态 ——
# 这是设计好的过渡态，不对外服务，只用于跑数据转换。
#
# 【数据转换为什么不在本文件里】
#   把 18 位截成后 6 位是**不可逆**的（截完就再也还原不回完整身份证），
#   而且需要「报告 + 人工确认 + 可 dry-run 重跑」这些 migration 给不了的能力。
#   所以它落在 apps/core/management/commands/normalize_person_cards.py，
#   由运维显式执行。migration 只负责 schema，不碰业务数据。
#
# 【校验器的作用范围，不要误解】
#   CharField 的 validators 只在 full_clean() 时触发，`objects.create()` / `.save()`
#   都不会调用它 —— 也就是说**它拦不住写入**。真正拦写入的是两道：
#     服务端 apps/core/models.py 的 normalize_card()（store_people / views 里显式调用）；
#     DB 层的 varchar(6) 列宽。
#   这里挂 validators 是为了让 `full_clean()`（后台 admin、表单、shell 手工操作）
#   与那两道口径一致，不是为了「靠它守住」。
from django.core.validators import RegexValidator
from django.db import migrations, models

CARD_PATTERN = r"^[0-9]{5}[0-9Xx]$"
CARD_ERROR = "身份证后6位应为6位，前5位为数字，末位为数字或X"


def _card_field(**kwargs):
    return models.CharField(max_length=6, validators=[RegexValidator(CARD_PATTERN, CARD_ERROR)], **kwargs)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_card_relax_unique_and_notnull"),
    ]

    operations = [
        # person.card —— 全系统唯一一处曾经是 UNIQUE 的 card，0007 已摘掉。
        # null=False 保持不变（必须显式给值），且**没有 default**：默认值会掩盖漏填。
        migrations.AlterField(
            model_name="person",
            name="card",
            field=_card_field(),
        ),
        # 其余四处允许为空（业务上人可以不填身份证），列宽同样收到 6
        migrations.AlterField(
            model_name="crew",
            name="card",
            field=_card_field(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="leader",
            name="card",
            field=_card_field(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="ticketsubscribe",
            name="card",
            field=_card_field(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="student",
            name="card",
            field=_card_field(blank=True, null=True),
        ),
    ]
