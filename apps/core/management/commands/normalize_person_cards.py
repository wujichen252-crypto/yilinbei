"""把历史身份证数据转换成「后 6 位」。**默认 dry-run，不写任何数据。**

===========================================================================
执行顺序（不许颠倒）
===========================================================================
    1. python manage.py migrate core 0007        # 摘掉 UNIQUE / NOT NULL，列宽仍 255
    2. python manage.py normalize_person_cards            # 本命令，先看报告
    3. python manage.py normalize_person_cards --apply    # 确认无误后落库
    4. python manage.py migrate                   # 0008 把列宽收到 6

第 4 步必须在第 3 步之后：表里还有 18 位数据时 `varchar(6)` 会直接迁移失败。

===========================================================================
本命令做什么
===========================================================================
对 person / crew / leader / ticket_subscribe / student 五张表的 card 列：
    18 位             → 取后 6 位
    恰好 6 位         → 原样保留（末位小写 x 归一大写 X）
    长度不是 6/18     → **不转换**，只报告，整批中止
    末 6 位含非法字符 → **不转换**，只报告，整批中止
    空值 / 空串 / 空格 → 只统计，**不改写**（是否置 NULL 由字段定义决定，不在这里做）

**绝不做「静默截断」。** `text[-6:]` 对任何长度都能取出一段看着合法的 6 位数字，
但那等于把一个人的身份证悄悄换成另一个人的后 6 位 —— 正是本次改造要根除的错配来源。
所以只认 6 位和 18 位两种长度，其余一律报错让人来看。

转换后与同字段其它行撞号时，默认也中止（除非显式传 --allow-collisions）。
但要注意：**撞号本身不是错误** —— 后 6 位只有 10^6 种取值，不同的人撞号是常态，
这正是本次取消 UNIQUE 的原因。这个开关只是让运维在写库前知道撞了多少、是哪些行。

===========================================================================
为什么转换不能写进 migration
===========================================================================
18 位 → 后 6 位**不可逆**。一旦执行，完整身份证就永久丢失。这种操作需要
「先看报告、再人工确认、可重跑」的能力，而 migration 是一次性自动执行的。
所以迁移只改 schema（0007 / 0008），数据转换落在本命令，由人显式按下。

本命令也**不删除任何行**：撞号（同一个后 6 位出现两次）在业务上是合法的
（后 6 位本来就会碰撞，这正是本次改造的前提），只提示、不合并、不删除。
是否清理历史业务数据是另一个独立的人工决策，不在本命令职责内。
"""
import re
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.models import (CARD_PATTERN, Crew, Leader, Person, Student,
                              TicketSubscribe)

# 参与转换的表。字段名统一是 card。
# 顺序固定，报告与写库都按这个顺序，便于与运维脚本对账。
CARD_MODELS = (
    ("person", Person),
    ("ticket_subscribe", TicketSubscribe),
    ("crew", Crew),
    ("leader", Leader),
    ("student", Student),
)

LEGACY_LENGTH = 18
TARGET_LENGTH = 6


def classify(raw):
    """把一个 card 原始值归类。返回 (类别, 转换后的值或 None)。

    类别取值：
        empty       —— NULL / 空串 / 纯空格，无需转换
        ok          —— 已经是合法的 6 位（末位大写或数字）
        convert     —— 合法且需要改写：18 位取后 6 位，或 6 位里的末位小写 x
        invalid     —— 长度或字符不合法，**不转换、只报告**

    【只认 6 位和 18 位两种长度】7 位、15 位旧号、带连字符的值一律 invalid。
    这一点是刻意的：`text[-6:]` 对任何长度都能取出一段看着合法的 6 位数字，
    悄悄截断就是「静默把一个人的身份证换成另一个人的后 6 位」，
    而这正是本次改造要根除的错配来源。宁可报错让人来看，也不猜。
    """
    if raw is None:
        return "empty", None
    if not isinstance(raw, str):
        raw = str(raw)
    text = raw.strip()
    if not text:
        return "empty", None
    if len(text) not in (TARGET_LENGTH, LEGACY_LENGTH):
        return "invalid", None
    tail = text[-TARGET_LENGTH:]
    # 【必须用 CARD_PATTERN，不能用 str.isdigit()】isdigit() 是 Unicode 语义的：
    # '１'.isdigit() 和 '٣'.isdigit() 都是 True。用它判的话，全角/阿拉伯-印度数字
    # 会被判成「已经是合法的 6 位」，于是既不被转换、也不被报错，原样留在库里；
    # 而接口侧的 normalize_card 用的是 [0-9]（只认 ASCII），从此永远拒绝这一行 ——
    # 这条记录谁都改不动了。判定标准必须和模型字段上的校验器完全同一个。
    if not re.match(CARD_PATTERN, tail):
        return "invalid", None
    normalized = tail[:5] + ("X" if tail[5] in "xX" else tail[5])
    return ("ok" if normalized == text else "convert"), normalized


def invalid_reason(raw):
    """给不合法的值一个准确的归类：长度问题还是字符问题。"""
    text = raw if isinstance(raw, str) else str(raw)
    stripped = text.strip()
    if len(stripped) not in (TARGET_LENGTH, LEGACY_LENGTH):
        return "bad_length", "长度为 %d（只接受 6 位或 18 位）：%r" % (len(stripped), text)
    return "bad_chars", "末 6 位含非法字符：%r" % (text,)


class Command(BaseCommand):
    help = ("Report (and optionally apply) the 18-digit -> last-6-digits conversion "
            "for every card column. Dry-run unless --apply is passed.")

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="真正写库。不加这个参数时只报告，不修改任何数据")
        parser.add_argument("--allow-collisions", action="store_true",
                            help="允许转换后出现重复的后 6 位（默认遇到撞号就中止）")
        parser.add_argument("--batch-size", type=int, default=500,
                            help="批量更新的批大小，默认 500")

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        allow_collisions = options["allow_collisions"]

        self.stdout.write("模式：%s" % ("APPLY（会写库）" if apply_changes else "DRY-RUN（只报告）"))
        self.stdout.write("")

        totals = Counter()
        collisions_total = 0
        invalid_samples = []
        collision_samples = []

        for label, model in CARD_MODELS:
            stats, updates, collisions, invalid = self._scan(model)
            totals.update(stats)
            collisions_total += len(collisions)
            invalid_samples.extend((label, err) for err in invalid[:10])
            collision_samples.extend((label, err) for err in collisions[:10])

            self.stdout.write("== %s ==" % label)
            self.stdout.write("  总数量            : %d" % stats["total"])
            self.stdout.write("  18位数量          : %d" % stats["length_18"])
            self.stdout.write("  6位数量           : %d" % stats["length_6"])
            self.stdout.write("  空值数量(NULL)    : %d" % stats["null"])
            self.stdout.write("  空字符串数量      : %d" % stats["empty_string"])
            self.stdout.write("  空格数量          : %d" % stats["spaces"])
            self.stdout.write("  异常长度数量      : %d" % stats["bad_length"])
            self.stdout.write("  非法字符数量      : %d" % stats["bad_chars"])
            self.stdout.write("  转换后重复数量    : %d" % len(collisions))
            self.stdout.write("  待转换            : %d" % len(updates))
            self.stdout.write("")

        self.stdout.write("===== 合计 =====")
        self.stdout.write("  总数量            : %d" % totals["total"])
        self.stdout.write("  18位数量          : %d" % totals["length_18"])
        self.stdout.write("  6位数量           : %d" % totals["length_6"])
        self.stdout.write("  空值数量(NULL)    : %d" % totals["null"])
        self.stdout.write("  空字符串数量      : %d" % totals["empty_string"])
        self.stdout.write("  空格数量          : %d" % totals["spaces"])
        self.stdout.write("  异常长度数量      : %d" % totals["bad_length"])
        self.stdout.write("  非法字符数量      : %d" % totals["bad_chars"])
        self.stdout.write("  转换后重复数量    : %d" % collisions_total)
        self.stdout.write("")

        if invalid_samples:
            self.stdout.write("【无法自动转换的值，需要人工处理】")
            for label, err in invalid_samples:
                self.stdout.write("  %s: %s" % (label, err))
            self.stdout.write("")

        if collision_samples:
            self.stdout.write("【转换后会撞号的值】")
            self.stdout.write("  说明：后 6 位撞号**不是错误** —— 不同的人本来就可能同后 6 位，"
                              "这正是本次改造取消 UNIQUE 的原因。")
            self.stdout.write("  列出仅为让运维知道「哪些行从此无法再靠 card 区分」，"
                              "它们仍然是人各一行，不会被合并或删除。")
            for label, err in collision_samples:
                self.stdout.write("  %s: %s" % (label, err))
            self.stdout.write("")

        # 真正的闸口：异常值 = 不可自动转换，必须人工处理
        if totals["bad_length"] or totals["bad_chars"]:
            self.stdout.write(self.style.ERROR(
                "存在异常长度/非法字符的值，已中止。请人工确认这些值后重跑。"))
            self.stdout.write("（本命令**不会**把这些值截断或置空。）")
            return

        if collisions_total and not allow_collisions:
            self.stdout.write(self.style.WARNING(
                "转换后存在撞号。确认业务上可接受后加 --allow-collisions 重跑。"))
            return

        if not apply_changes:
            self.stdout.write(self.style.SUCCESS("DRY-RUN 结束，未修改任何数据。"))
            self.stdout.write("确认报告后执行：python manage.py normalize_person_cards --apply")
            return

        written = 0
        for label, model in CARD_MODELS:
            _, updates, _, _ = self._scan(model)
            written += self._write(model, updates, options["batch_size"])
            self.stdout.write("  %s 已更新 %d 行" % (label, len(updates)))
        self.stdout.write(self.style.SUCCESS("APPLY 结束，共更新 %d 行。" % written))
        self.stdout.write("请接着执行 `python manage.py migrate` 应用 0008（列宽收紧到 6）。")

    # ------------------------------------------------------------------
    def _scan(self, model):
        """只读扫描一张表，返回 (统计, 待更新列表, 撞号说明, 非法值说明)。"""
        stats = Counter()
        updates = []          # [(pk, 新值), ...]
        final_values = defaultdict(list)   # 转换后的值 -> [pk, ...]
        invalid = []
        collision_notes = []

        rows = model.objects.all().values_list("pk", "card")
        for pk, raw in rows.iterator():
            stats["total"] += 1
            if raw is None:
                stats["null"] += 1
            elif raw == "":
                stats["empty_string"] += 1
            elif raw.strip() == "":
                stats["spaces"] += 1

            kind, value = classify(raw)
            if kind == "empty":
                continue
            if kind == "invalid":
                bucket, reason = invalid_reason(raw)
                stats[bucket] += 1
                invalid.append("pk=%s %s" % (pk, reason))
                continue

            if len(str(raw).strip()) == LEGACY_LENGTH:
                stats["length_18"] += 1
            else:
                stats["length_6"] += 1

            final_values[value].append(pk)
            if raw != value:
                updates.append((pk, value))

        for value, pks in final_values.items():
            if len(pks) > 1:
                collision_notes.append("%s 后 6 位 %s 共 %d 行：%s"
                                       % (model._meta.db_table, value, len(pks),
                                          ", ".join("pk=%s" % p for p in pks[:20])))
        return stats, updates, collision_notes, invalid

    # ------------------------------------------------------------------
    def _write(self, model, updates, batch_size):
        """按主键批量更新。每批一个事务，失败即整批回滚。"""
        if not updates:
            return 0
        # 先取出实例，避免用 queryset.update() 绕过 auto_now / 字段校验路径
        by_id = {obj.pk: obj for obj in model.objects.filter(pk__in=[p for p, _ in updates])}
        done = 0
        with transaction.atomic():
            for pk, value in updates:
                obj = by_id.get(pk)
                if obj is None:
                    continue
                obj.card = value
                obj.save(update_fields=["card"])
                done += 1
                if done % batch_size == 0:
                    self.stdout.write("    ... 已处理 %d 行" % done)
        return done
