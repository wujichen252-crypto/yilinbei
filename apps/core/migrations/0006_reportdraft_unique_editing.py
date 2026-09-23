# 为 report_draft 表补两条部分唯一约束，杜绝并发下产生两条"编辑中"草稿。
# 迁移前先合并历史重复：同一 (user, scope, report_id) 只保留 updated_at 最新的一条。
from django.db import migrations, models


def dedupe_editing_drafts(apps, schema_editor):
    ReportDraft = apps.get_model("core", "ReportDraft")
    seen = set()
    stale_ids = []
    qs = ReportDraft.objects.filter(state=0).order_by("-updated_at", "-id")
    for draft in qs:
        key = (draft.user_id, draft.scope, draft.report_id)
        if key in seen:
            stale_ids.append(draft.id)
        else:
            seen.add(key)
    if stale_ids:
        ReportDraft.objects.filter(id__in=stale_ids).delete()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_auto_20260923_0013"),
    ]

    operations = [
        migrations.RunPython(dedupe_editing_drafts, noop),
        migrations.AddConstraint(
            model_name="reportdraft",
            constraint=models.UniqueConstraint(
                fields=["user_id", "scope"],
                condition=models.Q(state=0, report_id__isnull=True),
                name="draft_one_new_editing",
            ),
        ),
        migrations.AddConstraint(
            model_name="reportdraft",
            constraint=models.UniqueConstraint(
                fields=["user_id", "scope", "report_id"],
                condition=models.Q(state=0),
                name="draft_one_edit_editing",
            ),
        ),
    ]
