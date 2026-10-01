"""从 Excel 批量导入账号。仅后台管理命令，不暴露任何 HTTP 接口。

===========================================================================
用法
===========================================================================
    # 1. 生成导入模板（第一行即填写提示，C1 说明账号类型怎么填）
    python manage.py import_accounts --template
    python manage.py import_accounts --template path/to/模板.xlsx

    # 2. 按模板填好后导入（第一行是提示行，数据从第二行开始）
    python manage.py import_accounts 账号导入模板.xlsx

    可选参数：
    --password scylb@2026   默认初始密码；Excel「初始密码」列留空的行用它，
                            列里填了值的行按行内密码建号（市州 xbyyz@2026 这类
                            红头文件口径就写在列里，一张表可以混多种密码）
    --parent city01         整表默认的所属市州（写 users.parent_id）。也可以不用
                            参数，在 Excel「所属市州」列里逐行填市州账号；
                            同一行两处都填时以列为准。「所属市州」列还可以填
                            同一张表里将要导入的市州账号（命令会先建市州再建学校）

===========================================================================
模板与解析规则
===========================================================================
· 第 1 行固定为提示行（不当数据）：A1=账号、B1=账号名称、C1=所属市州、
  D1=账号类型（0=高校，1=市州，2=组委会，3=管理员，5=中小学）、
  E1=合并办学特许的填法、F1=初始密码的填法
· 数据从第 2 行起，六列依次：登录账号（唯一）/ 账号名称 / 所属市州 /
  账号类型（数字）/ 合并办学特许（填 1 = 可同时报小学组和中学组；不可以就
  留空）/ 初始密码（留空用 --password 默认值）
· 所属市州填市州的登录账号（如 city01），中小学账号一般要填（市州端才能
  看到下级学校）；市州账号自己不用填，填了该行报错；可以引用同一张表里
  的市州行，命令会先建市州、再建挂靠它的学校
· 合并办学特许列留空或 0 视为否；填 1 只对中小学（type=5）生效，
  其他类型填 1 该行报错
· 单元格是数字时按整数字符串处理 —— Excel 常把 123456 存成浮点，
  直接 str() 会得到 "123456.0" 当成账号名
· 类型只收 0/1/2/3/5；4=省级是上一届西部音乐周的历史渠道，不开放批量建号
· 模板不放示例数据行 —— 示例行会被当成数据导进去，宁缺勿错

===========================================================================
导入语义（可重跑）
===========================================================================
· 逐行校验：任何一行有问题都不影响其他行，合法行照常入库，
  问题行逐条报告行号与原因
· 账号已存在（含被软删的，username 的唯一约束在库层面不分死活）→ 跳过，
  不算失败 —— 所以修完 Excel 重跑是安全的，已入库的账号会被跳过
· 文件内出现重复账号 → 第二次出现的行按失败处理（不覆盖先出现的）
· 有任何失败行时命令退出码非 0，但已入库的合法行**不回滚**：
  修正 Excel 后重跑即可
"""
import os
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import User

# 开放批量导入的账号类型。4=省级（TYPE_PROVINCE）是上一届历史渠道，刻意不列。
ALLOWED_TYPES = {
    User.TYPE_SCHOOL: "高校",
    User.TYPE_CITY: "市州",
    User.TYPE_COMMITTEE: "组委会",
    User.TYPE_ADMIN: "管理员",
    User.TYPE_PRIMARY_SECONDARY: "中小学",
}
TYPE_HINT = "账号类型（填数字）：0=高校，1=市州，2=组委会，3=管理员，5=中小学"
CITY_HINT = "所属市州（填市州登录账号，如 city01；中小学账号一般要填，市州账号自己不用填）"
TWICE_HINT = "合并办学特许（仅中小学=5 可填）：可同时报小学组和中学组填 1，不可以就留空"
DEFAULT_PASSWORD = "scylb@2026"
PW_HINT = "初始密码（留空用默认 %s；不同账号要不同密码就逐行填）" % DEFAULT_PASSWORD
USERNAME_MAX = 30     # users.username max_length
NICKNAME_MAX = 255    # users.nickname max_length


def cell_text(value):
    """Excel 单元格 → 干净字符串；数字单元格不产生 "123456.0" 这类尾巴。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_type(raw):
    """账号类型单元格 → (类型值, None) 或 (None, 错误说明)。"""
    text = cell_text(raw)
    if not text:
        return None, "账号类型不能为空"
    if not text.isdigit():
        return None, "账号类型必须是数字（%s）" % TYPE_HINT
    value = int(text)
    if value not in ALLOWED_TYPES:
        allowed = "、".join("%d=%s" % (k, v) for k, v in sorted(ALLOWED_TYPES.items()))
        return None, "账号类型 %d 不在可导入范围内（%s）" % (value, allowed)
    return value, None


def parse_can_twice(raw):
    """合并办学特许列（users.can_report_twice）→ (是/否, None) 或 (False, 错误说明)。

    口径（users 表字段注释同款）：填 1 = 合并办学的中小学，可同时报
    小学组、中学组各一支；不可以就留空，0 视同留空。其他值一律报错 ——
    宁可让经办人改表重跑，也不让歧义值默默变成"否"。
    """
    text = cell_text(raw)
    if not text or text == "0":
        return False, None
    if text == "1":
        return True, None
    return False, "合并办学特许只能填 1 或留空（0 视同留空）"


class Command(BaseCommand):
    help = "从 Excel 模板批量导入账号（第一行为提示行，数据从第二行起）。"

    def add_arguments(self, parser):
        parser.add_argument("excel", nargs="?", help="要导入的 Excel 文件路径")
        parser.add_argument("--template", nargs="?", const="账号导入模板.xlsx",
                            help="只生成导入模板不导数据；可带输出路径，默认 ./账号导入模板.xlsx")
        parser.add_argument("--password", default=DEFAULT_PASSWORD,
                            help="新账号初始密码，默认 %s" % DEFAULT_PASSWORD)
        parser.add_argument("--parent", default=None, metavar="市州账号名",
                            help="整表默认的所属市州账号 username；"
                                 "Excel「所属市州」列填了值的行以列为准")

    def handle(self, *args, **options):
        if options["template"]:
            self._write_template(options["template"])
            return
        path = options["excel"]
        if not path:
            raise CommandError("请给出要导入的 Excel 文件，或用 --template 先生成模板。"
                               "用法：python manage.py import_accounts 账号导入模板.xlsx")
        if not os.path.exists(path):
            raise CommandError("文件不存在：%s（用 --template 可生成模板）" % path)
        self._import(path, options)

    # ------------------------------------------------------------------
    def _write_template(self, path):
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "账号"
        sheet.append(["账号（登录用，唯一，不可与现有账号重复）",
                      "账号名称（学校/单位显示名）",
                      CITY_HINT,
                      TYPE_HINT,
                      TWICE_HINT,
                      PW_HINT])
        for cell in sheet[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        sheet.column_dimensions["A"].width = 30
        sheet.column_dimensions["B"].width = 34
        sheet.column_dimensions["C"].width = 32
        sheet.column_dimensions["D"].width = 48
        sheet.column_dimensions["E"].width = 44
        sheet.column_dimensions["F"].width = 30
        sheet.freeze_panes = "A2"
        workbook.save(path)
        self.stdout.write(self.style.SUCCESS("模板已生成：%s" % path))
        self.stdout.write("第 1 行为提示行，请从第 2 行开始填数据；%s" % TYPE_HINT)

    # ------------------------------------------------------------------
    def _import(self, path, options):
        from openpyxl import load_workbook

        try:
            workbook = load_workbook(path, read_only=True, data_only=True)
        except Exception as exc:
            raise CommandError("无法读取 Excel：%s" % exc)

        sheet = workbook.active
        rows = []      # [(行号, username, nickname, 所属市州账号, type, can_report_twice, 初始密码)]
        failures = []  # [(行号, 原因)]
        seen = {}      # 文件内 username -> 首次出现的行号
        for row_no, row in enumerate(sheet.iter_rows(min_row=2, max_col=6,
                                                     values_only=True), start=2):
            username, nickname, raw_city, raw_type, raw_twice, raw_pw = \
                (row + (None,) * 6)[:6]
            if not any(cell_text(v) for v in
                       (username, nickname, raw_city, raw_type, raw_twice, raw_pw)):
                continue    # 整行为空：静默跳过（常见于模板尾部）
            username, nickname = cell_text(username), cell_text(nickname)
            city = cell_text(raw_city)
            password = cell_text(raw_pw) or None
            user_type, type_error = parse_type(raw_type)
            can_twice, twice_error = parse_can_twice(raw_twice)
            error = None
            if not username:
                error = "账号不能为空"
            elif " " in username:
                error = "账号不能含空格：%r" % username
            elif len(username) > USERNAME_MAX:
                error = "账号超过 %d 个字符（users.username 列宽）" % USERNAME_MAX
            elif not nickname:
                error = "账号名称不能为空"
            elif len(nickname) > NICKNAME_MAX:
                error = "账号名称超过 %d 个字符" % NICKNAME_MAX
            elif type_error:
                error = type_error
            elif twice_error:
                error = twice_error
            elif can_twice and user_type != User.TYPE_PRIMARY_SECONDARY:
                error = "只有中小学账号（5）可以填 1（合并办学特许）"
            elif user_type == User.TYPE_CITY and city:
                error = "市州账号（1）不需要填所属市州"
            elif username in seen:
                error = "与第 %d 行的账号重复" % seen[username]
            if error:
                failures.append((row_no, error))
                continue
            seen[username] = row_no
            rows.append((row_no, username, nickname, city, user_type, can_twice, password))
        workbook.close()

        if not rows and not failures:
            raise CommandError("表格没有可导入的数据行（数据应从第 2 行开始）。")

        parent = None
        if options["parent"]:
            parent = User.all_objects.filter(username=options["parent"]).first()
            if parent is None:
                raise CommandError("--parent 指定的账号不存在：%s" % options["parent"])

        # 解析「所属市州」列：填了的必须是市州（type=1）账号——库里已存在的，
        # 或同一张表里将要导入的市州行（建号时会先建市州再建学校）；否则该行失败
        city_names = {row[3] for row in rows if row[3]}
        city_users = {user.username: user for user in
                      (User.all_objects.filter(username__in=city_names)
                       if city_names else [])}
        file_city_names = {row[1] for row in rows if row[4] == User.TYPE_CITY}
        file_other_names = {row[1] for row in rows} - file_city_names
        checked = []
        for row_no, username, nickname, city, user_type, can_twice, password in rows:
            if city:
                city_user = city_users.get(city)
                if city_user is not None:
                    if city_user.type != User.TYPE_CITY:
                        failures.append((row_no, "所属市州账号不是市州（1）：%s" % city))
                        continue
                elif city in file_city_names:
                    pass    # 引用同表市州行：合法，建号阶段先建市州
                elif city in file_other_names:
                    failures.append((row_no, "所属市州账号不是市州（1）：%s" % city))
                    continue
                else:
                    failures.append((row_no, "所属市州账号不存在：%s" % city))
                    continue
            checked.append((row_no, username, nickname, city, user_type, can_twice, password))
        rows = checked

        # 库里已存在的（含软删）提前查出，跳过不算失败
        existing = set(User.all_objects.filter(
            username__in=[r[1] for r in rows]).values_list("username", flat=True))

        created, skipped, privileged, orphan = [], [], [], []
        created_passwords = []   # 每个新账号实际用的初始密码（结果汇报用）
        parent_links = {}        # 市州账号名 -> [新账号名]（结果汇报用）
        created_users = {}       # 本次运行建出的账号（含表内市州行），供同表挂靠引用

        # 表内挂靠引用要求市州先存在：市州行排最前先建，其余按原行序
        ordered = ([row for row in rows if row[4] == User.TYPE_CITY] +
                   [row for row in rows if row[4] != User.TYPE_CITY])

        with transaction.atomic():
            for _, username, nickname, city, user_type, can_twice, password in ordered:
                if username in existing:
                    skipped.append(username)
                    continue
                if city:
                    # 库里已有的市州优先；同表市州行用本次先建出的那个
                    city_user = city_users.get(city) or created_users.get(city)
                else:
                    city_user = parent
                user = User.objects.create_user(
                    username, password or options["password"],
                    nickname=nickname, type=user_type,
                    can_report_twice=can_twice,
                    parent_id=city_user.id if city_user else None)
                created_users[username] = user
                created.append(username)
                created_passwords.append(password or options["password"])
                if can_twice:
                    privileged.append(username)
                if city_user is not None:
                    parent_links.setdefault(city_user.username, []).append(username)
                elif user_type == User.TYPE_PRIMARY_SECONDARY:
                    orphan.append(username)

        self.stdout.write("===== 导入结果 =====")
        self.stdout.write("  新建 %d 个：%s" % (len(created), "、".join(created) or "（无）"))
        self.stdout.write("  跳过（已存在）%d 个：%s" % (len(skipped), "、".join(skipped) or "（无）"))
        if privileged:
            self.stdout.write("  合并办学特许（可报小学组+中学组）%d 个：%s"
                              % (len(privileged), "、".join(privileged)))
        if parent_links:
            self.stdout.write("  挂靠市州 %d 个：" % len(parent_links))
            for city_name, children in parent_links.items():
                self.stdout.write("    %s：%s" % (city_name, "、".join(children)))
        if orphan:
            self.stdout.write(self.style.WARNING(
                "  注意：%d 个中小学账号未挂靠市州（%s），市州端将看不到它们"
                % (len(orphan), "、".join(orphan))))
        if not created_passwords:
            self.stdout.write("  初始密码：%s（没有新建账号）" % options["password"])
        elif len(set(created_passwords)) == 1:
            self.stdout.write("  初始密码：%s" % created_passwords[0])
        else:
            self.stdout.write("  初始密码分布：%s" % "、".join(
                "%s ×%d 个" % (pw, n) for pw, n in sorted(Counter(created_passwords).items())))
        if failures:
            self.stdout.write(self.style.ERROR("  失败 %d 行：" % len(failures)))
            for row_no, error in failures:
                self.stdout.write("    第 %d 行：%s" % (row_no, error))

        if failures:
            raise CommandError("有 %d 行未入库（明细见上）。修正 %s 后重跑，"
                               "已入库的账号会自动跳过。" % (len(failures), path))
        if not created:
            self.stdout.write(self.style.WARNING("没有新建任何账号（全部已存在）。"))
