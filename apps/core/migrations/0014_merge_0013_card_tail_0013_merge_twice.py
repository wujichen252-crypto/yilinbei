# 合并迁移：master 两侧各自长出的 0013 叶子在此汇合，保证迁移图单叶子。
#   - 0013_card_tail_identity（身份证后六位：历史归一化 + 列注释）
#   - 0013_merge_0011_twice_0012_display_order（报名次数限制一侧的分支汇合）
# 两侧互不引用、无操作需要执行；仅声明依赖关系。
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_card_tail_identity"),
        ("core", "0013_merge_0011_twice_0012_display_order"),
    ]

    operations = [
    ]
