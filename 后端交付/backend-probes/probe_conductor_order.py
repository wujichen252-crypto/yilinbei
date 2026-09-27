# -*- coding: utf-8 -*-
"""探针 B：adviser_instructors 指挥落位（纯函数，不碰数据库）

用法：**在 yilinbei 仓库根目录下**执行（不需要配任何环境变量）
    .venv/Scripts/python.exe backend-probes/probe_conductor_order.py

仓库不在当前目录时，用 YLB_REPO 指定：
    set YLB_REPO=D:\\path\\to\\yilinbei        # Git Bash: export YLB_REPO=/d/path/to/yilinbei

Git Bash 上如果中文/emoji 报 UnicodeEncodeError，前面加 PYTHONIOENCODING=utf-8。

它验证的是本方案「必做 2」：教师指挥按自己的署名号落位，且**历史数据行为逐字不变**。
覆盖了方案 6.2 边界表里的每一行。
"""
import os
import sys
from types import SimpleNamespace as NS

# 默认就用当前目录 —— 在仓库根目录下跑时无需任何配置
REPO = os.environ.get("YLB_REPO") or os.getcwd()
if not os.path.isfile(os.path.join(REPO, "manage.py")):
    sys.exit("❌ 在 %s 下找不到 manage.py。\n"
             "   请在 yilinbei 仓库根目录下运行本探针，"
             "或用 YLB_REPO=<仓库绝对路径> 指定。" % REPO)
print("仓库 :", REPO)
sys.path.insert(0, REPO)

os.environ["DJANGO_SETTINGS_MODULE"] = "django_config.settings"
os.environ["SECRET_KEY"] = "probe-only-not-a-real-key"
os.environ["DB_ENGINE"] = "django.db.backends.sqlite3"
os.environ["DB_NAME"] = ":memory:"

import django  # noqa: E402

django.setup()

from apps.api.export_services import adviser_instructors  # noqa: E402

fail = []


def check(label, got, want):
    ok = got == want
    print("   %s %s  实得=%r 期望=%r" % ("✅" if ok else "❌", label, got, want))
    if not ok:
        fail.append(label)


def P(pid, name):
    return NS(id=pid, name=name, phone="139")


def names(result):
    return [p.name for p in result]


cond = P(1, "指挥")
tA, tB = P(2, "老师A"), P(3, "老师B")

print("\n【指挥是教师，有 2 名其他指导老师】")
check("order=None（历史数据）→ 指挥占第一槽（硬约束：与改动前一致）",
      names(adviser_instructors([cond], [tA, tB], 1, None)), ["指挥", "老师A", "老师B"])
check("order=1 → 指挥仍在第 1 槽",
      names(adviser_instructors([cond], [tA, tB], 1, 1)), ["指挥", "老师A", "老师B"])
check("order=2 → 指挥落到第 2 槽（本方案的目标行为）",
      names(adviser_instructors([cond], [tA, tB], 1, 2)), ["老师A", "指挥", "老师B"])
check("order=3（非法值）→ 旧口径",
      names(adviser_instructors([cond], [tA, tB], 1, 3)), ["指挥", "老师A", "老师B"])
check("order=0（非法值）→ 旧口径",
      names(adviser_instructors([cond], [tA, tB], 1, 0)), ["指挥", "老师A", "老师B"])
check("order='2'（字符串）→ 旧口径（只用 `in (1,2)` 判断，不做类型转换）",
      names(adviser_instructors([cond], [tA, tB], 1, "2")), ["指挥", "老师A", "老师B"])

print("\n【只有指挥 1 名指导老师（rest 为空）】")
check("order=None", names(adviser_instructors([cond], [], 1, None)), ["指挥"])
check("order=2 → 仍只有指挥（挪不挪都一样）",
      names(adviser_instructors([cond], [], 1, 2)), ["指挥"])

print("\n【填 2 但只有 1 名其他老师 → min 兜底，不越界】")
check("order=2, 1 名其他老师", names(adviser_instructors([cond], [tA], 1, 2)), ["老师A", "指挥"])

print("\n【指挥非教师（type≠1）→ 完全不受影响】")
check("type=0, order=2", names(adviser_instructors([cond], [tA, tB], 0, 2)), ["老师A", "老师B"])
check("type=None（人物缺失）", names(adviser_instructors([cond], [tA, tB], None, 2)),
      ["老师A", "老师B"])

print("\n【无指挥 / 多指挥 → 旧口径】")
check("无指挥", names(adviser_instructors([], [tA, tB], 1, 2)), ["老师A", "老师B"])
check("多指挥", names(adviser_instructors([cond, cond], [tA, tB], 1, 2)), ["老师A", "老师B"])

print("\n【指挥被重复提交为指导老师（同 person_id）：先去重，再按号落位】")
check("混入指挥本人 + order=2", names(adviser_instructors([cond], [cond, tA], 1, 2)),
      ["老师A", "指挥"])
check("混入指挥本人 + order=None", names(adviser_instructors([cond], [cond, tA], 1, None)),
      ["指挥", "老师A"])

print("\n【回归：全空】")
check("全空", names(adviser_instructors([], [], None, None)), [])

print("\n====== 结论：%s ======" % ("❌ 失败 %s" % fail if fail else "✅ 全部通过"))
sys.exit(1 if fail else 0)
