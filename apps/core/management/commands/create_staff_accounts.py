"""创建组委会/管理员后台账号：默认各 3 个。仅后台管理命令，无 HTTP 接口。

===========================================================================
用法
===========================================================================
    python manage.py create_staff_accounts                # 组委会×3 + 管理员×3
    python manage.py create_staff_accounts --type 2       # 只建 3 个组委会
    python manage.py create_staff_accounts --type 3 --count 5
    python manage.py create_staff_accounts --password '别的初始密码'

===========================================================================
命名与语义
===========================================================================
· 用户名：committeeNN / adminNN，NN 从 01 递增；已被任何账号（含软删，
  username 的唯一约束不分死活）占用的号码自动跳过 —— 所以与现有的
  committee01/admin01 不冲突，**重跑会继续往后建新号**（再跑一次得到
  04-06 一批），不是报错也不是覆盖
· 昵称：组委会NN / 管理员NN，与用户名同号
· 初始密码默认 scylb@2026（09-27 起的建号口径，--password 可改）
· 一次命令里的全部建号在同一个事务里，任何一步失败整体回滚
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import User

# 后台管理角色 → (用户名前缀, 昵称前缀)。本命令只做后台账号，
# 学校类账号（0/5）请走 import_accounts 的 Excel 批量导入。
STAFF_PREFIXES = {
    User.TYPE_COMMITTEE: ("committee", "组委会"),
    User.TYPE_ADMIN: ("admin", "管理员"),
}
DEFAULT_PASSWORD = "scylb@2026"
DEFAULT_COUNT = 3


def taken_numbers(username_prefix):
    """库里已被占用的号码（含软删账号）：committee01 -> 1。"""
    numbers = set()
    for username in User.all_objects.filter(
            username__startswith=username_prefix).values_list("username", flat=True):
        tail = username[len(username_prefix):]
        if tail.isdigit():
            numbers.add(int(tail))
    return numbers


def next_usernames(username_prefix, count):
    """从 01 起找 count 个未被占用的号码，返回 [(用户名, 序号), ...]。"""
    taken = taken_numbers(username_prefix)
    result, number = [], 1
    while len(result) < count:
        if number not in taken:
            result.append(("%s%02d" % (username_prefix, number), number))
        number += 1
    return result


class Command(BaseCommand):
    help = "创建组委会/管理员后台账号（默认各 3 个，自动跳过已占用的号码）。"

    def add_arguments(self, parser):
        parser.add_argument("--type", type=int, default=None, choices=sorted(STAFF_PREFIXES),
                            help="只建某一类：2=组委会，3=管理员；不传则各建 --count 个")
        parser.add_argument("--count", type=int, default=DEFAULT_COUNT,
                            help="每类建几个，默认 %d" % DEFAULT_COUNT)
        parser.add_argument("--password", default=DEFAULT_PASSWORD,
                            help="初始密码，默认 %s" % DEFAULT_PASSWORD)

    def handle(self, *args, **options):
        if options["count"] < 1:
            raise CommandError("--count 至少为 1")
        targets = [options["type"]] if options["type"] else sorted(STAFF_PREFIXES)
        password = options["password"]

        plan = []
        for user_type in targets:
            prefix, label = STAFF_PREFIXES[user_type]
            plan.append((user_type, label, next_usernames(prefix, options["count"])))

        created = []
        with transaction.atomic():
            for user_type, _, usernames in plan:
                for username, number in usernames:
                    user = User.objects.create_user(
                        username, password,
                        nickname="%s%02d" % (STAFF_PREFIXES[user_type][1], number),
                        type=user_type)
                    created.append(user)

        self.stdout.write("===== 建号结果 =====")
        for user in created:
            self.stdout.write("  %s  %s  type=%d  初始密码=%s"
                              % (user.username, user.nickname, user.type, password))
        self.stdout.write(self.style.SUCCESS("共创建 %d 个账号。" % len(created)))
        if not options["type"]:
            self.stdout.write("（组委会、管理员各 %d 个；重跑本命令会继续往后建新号，"
                              "不会覆盖现有账号）" % options["count"])
