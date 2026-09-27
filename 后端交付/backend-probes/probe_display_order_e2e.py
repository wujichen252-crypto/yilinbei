# -*- coding: utf-8 -*-
"""探针 A：display_order 前后端契约端到端（真后端代码 + 内存库，不碰 db.sqlite3）

用法：**在 yilinbei 仓库根目录下**执行（不需要配任何环境变量）
    .venv/Scripts/python.exe backend-probes/probe_display_order_e2e.py

仓库不在当前目录时，用 YLB_REPO 指定：
    set YLB_REPO=D:\\path\\to\\yilinbei        # Git Bash: export YLB_REPO=/d/path/to/yilinbei

Git Bash 上如果中文/emoji 报 UnicodeEncodeError，前面加 PYTHONIOENCODING=utf-8。

它验证的是本方案「必做 1」那 6 处改动是否真的打通了：
  ① 用内存 sqlite，绝不连 db.sqlite3
  ② migrate 后 report_person 真的有 display_order 列
  ③ 草稿路径：0 / 1 / 缺键 三种输入都能正确往返；非指挥行恒为 None
  ④ 提交路径：落库值正确，且【提交后重建 payload】仍能读回来（最易漏的一处）
  ⑤ 老数据（NULL）不报错
  ⑥ 非法值 -1 / '1' / True / 1.5 全部被拒
  ⑦ 白名单：多一个未知键会被拒
"""
import os
import sys

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

from django.conf import settings  # noqa: E402

DB = settings.DATABASES["default"]
print("① 隔离库 : ENGINE=%s  NAME=%r" % (DB["ENGINE"], DB["NAME"]))
assert DB["ENGINE"].endswith("sqlite3"), "没切到 sqlite"
assert DB["NAME"] == ":memory:", "不是内存库，中止"
print("   ✅ 确认没有指向 db.sqlite3")

from django.core.management import call_command  # noqa: E402
from django.db import connection  # noqa: E402

call_command("migrate", verbosity=0)
print("② migrate 通过（应含 0012_report_person_display_order）")

cols = [c.name for c in connection.introspection.get_table_description(
    connection.cursor(), "report_person")]
print("   report_person 列 :", ", ".join(cols))
assert "display_order" in cols, "列不存在 —— 本方案的必做 1 还没做完"
print("   ✅ report_person.display_order 真的建出来了")

from apps.core.models import User, ReportPerson  # noqa: E402
from apps.core.report_drafts import (  # noqa: E402
    normalize_draft_payload, parse_submission_payload, create_or_get_draft,
    draft_data, build_payload_from_report, create_report_from_submission, DraftError)

user = User.objects.create_user("probe_user", "pw", type=0, nickname="探针学校")
SCOPE = 0  # 学校端

ABSENT = object()


def person(name, card, type_, pos, sig, **extra):
    # 逐字段对齐前端 personToPayload 的**实际类型**：
    #   gender → nullStr()  字符串（'男'/'女'），后端 _string() 只收字符串
    #   age    → intOrNull() 整数
    #   sig    → signatureOrderOrNull()：'' → null，不是空字符串
    d = {"id": None, "name": name, "card": card, "age": 35, "gender": "男",
         "school": "示例学校", "phone": "13900000001",
         "type": type_, "position": pos, "signature_order": sig or None}
    d.update(extra)
    return d


def make_payload(marker=ABSENT):
    """marker=ABSENT → 完全不带 display_order 键；否则带上该值"""
    cond = person("张指挥", "510100199001010001", 1, 2, 1)
    if marker is not ABSENT:
        cond["display_order"] = marker          # ← 前端 buildDraftPayload 就是往这写
    return {
        "choir_name": "示例乐团", "name": "示例曲目", "group": "小学组",
        "establishment": "小学", "contact_name": "李老师",
        "contact_phone": "13900000009", "time_length": 300,
        "file": None, "spectrum": None,
        # 顺序与前端一致：教师行在前，然后是指挥、学生
        "person": [person("王指导", "510100199001010002", 1, 4, 2),
                   cond,
                   person("赵同学", "510100200501010003", 0, 0, "")],
    }


def pick(payload_person):
    return [p for p in payload_person if p.get("type") == 1 and p.get("position") == 2]


fail = []


def check(label, got, want):
    ok = got == want
    print("   %s %s  实得=%r 期望=%r" % ("✅" if ok else "❌", label, got, want))
    if not ok:
        fail.append(label)


print("\n③ 草稿路径（新增页暂存 → 回显）")
for marker, want in ((0, 0), (1, 1), (ABSENT, None)):
    d = create_or_get_draft(user, SCOPE, make_payload(marker))
    echo = draft_data(d)["payload"]
    check("存入 marker=%-6s → 回显 display_order" % (marker if marker is not ABSENT else "缺键"),
          pick(echo["person"])[0].get("display_order"), want)
    # 【注意】后端 _person() 是**每个键都产出**的（result["display_order"] = ...），
    # 所以回显 payload 里每一行都**有**这个键，缺席时值是 None —— 这是后端归一化，
    # 不是前端发的。这里断言的是**值**，不是键存在与否。
    others = [p for p in echo["person"] if not (p.get("type") == 1 and p.get("position") == 2)]
    check("   非指挥行的 display_order 值", [p.get("display_order") for p in others], [None, None])

print("\n④ 提交路径（正式报名落库 → 编辑页重建 payload）")
sub = parse_submission_payload(make_payload(0), user.id)
print("   parse_submission_payload 后，指挥行的 display_order =",
      pick(list(sub.people))[0].get("display_order"))
report = create_report_from_submission(user, sub, scope=SCOPE)
rows = list(ReportPerson.objects.filter(report_id=report.id).order_by("id"))
print("   report_person 落库值 :",
      [(r.type, r.position, r.signature_order, r.display_order) for r in rows])
check("指挥行入库 display_order", [r.display_order for r in rows if r.type == 1 and r.position == 2], [0])
check("其它行入库 display_order", sorted(
    (r.display_order for r in rows if not (r.type == 1 and r.position == 2)),
    key=lambda x: (x is not None, x)), [None, None])

rebuilt = build_payload_from_report(report)
check("重建 payload 里指挥行的 display_order（最易漏的一处）",
      pick(rebuilt["person"])[0].get("display_order"), 0)
check("重建 payload 的 person 行数", len(rebuilt["person"]), 3)
print("   重建后键集合 :", sorted(rebuilt["person"][0].keys()))
FRONTEND_KEYS = {"id", "name", "card", "age", "gender", "school", "phone", "instrument",
                 "head", "major", "other", "remark", "type", "position",
                 "signature_order", "display_order"}
check("重建 payload 的键集合 == 前端发出的 16 个键",
      set(rebuilt["person"][0].keys()), FRONTEND_KEYS)

print("\n⑤ 老数据（display_order 为 NULL）→ 不报错、按默认走")
ReportPerson.objects.filter(report_id=report.id).update(display_order=None)
rebuilt2 = build_payload_from_report(report)
check("老数据重建后指挥行 display_order", pick(rebuilt2["person"])[0].get("display_order"), None)

print("\n⑥ 非法值（前端理论上不会发，后端必须兜住）")
for label, marker, keyword in (("-1（负数）", -1, "不能小于 0"),
                               ("'1'（字符串）", "1", "必须是整数"),
                               ("True（布尔）", True, "必须是整数"),
                               ("1.5（小数）", 1.5, "必须是整数")):
    try:
        normalize_draft_payload(make_payload(marker))
        print("   ❌ %s 被放行了" % label)
        fail.append(label)
    except DraftError as e:
        ok = keyword in str(e)
        print("   %s %s → 被拒：%s" % ("✅" if ok else "❌", label, e))
        if not ok:
            fail.append(label)

print("\n⑦ 白名单：person 里多一个未知键")
bad = make_payload(0)
bad["person"][0]["foo"] = 1
try:
    normalize_draft_payload(bad)
    print("   ❌ 未知键被放行")
    fail.append("白名单")
except DraftError as e:
    print("   ✅ 被拒：%s %s" % (e, getattr(e, "extra", "")))

print("\n====== 结论：%s ======" % ("❌ 有断言失败 %s" % fail if fail else "✅ 全部通过"))
sys.exit(1 if fail else 0)
