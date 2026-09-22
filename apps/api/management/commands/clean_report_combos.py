"""清洗报名表的无效「乐团类型 × 组别」组合。

前端打补丁后铜管乐团的组别下拉只剩 {小学组, 中学组}（赛程里大学组只有
管乐团展演，见 seed_tickets）。补丁前 city 编辑页允许存下「铜管乐团 +
大学组」这类记录，现在用户编辑它们会卡在前端提交校验。此命令默认只
审计（dry-run），列出命中的记录供组委会决定处理方式；实际清洗需要显
式传 --soft-delete 或 --set-group，两者互斥。
"""
from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Report, ReportPerson

# 前端补丁后铜管乐团下拉里可选的组别；不在此列的铜管乐团记录都无法再编辑
BRASS_GROUPS = ("小学组", "中学组")


def dirty_reports(ids=None):
    """所有 establishment=铜管乐团 且组别不在下拉里的报名（含已软删）。"""
    qs = Report.all_objects.filter(establishment="铜管乐团").exclude(group__in=BRASS_GROUPS)
    if ids:
        qs = qs.filter(id__in=ids)
    return qs.order_by("id")


def person_counts(report_ids):
    if not report_ids:
        return {}
    rows = (ReportPerson.objects.filter(report_id__in=report_ids)
            .values_list("report_id", flat=True))
    counts = {}
    for report_id in rows:
        counts[report_id] = counts.get(report_id, 0) + 1
    return counts


class Command(BaseCommand):
    help = ("Audit and clean reports with establishment=铜管乐团 and a group "
            "outside the patched dropdown (小学组/中学组). Audit-only by default.")

    def add_arguments(self, parser):
        parser.add_argument("--ids", default="", help="逗号分隔的 report id，只处理这些记录")
        parser.add_argument("--soft-delete", action="store_true",
                            help="把命中的未删除记录软删除（写入 deleted_at）")
        parser.add_argument("--set-group", choices=BRASS_GROUPS, default=None,
                            help="把命中记录的 group 改写为指定组别")

    def handle(self, *args, **options):
        if options["soft_delete"] and options["set_group"]:
            raise CommandError("--soft-delete 与 --set-group 互斥，请二选一")
        ids = [int(x) for x in options["ids"].split(",") if x.strip()]

        reports = list(dirty_reports(ids))
        if not reports:
            self.stdout.write("未发现脏数据：没有组别不在下拉列表里的铜管乐团报名。")
            return

        counts = person_counts([r.id for r in reports])
        self.stdout.write(f"命中 {len(reports)} 条「铜管乐团 + 组别不在 {'/'.join(BRASS_GROUPS)} 内」的报名：")
        for report in reports:
            state = "已删除" if report.deleted_at else "未删除"
            self.stdout.write(
                f"  id={report.id}  {report.choir_name}  group={report.group}  "
                f"user_id={report.user_id}  status={report.status}  "
                f"人员={counts.get(report.id, 0)}  {state}  updated_at={report.updated_at:%Y-%m-%d %H:%M}"
            )

        if not (options["soft_delete"] or options["set_group"]):
            self.stdout.write(self.style.WARNING(
                "当前为审计模式（dry-run）。确认处理方式后加 --soft-delete 或 --set-group=<组别> 执行清洗。"
            ))
            return

        live = [r for r in reports if r.deleted_at is None]
        if options["soft_delete"]:
            for report in live:
                report.delete()
            self.stdout.write(self.style.SUCCESS(f"已软删除 {len(live)} 条记录（其余为已删除，跳过）。"))
        else:
            for report in live:
                report.group = options["set_group"]
                report.save(update_fields=["group", "updated_at"])
            self.stdout.write(self.style.SUCCESS(f"已把 {len(live)} 条记录的组别改为 {options['set_group']}。"))
