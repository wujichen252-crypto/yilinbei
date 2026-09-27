# 合并 0011_user_can_report_twice 与 0011_report_person_signature_order → 0012
# 两个分支（分别来自本地提交与远端合并 5d3fabc），无表结构变更。
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_user_can_report_twice"),
        ("core", "0012_report_person_display_order"),
    ]

    operations = []
