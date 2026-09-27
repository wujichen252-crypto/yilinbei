"""身份证后 6 位改造的验收测试 —— 按实施任务书列出的 25 个场景编号组织。

测试名里的「场景N」对应任务书里的第 N 条，便于逐条对账。已在别处覆盖的场景
在这里只写一句指引，不重复造轮子（重复的测试会在改动时出现「改了一处、
另一处还绿着」的假象）：

    场景 7   → test_reports.PersonHeadAndIdentityTests.test_same_card_without_person_id_creates_a_new_person
    场景 10/11/13 → test_reports.PersonHeadAndIdentityTests.*
    场景 15/16 → test_tickets_pagination_openapi.TicketCapacityTests.*
    场景 20（前端写入分支） → yl-frontend/tests/photoMatch.test.mjs

【为什么单独一个文件而不是散进各测试文件】
本轮改造的验收标准是「全系统统一口径」。散着放的话，改的人只看单个文件，
很容易漏掉「Crew/Leader/TicketSubscribe/Student 也必须一起改」这类跨模型的一致性，
所以这里用数据驱动的方式把五个模型的字段定义一次性钉死。
"""
from django.core.validators import RegexValidator
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings

from apps.core.management.commands.normalize_person_cards import classify, invalid_reason
from apps.core.models import (CARD_ERROR, CARD_MAX_LENGTH, CARD_PATTERN, Crew, Leader,
                              Person, Report, ReportPerson, Student, TicketSubscribe,
                              User, normalize_card)

from .base import ApiTestCase, card_for

# 参与「后 6 位」口径的全部模型与字段形态。
# (模型, 是否允许为空, 中文名)  —— 允许为空的理由是这几张表的人员可以不填证件号，
# 而 person 表是报名主体的核心档案，必须有值（旧实现用 default=" " 占位，已删除）。
CARD_MODELS = (
    (Person, False, "person"),
    (Crew, True, "crew"),
    (Leader, True, "leader"),
    (TicketSubscribe, True, "ticket_subscribe"),
    (Student, True, "student"),
)

VALID_CARDS = [
    ("123456", "123456"),
    ("12345X", "12345X"),
    ("12345x", "12345X"),          # 末位小写 x 归一大写
    ("000001", "000001"),          # 前导零必须保留，不能当数字处理
    ("  12345x  ", "12345X"),      # 去首尾空格
    ("999999", "999999"),
]

INVALID_CARDS = [
    None,
    "",
    "   ",
    "12345",        # 少一位
    "1234567",      # 多一位
    "12345678",
    "110101199001011234",   # 18 位全号：必须被拒，绝不能悄悄截成后 6 位
    "ABCDEF",
    "1234A6",
    "abcdef",
    "12345X1",
    "１２３４５６",   # 全角数字
    "123 456",
    "12345-",
]


class NormalizeCardTests(ApiTestCase):
    """场景 1/2：格式与归一化的唯一权威实现。"""

    def test_scenario1_valid_cards_are_accepted_and_normalized(self):
        for raw, expected in VALID_CARDS:
            with self.subTest(raw=raw):
                ok, value = normalize_card(raw)
                self.assertTrue(ok, "应接受 %r" % (raw,))
                self.assertEqual(value, expected)

    def test_scenario2_invalid_cards_are_rejected_with_one_message(self):
        for raw in INVALID_CARDS:
            with self.subTest(raw=raw):
                ok, message = normalize_card(raw)
                self.assertFalse(ok, "应拒绝 %r" % (raw,))
                self.assertEqual(message, CARD_ERROR)

    def test_scenario2_full_id_number_is_never_silently_truncated(self):
        """18 位全号必须被**拒绝**。

        这条是本轮改造里最容易写错的地方：`card[-6:]` 能把任意长度的输入
        变成一段看着合法的 6 位数字。那样写的话，18 位全号会被静默截断入库，
        用户以为自己填的是完整身份证 —— 改造的初衷（不采集完整身份证）
        就完全落空了，而且没有任何报错能让人发现。
        """
        ok, message = normalize_card("110101199001011234")
        self.assertFalse(ok)
        self.assertEqual(message, CARD_ERROR)

    def test_scenario2_non_string_input_does_not_crash(self):
        """非字符串不炸、且只有能原样还原成合法 6 位的才放行。

        Excel 导入路径里 `123456` 可能以数字类型到达，所以「整数能还原成同样的
        6 位」是**要放行**的（前导零的数字在 Excel 里本来就已经丢了，那种情况
        会因位数不足被拒，不会静默变成另一个人）。
        """
        self.assertEqual(normalize_card(123456), (True, "123456"))
        # 会丢失或改变原值的一律拒绝
        for raw in (12345.6, 123456.0, [1, 2, 3], {"a": 1}, True, 1234):
            with self.subTest(raw=raw):
                self.assertFalse(normalize_card(raw)[0])
        # None 单独一条：旧实现里 None 会被 str() 成 "None" 再截 6 位
        self.assertFalse(normalize_card(None)[0])

    def test_scenario2_unicode_digits_are_not_ascii_digits(self):
        """全角/阿拉伯-印度数字不是合法身份证字符，必须拒绝。

        Python 的 str.isdigit() 是 Unicode 语义的（'１'.isdigit() 为 True），
        用它当判据会让这类值被判成合法 —— 而模型的校验器用的是 [0-9]。
        两边判据不一致时，值是「命令认为合法、接口认为非法」的，谁都改不动它。
        """
        for raw in ("12345１", "１２３４５６", "1234５6", "١٢٣٤٥٦"):
            with self.subTest(raw=raw):
                self.assertFalse(normalize_card(raw)[0])

    def test_pattern_and_error_text_are_the_single_source(self):
        import re
        self.assertTrue(re.match(CARD_PATTERN, "12345X"))
        self.assertTrue(re.match(CARD_PATTERN, "12345x"))
        self.assertIsNone(re.match(CARD_PATTERN, "1234567"))
        self.assertEqual(CARD_MAX_LENGTH, 6)


class CardFieldDefinitionTests(ApiTestCase):
    """场景 3/4/5：五个模型的字段定义必须一次到齐，不留半迁移状态。"""

    def test_scenario3_person_card_is_6_chars_not_unique_not_null_no_default(self):
        field = Person._meta.get_field("card")
        self.assertEqual(field.max_length, CARD_MAX_LENGTH)
        self.assertFalse(field.unique, "person.card 不得唯一 —— 后 6 位撞号是常态")
        self.assertFalse(field.null)
        self.assertFalse(field.has_default(), 'default=" " 占位符必须删除')

    def test_scenario4_every_other_card_field_shares_the_same_shape(self):
        for model, nullable, label in CARD_MODELS:
            field = model._meta.get_field("card")
            with self.subTest(model=label):
                self.assertEqual(field.max_length, CARD_MAX_LENGTH)
                self.assertFalse(field.unique, "%s.card 不得唯一" % label)
                self.assertEqual(field.null, nullable)
                self.assertFalse(field.has_default(), "%s.card 不得有默认值" % label)
                # CharField 会自动挂一个 MaxLengthValidator，只看正则那几个
                patterns = [v.regex.pattern for v in field.validators
                            if isinstance(v, RegexValidator)]
                self.assertEqual(
                    patterns, [CARD_PATTERN],
                    "%s.card 的校验器必须就是 CARD_PATTERN，不能各写一份" % label,
                )

    def test_scenario3_no_composite_unique_constraint_mentions_card(self):
        """不许用 unique_together / UniqueConstraint 把 card 拼回去。

        `unique(card)`、`unique(name, card)`、`unique(report_id, card)` 都是把
        「撞号」重新变成错误 —— 与本次改造的前提直接矛盾，必须一起钉住。
        """
        for model, _, label in CARD_MODELS:
            constraints = list(model._meta.constraints)
            together = list(model._meta.unique_together or [])
            with self.subTest(model=label):
                for constraint in constraints:
                    fields = getattr(constraint, "fields", ())
                    self.assertNotIn("card", fields,
                                     "%s 的组合唯一约束不能含 card：%r" % (label, constraint))
                for group in together:
                    self.assertNotIn("card", group,
                                     "%s 的 unique_together 不能含 card：%r" % (label, group))

    def test_scenario5_two_people_may_share_one_card_in_the_database(self):
        """真正写两行进库，验证 UNIQUE 索引确实没了（模型定义看不出的部分）。"""
        school = self.create_user("dup-school", 0)
        first = Person.objects.create(name="甲", card=card_for("dup"), user_id=school.id)
        second = Person.objects.create(name="乙", card=card_for("dup"), user_id=school.id)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(Person.objects.filter(card=card_for("dup")).count(), 2)

    def test_scenario5_person_card_is_not_nullable_at_the_database_level(self):
        school = self.create_user("null-school", 0)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Person.objects.create(name="无证件", card=None, user_id=school.id)


@override_settings(
    PERSON_HEAD_ALLOWED_DOMAINS=["avatars.example.com"],
    PERSON_HEAD_CDN_DOMAINS=[".cdn.example.com"],
    QINIU_DOMAIN="",
    ALIYUN_OSS_HOST="",
    ALIYUN_OSS_BUCKET="",
    ALIYUN_OSS_ENDPOINT="",
)
class CardApiTests(ApiTestCase):
    """场景 6：接口层必须与 normalize_card 同一口径。"""

    def setUp(self):
        self.school = self.create_user("card-school", 0)
        self.authorize_as(self.school)

    def payload(self, people, **overrides):
        value = {
            "choir_name": "口径测试团队",
            "name": "口径测试节目",
            "group": "大学组",
            "establishment": "管乐团",
            "contact_name": "联系人",
            "contact_phone": "13800000000",
            "time_length": 120,
            "person": people,
        }
        value.update(overrides)
        return value

    def test_scenario6_invalid_card_is_rejected_and_nothing_is_written(self):
        for bad in ("12345", "110101199001011234", "ABCDEF", ""):
            with self.subTest(card=bad):
                result = self.json_request("post", "/api/school/report/create", self.payload(
                    [{"name": "张三", "card": bad, "position": 0, "type": 0}]
                ))
                self.assertEqual(result.json()["code"], 1)
                self.assertIn(CARD_ERROR, result.json()["msg"])
                self.assertFalse(Report.objects.exists())
                self.assertFalse(Person.objects.exists())

    def test_scenario6_valid_card_is_trimmed_and_normalized_before_storage(self):
        result = self.json_request("post", "/api/school/report/create", self.payload(
            [{"name": "张三", "card": "  12345x  ", "position": 0, "type": 0}]
        ))
        self.assertEqual(result.json()["code"], 0)
        self.assertEqual(Person.objects.get().card, "12345X")

    def test_scenario9_one_payload_may_contain_the_same_card_twice(self):
        """同一份提交里两行同后 6 位、都没有 person_id → 建两条 person、两条关联。

        这是本次改造最反直觉的一条：改前第二行会被当成「重复人员」被合并或拒绝。
        现在 card 不标识身份，两行就是两个人，必须各建一条。
        """
        same = card_for("same-in-payload")
        result = self.json_request("post", "/api/school/report/create", self.payload([
            {"name": "张甲", "card": same, "position": 0, "type": 0},
            {"name": "张乙", "card": same, "position": 1, "type": 0},
        ]))
        self.assertEqual(result.json()["code"], 0)
        self.assertEqual(Person.objects.filter(card=same).count(), 2)
        self.assertEqual(ReportPerson.objects.count(), 2)
        self.assertEqual(len({link.person_id for link in ReportPerson.objects.all()}), 2)

    def test_scenario10_the_same_person_id_twice_in_one_payload_is_allowed(self):
        """同一个 person_id 出现两次 → 复用同一行，产生两条关联。

        业务上真实存在：一个人既是指挥又要上场演奏时会被填两行（type/position 不同）。
        改前靠 card 去重会把它并成一行，丢掉其中一次出场。
        """
        mine = Person.objects.create(
            name="双角色", card=card_for("dual"), user_id=self.school.id
        )
        result = self.json_request("post", "/api/school/report/create", self.payload([
            {"person_id": mine.id, "name": "双角色", "card": card_for("dual"),
             "position": 0, "type": 0},
            {"person_id": mine.id, "name": "双角色", "card": card_for("dual"),
             "position": 1, "type": 1},
        ]))
        self.assertEqual(result.json()["code"], 0)
        self.assertEqual(Person.objects.count(), 1)
        links = list(ReportPerson.objects.order_by("position"))
        self.assertEqual(len(links), 2)
        self.assertEqual({link.person_id for link in links}, {mine.id})

    def test_scenario11_person_id_is_not_rejected_when_the_name_differs(self):
        """姓名不一致既不构成拒绝理由，也不该被静默丢弃 —— 身份基准是 person_id。

        改前是「card 命中但 name 不符 → 报错」。现在用户显式指定了 person_id，
        就表示「我知道我在复用谁」；姓名可能是更正错别字。
        两种错法都要防：一是拿姓名去否决用户的显式选择（报错），
        二是报成功但不落库（改名变成空操作，用户以为改好了）。
        """
        mine = Person.objects.create(
            name="原名", card=card_for("rename"), user_id=self.school.id
        )
        result = self.json_request("post", "/api/school/report/create", self.payload([
            {"person_id": mine.id, "name": "改过的名字", "card": card_for("rename"),
             "position": 0, "type": 0},
        ]))
        self.assertEqual(result.json()["code"], 0)
        mine.refresh_from_db()
        self.assertEqual(mine.name, "改过的名字")
        self.assertEqual(Person.objects.count(), 1)

    def test_scenario12_non_integer_person_id_is_rejected_explicitly(self):
        """person_id 类型不对必须明确报错，不许当成「没传 person_id」静默新建。"""
        for bad in ("abc", 1.5, True, -1, 0, "12.0"):
            with self.subTest(person_id=bad):
                result = self.json_request("post", "/api/school/report/create", self.payload([
                    {"person_id": bad, "name": "张三", "card": card_for("bad-pid"),
                     "position": 0, "type": 0},
                ]))
                self.assertEqual(result.json()["code"], 1)
                self.assertFalse(Person.objects.exists(),
                                 "person_id=%r 被当成了「没传」，静默新建了一行" % (bad,))

    def test_scenario12_numeric_string_person_id_is_accepted(self):
        """表单里 id 常以字符串到达（URL/JSON 序列化），数字串要认。"""
        mine = Person.objects.create(
            name="字符串id", card=card_for("str-pid"), user_id=self.school.id
        )
        result = self.json_request("post", "/api/school/report/create", self.payload([
            {"person_id": str(mine.id), "name": "字符串id", "card": card_for("str-pid"),
             "position": 0, "type": 0},
        ]))
        self.assertEqual(result.json()["code"], 0)
        self.assertEqual(Person.objects.count(), 1)

    def test_scenario13_user_id_in_payload_cannot_hijack_a_person_when_reusing(self):
        """带 person_id 复用他校人员时，客户端塞的 user_id 不能把归属改过来。"""
        other = self.create_user("other-unit", 0)
        foreign = Person.objects.create(
            name="他校人员", card=card_for("hijack"), user_id=other.id
        )
        result = self.json_request("post", "/api/school/report/create", self.payload([
            {"person_id": foreign.id, "name": "他校人员", "card": card_for("hijack"),
             "user_id": self.school.id, "position": 0, "type": 0},
        ]))
        self.assertEqual(result.json()["code"], 1)
        self.assertEqual(result.json()["msg"], "人员不属于当前单位")
        foreign.refresh_from_db()
        self.assertEqual(foreign.user_id, other.id)

    def test_scenario14_reuse_updates_profile_fields_but_never_ownership(self):
        mine = Person.objects.create(
            name="档案", card=card_for("profile"), user_id=self.school.id,
            phone="old-phone", school="旧学校",
        )
        result = self.json_request("post", "/api/school/report/create", self.payload([
            {"person_id": mine.id, "name": "档案", "card": card_for("profile"),
             "phone": "new-phone", "school": "新学校",
             "head": "https://avatars.example.com/a.png", "position": 0, "type": 0},
        ]))
        self.assertEqual(result.json()["code"], 0)
        mine.refresh_from_db()
        self.assertEqual(mine.phone, "new-phone")
        self.assertEqual(mine.school, "新学校")
        self.assertEqual(mine.head, "https://avatars.example.com/a.png")
        self.assertEqual(mine.user_id, self.school.id)

    def test_scenario14_a_rejected_field_stops_the_whole_submission(self):
        """档案字段里有一个不合法 → 整单回滚，其余字段一个都不许落库。

        否则用户看到报错、改完重交，中间态已经把一半字段写进去了。
        """
        mine = Person.objects.create(
            name="档案", card=card_for("profile-rollback"), user_id=self.school.id,
            phone="old-phone",
        )
        result = self.json_request("post", "/api/school/report/create", self.payload([
            {"person_id": mine.id, "name": "档案", "card": card_for("profile-rollback"),
             "phone": "new-phone", "head": "https://evil.example.com/a.png",
             "position": 0, "type": 0},
        ]))
        self.assertEqual(result.json()["code"], 1)
        mine.refresh_from_db()
        self.assertEqual(mine.phone, "old-phone")
        self.assertFalse(Report.objects.exists())


class TicketCardTests(ApiTestCase):
    """场景 17：票务系统的 card 也必须是非唯一的后 6 位。"""

    def setUp(self):
        from apps.core.models import Ticket
        self.ticket = Ticket.objects.create(
            type_name="场景17场次", ticket_session="上午场", time="9月16日", number=3
        )

    def book(self, name, card, remote_addr="127.0.0.1"):
        return self.json_request("post", "/api/ticket/make", {
            "ticket_id": self.ticket.id, "name": name, "card": card,
            "phone": "13800000000",
        }, REMOTE_ADDR=remote_addr)

    def test_scenario17_ticket_card_field_is_not_unique(self):
        field = TicketSubscribe._meta.get_field("card")
        self.assertEqual(field.max_length, CARD_MAX_LENGTH)
        self.assertFalse(field.unique)
        self.assertFalse(field.has_default())

    def test_scenario17_two_subscriptions_may_share_a_card(self):
        """同场次、同后 6 位、不同姓名 → 两条预约都能成立。"""
        same = card_for("ticket-shared")
        first = self.book("张三", same, remote_addr="10.0.0.1")
        second = self.book("李四", same, remote_addr="10.0.0.2")
        self.assertEqual(first.json()["code"], 0)
        self.assertEqual(second.json()["code"], 0)
        self.assertEqual(TicketSubscribe.objects.filter(card=same).count(), 2)
        self.assertEqual(
            sorted(TicketSubscribe.objects.values_list("name", flat=True)), ["张三", "李四"]
        )

    def test_scenario17_full_id_number_is_rejected_by_the_ticket_api(self):
        result = self.book("张三", "110101199001011234")
        self.assertEqual(result.json()["code"], 1)
        self.assertEqual(result.json()["msg"], CARD_ERROR)
        self.assertFalse(TicketSubscribe.objects.exists())

    def test_scenario17_booking_card_is_normalized_like_everywhere_else(self):
        result = self.book("张三", " 12345x ")
        self.assertEqual(result.json()["code"], 0)
        self.assertEqual(TicketSubscribe.objects.get().card, "12345X")

    def test_scenario17_query_normalizes_and_rejects_invalid_input(self):
        self.book("张三", "12345X")
        hit = self.client.get("/api/ticket/my", {"name": "张三", "card": "12345x"}).json()
        invalid = self.client.get("/api/ticket/my", {"name": "张三", "card": "1234567"}).json()
        full = self.client.get(
            "/api/ticket/my", {"name": "张三", "card": "110101199001011234"}
        ).json()
        self.assertEqual(len(hit["data"]), 1)
        self.assertEqual(invalid["data"], [])
        self.assertEqual(full["data"], [])


class NormalizeCommandClassifyTests(ApiTestCase):
    """场景 24/25：数据转换的判定规则 —— 先测纯函数，再测整条命令。"""

    def test_scenario21_eighteen_digit_values_convert_to_the_last_six(self):
        cases = [
            ("110101199001011234", "convert", "011234"),
            ("11010119900102123X", "convert", "02123X"),
            ("11010119900103123x", "convert", "03123X"),   # 小写 x 归一大写
            ("000000000000000000", "convert", "000000"),
        ]
        for raw, kind, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(classify(raw), (kind, expected))

    def test_scenario22_six_digit_values_are_kept_as_is(self):
        for raw in ("123456", "12345X", "000001", "999999"):
            with self.subTest(raw=raw):
                self.assertEqual(classify(raw), ("ok", raw))
        # 只有末位小写 x 需要改写
        self.assertEqual(classify("12345x"), ("convert", "12345X"))

    def test_scenario23_empty_values_need_no_conversion(self):
        for raw in (None, "", "   ", "\t"):
            with self.subTest(raw=raw):
                self.assertEqual(classify(raw), ("empty", None))

    def test_scenario24_abnormal_lengths_are_never_truncated(self):
        """7 位、15 位、带连字符的值一律 invalid。

        这里防的是「静默截断」：`text[-6:]` 对以上每一种都能编出一段看着合法的
        6 位数字。真那么做的话，一个人的身份证后 6 位会被悄悄换成另一段数字，
        而日志里什么都看不出来。
        """
        for raw in ("1234567", "12345", "110101900101123", "110101-1990", "12345678901234567"):
            with self.subTest(raw=raw):
                self.assertEqual(classify(raw), ("invalid", None))
                self.assertNotEqual(invalid_reason(raw)[0], "")
        # 明确检查两个归类桶，供命令的报告输出使用
        self.assertEqual(invalid_reason("1234567")[0], "bad_length")
        self.assertEqual(invalid_reason("12A456")[0], "bad_chars")

    def test_scenario24_invalid_characters_are_reported_not_repaired(self):
        for raw in ("12345A", "ABCDEF", "12 456", "12345-"):
            with self.subTest(raw=raw):
                self.assertEqual(classify(raw), ("invalid", None))
        self.assertEqual(invalid_reason("12345A")[0], "bad_chars")

    def test_scenario24_unicode_digits_are_invalid_not_accepted(self):
        """全角/阿拉伯-印度数字必须落进「非法字符」桶。

        这条曾经真的漏掉了：classify 原先用 str.isdigit() 判字符，而它是 Unicode
        语义的（'１'.isdigit() 为 True），于是这类值被判成「已经是合法的 6 位」，
        既不转换也不报错，原样留在库中。命令还宣称自己「已完成转换」。
        而接口侧的判据是 [0-9]，从此永远拒绝这一行 —— 那条记录谁都改不动。
        """
        for raw in ("12345１", "１２３４５６", "1234５6"):
            with self.subTest(raw=raw):
                self.assertEqual(classify(raw), ("invalid", None))
                self.assertEqual(invalid_reason(raw)[0], "bad_chars")


# ---------------------------------------------------------------------------
# 场景 21~25：迁移与数据转换命令
#
# 【为什么用 TransactionTestCase 而不是 TestCase】
# 这几个用例要真的把 schema 迁回 0006、灌入 18 位历史数据、再迁移到 0008。
# TestCase 把每个用例包在一个事务里，DDL 与 MigrationExecutor 在其中的行为
# 与真实迁移不同（而且 schema 变化无法随事务回滚），所以必须用 TransactionTestCase。
#
# 【tearDown 必须迁回最新】否则后面的用例看到的表结构就不是最新的了 ——
# 那是一种非常隐蔽的连带失败：报错在别的测试文件里，原因却在这里。
# ---------------------------------------------------------------------------
LEGACY = "0006_reportdraft_unique_editing"
RELAXED = "0007_card_relax_unique_and_notnull"
TIGHTENED = "0008_card_last6_length"


class CardMigrationTests(TransactionTestCase):
    """场景 21/23：迁回 0006 灌入 18 位数据 → 转换 → 迁移到 0008 的完整路径。"""

    # 让 Django 每个用例后清库；schema 由 tearDown 负责复原
    reset_sequences = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def migrate_to(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("core", target)])
        executor.loader.build_graph()
        return executor

    def person_columns(self):
        with connection.cursor() as cursor:
            return connection.introspection.get_table_description(cursor, "person")

    def column(self, name):
        for field in self.person_columns():
            if field.name == name:
                return field
        self.fail("person 表没有 %s 列" % name)

    @staticmethod
    def run_command(*args):
        from io import StringIO

        from django.core.management import call_command
        out = StringIO()
        call_command("normalize_person_cards", *args, stdout=out)
        return out.getvalue()

    def test_scenario21_schema_is_relaxed_before_data_and_tightened_after(self):
        """0007 摘掉 UNIQUE、列宽仍 255；0008 才收到 6。

        顺序不能反：表里还有 18 位数据时把列宽收到 varchar(6)，
        PostgreSQL/GaussDB 会直接报 `value too long for type character varying(6)`
        让整个迁移失败。
        """
        self.migrate_to(RELAXED)
        card = self.column("card")
        self.assertEqual(card.internal_size, 255, "0007 阶段列宽必须仍是 255")
        self.assertFalse(card.null_ok, "0007 阶段 person.card 必须仍是 NOT NULL")

        self.migrate_to(TIGHTENED)
        card = self.column("card")
        self.assertEqual(card.internal_size, 6)
        self.assertFalse(card.null_ok)

        # UNIQUE 索引必须真的没了 —— 只看模型定义看不出这一点
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, "person")
        uniques = [name for name, spec in constraints.items()
                   if spec.get("unique") and spec.get("columns") == ["card"]]
        self.assertEqual(uniques, [], "person.card 上的 UNIQUE 索引没被摘掉")

    def test_scenario23_command_converts_legacy_rows_and_leaves_the_rest_alone(self):
        self.migrate_to(RELAXED)
        school = User.objects.create(username="legacy-school", nickname="学校", type=0)
        cases = {
            "110101199001011234": "011234",
            "11010119900102123X": "02123X",
            "11010119900103123x": "03123X",
            "123456": "123456",           # 已经是 6 位，不动
            "000001": "000001",           # 前导零必须原样保留
        }
        pks = {}
        for raw in cases:
            row = Person.objects.create(name="历史", card=raw, user_id=school.id)
            pks[raw] = row.pk

        report = self.run_command()
        self.assertIn("DRY-RUN", report)
        # dry-run 一个字节都不能写
        for raw in cases:
            self.assertEqual(Person.objects.get(pk=pks[raw]).card, raw,
                             "dry-run 改动了数据：%r" % raw)

        applied = self.run_command("--apply")
        self.assertIn("APPLY", applied)
        for raw, expected in cases.items():
            self.assertEqual(Person.objects.get(pk=pks[raw]).card, expected,
                             "%r 应转换成 %r" % (raw, expected))

        # 迁移收紧到 6 位必须能成功（数据已经全是 6 位了）
        self.migrate_to(TIGHTENED)
        self.assertEqual(self.column("card").internal_size, 6)

    def test_scenario24_abnormal_values_abort_the_whole_run(self):
        """异常长度/非法字符 → 中止，且**任何一行**都不许被改写。

        含「7 位数字」这一条尤其重要：`text[-6:]` 能把 7 位截成一段看着合法的
        6 位，静默截断等于把一个人的证件号换成另一个人的，且日志里看不出来。
        """
        self.migrate_to(RELAXED)
        school = User.objects.create(username="bad-school", nickname="学校", type=0)
        good = Person.objects.create(name="正常", card="110101199001011234",
                                     user_id=school.id)
        bad = Person.objects.create(name="异常", card="1234567", user_id=school.id)

        report = self.run_command("--apply")
        self.assertIn("异常长度", report)
        self.assertIn("已中止", report)
        # 合法的那一行也不许被顺手改掉（整批中止，不是「跳过坏的、改好的」）
        self.assertEqual(Person.objects.get(pk=good.pk).card, "110101199001011234")
        self.assertEqual(Person.objects.get(pk=bad.pk).card, "1234567")

    def test_scenario25_collisions_are_reported_but_never_merged_or_deleted(self):
        """撞号只提示，不合并、不删除 —— 撞号本身不是错误。"""
        self.migrate_to(RELAXED)
        school = User.objects.create(username="collide-school", nickname="学校", type=0)
        # 两个不同的 18 位全号，后 6 位恰好相同（011234）—— 这就是真实世界里
        # 的撞号场景，不是构造出来的极端值
        first = Person.objects.create(name="甲", card="110101199001011234",
                                      user_id=school.id)
        second = Person.objects.create(name="乙", card="110101198802011234",
                                       user_id=school.id)

        blocked = self.run_command("--apply")
        self.assertIn("转换后重复数量    : 1", blocked)
        self.assertIn("--allow-collisions", blocked)
        # 未加开关 → 两行都保持原样
        self.assertEqual(Person.objects.get(pk=first.pk).card, "110101199001011234")
        self.assertEqual(Person.objects.get(pk=second.pk).card, "110101198802011234")

        self.run_command("--apply", "--allow-collisions")
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.card, "011234")
        self.assertEqual(second.card, "011234")
        # 两条记录都还在，各自一个主键 —— 没有被合并成一条
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(Person.objects.filter(card="011234").count(), 2)

    def test_scenario25_command_never_deletes_rows(self):
        self.migrate_to(TIGHTENED)
        school = User.objects.create(username="keep-school", nickname="学校", type=0)
        Person.objects.create(name="甲", card="123456", user_id=school.id)
        Person.objects.create(name="乙", card="123456", user_id=school.id)

        self.run_command("--apply")

        # 只统计不断言文案：撞号说明里本来就会出现「不会被合并或删除」这句话。
        # 真正的不变量是「行数不变、主键不变」。
        self.assertEqual(Person.objects.count(), 2)
        self.assertEqual(sorted(Person.objects.values_list("pk", flat=True)), [1, 2])

    def test_migrations_contain_no_data_deleting_operations(self):
        """迁移里不许出现删数据的 RunPython。

        18 位 → 后 6 位不可逆，一旦迁移里藏了清理逻辑，
        部署时就会在没有人工确认的情况下永久丢掉数据。
        """
        from django.db.migrations import RunPython, RunSQL

        for name in ("0007_card_relax_unique_and_notnull", "0008_card_last6_length"):
            module = __import__("apps.core.migrations.%s" % name, fromlist=["Migration"])
            migration = module.Migration
            for operation in migration.operations:
                with self.subTest(migration=name, operation=type(operation).__name__):
                    self.assertNotIsInstance(operation, (RunPython, RunSQL))
                    self.assertFalse(hasattr(operation, "code"),
                                     "迁移里不得内联 SQL")
