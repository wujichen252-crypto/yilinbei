from unittest.mock import patch

from django.db import IntegrityError
from django.test import override_settings

from apps.core.models import Person, Report, ReportPerson
from apps.core.services import valid_person_head

from .base import ApiTestCase, card_for


class ReportTransactionAndSoftDeleteTests(ApiTestCase):
    def setUp(self):
        self.school = self.create_user("school", 0)
        self.authorize_as(self.school)

    def report_payload(self, **overrides):
        payload = {
            "choir_name": "事务测试团队",
            "name": "事务测试节目",
            "group": "大学组",
            "establishment": "管乐团",
            "contact_name": "联系人",
            "contact_phone": "13800000000",
            "time_length": 180,
            "dinner_reservation": [{"date": "2026-09-16", "count": 2}],
            "person": [],
        }
        payload.update(overrides)
        return payload

    def test_failed_create_rolls_back_people_created_earlier_in_request(self):
        """请求中途写库失败时，同一请求里先建好的 Person 必须跟着回滚。

        【2026-09-23 触发方式变更】改前这里靠「第二行与已存在人员同 card 但姓名不符」
        触发失败；新口径下 card 不再标识身份，该输入是**合法**的（会正常新建两行），
        所以换成一个与业务规则无关的写库异常来触发。要钉住的一直是事务边界
        （不留孤儿 Person / 不留半张报名表），不是某一条具体的校验分支。
        """
        payload = self.report_payload(
            person=[
                {"name": "先创建", "card": card_for("new"), "position": 0, "type": 0},
                {"name": "后创建", "card": card_for("later"), "position": 1, "type": 0},
            ]
        )
        real_create = Person.objects.create
        state = {"calls": 0}

        def flaky_create(*args, **kwargs):
            state["calls"] += 1
            if state["calls"] == 2:
                raise IntegrityError("注入：第二个人写库失败")
            return real_create(*args, **kwargs)

        with patch.object(Person.objects, "create", side_effect=flaky_create):
            with self.assertRaises(IntegrityError):
                self.json_request("post", "/api/school/report/create", payload)

        self.assertEqual(state["calls"], 2)   # 确认真的走到了第二个人
        self.assertFalse(Report.objects.exists())
        self.assertFalse(Person.objects.filter(card=card_for("new")).exists())
        self.assertFalse(Person.objects.filter(card=card_for("later")).exists())

    def test_failed_update_keeps_report_links_and_people_unchanged(self):
        """关联写入失败时，本次已改的报表字段、已软删的旧关联、已建的新人员全部回滚。

        【2026-09-23 触发方式变更】同上一个用例：原触发条件已合法化，改用注入的
        bulk_create 失败 —— 它发生在 report.save() **之后**，正好能证明回滚范围
        覆盖了报表本身，而不只是人员那一步。
        """
        report = self.make_report(self.school, name="原节目", status=1)
        old_person = Person.objects.create(
            name="原成员", card=card_for("old"), user_id=self.school.id
        )
        old_link = ReportPerson.objects.create(
            report_id=report.id, person_id=old_person.id, position=0, type=0
        )
        payload = self.report_payload(
            id=report.id,
            name="不应保存的新节目名",
            person=[
                {"name": "临时成员", "card": card_for("temporary"), "position": 0, "type": 0},
                {"name": "另一成员", "card": card_for("second"), "position": 1, "type": 0},
            ],
        )

        with patch.object(ReportPerson.objects, "bulk_create",
                          side_effect=IntegrityError("注入：关联写入失败")):
            with self.assertRaises(IntegrityError):
                self.json_request("put", "/api/school/report/update", payload)

        report.refresh_from_db()
        old_link.refresh_from_db()
        self.assertEqual(report.name, "原节目")
        self.assertEqual(report.status, 1)
        self.assertIsNone(old_link.deleted_at)   # 旧关联的软删也被回滚
        self.assertFalse(Person.objects.filter(card=card_for("temporary")).exists())
        self.assertFalse(Person.objects.filter(card=card_for("second")).exists())
        self.assertEqual(
            list(ReportPerson.objects.filter(report_id=report.id).values_list("person_id", flat=True)),
            [old_person.id],
        )

    def test_delete_is_soft_and_deleted_report_disappears_from_api(self):
        report = self.make_report(self.school)

        result = self.client.delete(f"/api/school/report/delete/{report.id}")

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["code"], 0)
        self.assertFalse(Report.objects.filter(pk=report.id).exists())
        deleted = Report.all_objects.get(pk=report.id)
        self.assertIsNotNone(deleted.deleted_at)
        listing = self.client.get("/api/school/report/list").json()
        self.assertEqual(listing["count"], 0)
        self.assertEqual(listing["data"], [])

    def test_successful_update_soft_deletes_old_links_and_resets_review(self):
        report = self.make_report(self.school, status=1)
        old_person = Person.objects.create(
            name="旧成员", card=card_for("old"), user_id=self.school.id
        )
        old_link = ReportPerson.objects.create(
            report_id=report.id, person_id=old_person.id, position=0, type=0
        )
        payload = self.report_payload(
            id=report.id,
            name="更新后的节目",
            person=[
                {"name": "新成员", "card": card_for("new"), "position": 2, "type": 1}
            ],
        )

        result = self.json_request("put", "/api/school/report/update", payload)

        self.assertEqual(result.json()["code"], 0)
        report.refresh_from_db()
        old_link.refresh_from_db()
        self.assertEqual(report.status, 0)
        self.assertIsNotNone(old_link.deleted_at)
        links = list(ReportPerson.objects.filter(report_id=report.id))
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].position, 2)
        self.assertEqual(Person.objects.get(pk=links[0].person_id).card, card_for("new"))

    def test_create_coerces_signature_order_leniently(self):
        # 直传路径宽松规整："3"→3；0/布尔归 None（不新增报错面，草稿路径才严格 400）
        payload = self.report_payload(person=[
            {"name": "老师A", "card": card_for("sig-a"), "position": 4, "type": 1,
             "signature_order": "3"},
            {"name": "老师B", "card": card_for("sig-b"), "position": 4, "type": 1,
             "signature_order": 0},
            {"name": "老师C", "card": card_for("sig-c"), "position": 4, "type": 1,
             "signature_order": True},
        ])

        result = self.json_request("post", "/api/school/report/create", payload)

        self.assertEqual(result.json()["code"], 0)
        cards = {p.id: p.card for p in Person.objects.all()}
        orders = {cards[link.person_id]: link.signature_order
                  for link in ReportPerson.objects.all()}
        self.assertEqual(orders, {card_for("sig-a"): 3, card_for("sig-b"): None,
                                  card_for("sig-c"): None})

    def test_create_coerces_display_order_leniently(self):
        # 直传路径宽松规整："2"→2；与 signature_order 的关键差异：0 是合法行下标必须保留
        payload = self.report_payload(person=[
            {"name": "老师A", "card": card_for("do-a"), "position": 4, "type": 1,
             "display_order": "2"},
            {"name": "老师B", "card": card_for("do-b"), "position": 4, "type": 1,
             "display_order": 0},
            {"name": "老师C", "card": card_for("do-c"), "position": 4, "type": 1,
             "display_order": -1},
            {"name": "老师D", "card": card_for("do-d"), "position": 4, "type": 1,
             "display_order": True},
        ])

        result = self.json_request("post", "/api/school/report/create", payload)

        self.assertEqual(result.json()["code"], 0)
        cards = {p.id: p.card for p in Person.objects.all()}
        orders = {cards[link.person_id]: link.display_order
                  for link in ReportPerson.objects.all()}
        self.assertEqual(orders, {card_for("do-a"): 2, card_for("do-b"): 0,
                                  card_for("do-c"): None, card_for("do-d"): None})

    def test_create_coerces_dinner_counts_leniently(self):
        # 直传路径宽松规整：数字字符串/0 收下并补齐 6 位，负数/布尔/垃圾归 None（不报错）
        payload = self.report_payload(
            dinner_reservation_counts=["12", 0, -1, True, "x", 3])

        result = self.json_request("post", "/api/school/report/create", payload)

        self.assertEqual(result.json()["code"], 0)
        report = Report.objects.get()
        self.assertEqual(report.dinner_reservation_counts, [12, 0, None, None, None, 3])

    def test_province_create_strips_dinner_fields(self):
        # 省级代报不受理用餐字段：与 dinner_reservation 同款整体剥离
        province = self.create_user("province-strip", 4)
        self.authorize_as(province)
        payload = self.report_payload(dinner_reservation=["0"],
                                      dinner_reservation_counts=[1, 2])

        result = self.json_request("post", "/api/province/report/create", payload)

        self.assertEqual(result.json()["code"], 0)
        report = Report.objects.get()
        self.assertEqual(report.dinner_reservation, [])
        # 剥离后从未写入：列保持空值语义 []
        self.assertEqual(report.dinner_reservation_counts, [])


class RejectedReportEditPeopleTests(ApiTestCase):
    """驳回后编辑的人员链路（组委会端 2026-09-27 反馈的两个问题）：

    1. 「修改身份证存成新人员」—— 前端没把 Person id 传回来；不带 id 一律
       新建是后六位口径的既定行为（无查重），带 id 必须原地更新。
    2. 「删除人员数据库未改」—— 前端把详情回显的外层 id（关联行
       report_person.id）当 Person id 传回，整单被「人员不存在」拦下。
    本组把详情回显形（id=关联行 id + person_id=Person id）的正确行为钉死。
    """

    def setUp(self):
        self.school = self.create_user("rej-school", 0)
        self.committee = self.create_user("rej-committee", 2)
        self.authorize_as(self.committee)
        # 错位自增序列：先造 1 个占位人员 + 一条 4 关联行的占位报名，让本报名的
        # 关联行 id 落在所有 Person id 之外 —— 否则新库里 report_person.id 与
        # person.id 恰好相等，「传关联行 id」会静默命中错误的人，测试失真
        decoy = Person.objects.create(name="占位", card=card_for("decoy"),
                                      user_id=self.school.id)
        other = self.make_report(self.school, choir_name="占位团队")
        for _ in range(4):
            ReportPerson.objects.create(report_id=other.id, person_id=decoy.id,
                                        position=0, type=0)
        self.report = self.make_report(self.school, status=-1, remark="驳回：信息有误")
        self.people = {}
        self.links = {}
        for name, card, position, ptype in (
            ("张三", card_for("rej-zhang"), 0, 0),
            ("李四", card_for("rej-li"), 2, 1),
            ("王五", card_for("rej-wang"), 4, 1),
        ):
            person = Person.objects.create(name=name, card=card, user_id=self.school.id)
            link = ReportPerson.objects.create(report_id=self.report.id,
                                               person_id=person.id,
                                               position=position, type=ptype)
            self.people[name] = person
            self.links[name] = link

    def payload(self, **overrides):
        payload = {
            "id": self.report.id, "choir_name": "驳回编辑团队", "name": "驳回编辑节目",
            "group": "大学组", "establishment": "管乐团", "contact_name": "联系人",
            "contact_phone": "13800000000", "time_length": 120,
        }
        payload.update(overrides)
        return payload

    def echo_item(self, name, card=None, with_person_id=True):
        """按 report_dict 详情回显的人员项形状构造：外层 id=关联行 id，
        person_id=Person id；card 缺省用当前库里的值。"""
        person = self.people[name]
        item = {"id": self.links[name].id, "name": name,
                "card": card or person.card,
                "position": self.links[name].position, "type": self.links[name].type}
        if with_person_id:
            item["person_id"] = person.id
        return item

    def test_echo_shape_updates_in_place_and_deletes(self):
        # 详情回显形整单提交：改张三身份证后 6 位 + 删王五
        payload = self.payload(person=[
            self.echo_item("张三", card=card_for("rej-zhang-new")),
            self.echo_item("李四"),
        ])

        result = self.json_request("put", "/api/committee/report/update", payload)

        self.assertEqual(result.json()["code"], 0)
        zhang = self.people["张三"]
        zhang.refresh_from_db()
        self.assertEqual(zhang.card, card_for("rej-zhang-new"))  # 原地更新，行 id 不变
        self.assertFalse(Person.objects.filter(name="王五").exists())   # 删除落地
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 0)              # 驳回态随重提复位
        active = ReportPerson.objects.filter(report_id=self.report.id)
        self.assertEqual({link.person_id for link in active},
                         {zhang.id, self.people["李四"].id})

    def test_link_id_only_rejects_whole_update(self):
        # 只传关联行 id（漏 person_id）：整单拦截、数据库分毫未改 —— 宁可报错
        # 也不能静默更新到错误的人或把人员当新建吞掉
        payload = self.payload(person=[
            self.echo_item("张三", card=card_for("rej-zhang-new"), with_person_id=False),
            self.echo_item("李四", with_person_id=False),
        ])

        result = self.json_request("put", "/api/committee/report/update", payload)

        self.assertEqual(result.json()["code"], 1)
        self.assertIn("人员不存在", result.json()["msg"])
        zhang = self.people["张三"]
        zhang.refresh_from_db()
        self.assertEqual(zhang.card, card_for("rej-zhang"))   # 未被改写
        self.assertTrue(Person.objects.filter(name="王五").exists())    # 删除未发生
        self.assertEqual(self.report.status, -1)             # 连状态都没动

    def test_update_without_person_ids_recreates_people(self):
        # 不带 id 的既定口径（后六位、无查重）：一律新建，不再被引用的旧行由
        # 孤儿清理回收。行 id 会变 —— 前端想保住行 id 就必须回传 Person id。
        payload = self.payload(person=[
            {"name": "张三", "card": card_for("rej-zhang"), "position": 0, "type": 0},
            {"name": "李四", "card": card_for("rej-li"), "position": 2, "type": 1},
        ])

        result = self.json_request("put", "/api/committee/report/update", payload)

        self.assertEqual(result.json()["code"], 0)
        self.assertFalse(Person.objects.filter(name="王五").exists())
        self.assertFalse(Person.objects.filter(pk=self.people["张三"].id).exists())
        active = ReportPerson.objects.filter(report_id=self.report.id)
        self.assertEqual({Person.objects.get(pk=l.person_id).name for l in active},
                         {"张三", "李四"})

    def test_full_id_card_is_rejected_wholesale(self):
        """18 位全号必须被**整批拒绝**，绝不自动截成后 6 位。

        身份证后 6 位改造的冻结规则：写入路径只认 `^[0-9]{5}[0-9Xx]$`。
        `110101199001011234` 若被静默取成 `110101`，那是前 6 位（地区码）——
        同区所有人的前 6 位都一样，入库后会变成一条看着合法、实则无法与真后 6 位
        区分的值，正是本次改造要根除的错配来源。
        """
        payload = self.payload(person=[
            self.echo_item("张三", card="110101199001011234"),
            self.echo_item("李四"),
        ])

        result = self.json_request("put", "/api/committee/report/update", payload)

        self.assertEqual(result.json()["code"], 1)
        self.assertIn("身份证后6位", result.json()["msg"])
        zhang = self.people["张三"]
        zhang.refresh_from_db()
        self.assertEqual(zhang.card, card_for("rej-zhang"))   # 未被改写
        self.assertNotEqual(zhang.card, "110101")             # 尤其不能变成前 6 位
        self.assertTrue(Person.objects.filter(name="王五").exists())   # 整单未落库
        self.assertEqual(self.report.status, -1)


@override_settings(
    PERSON_HEAD_ALLOWED_DOMAINS=["avatars.example.com"],
    PERSON_HEAD_CDN_DOMAINS=[".cdn.example.com"],
    QINIU_DOMAIN="",
    ALIYUN_OSS_HOST="",
    ALIYUN_OSS_BUCKET="",
    ALIYUN_OSS_ENDPOINT="",
)
class PersonHeadAndIdentityTests(ApiTestCase):
    def setUp(self):
        self.school = self.create_user("head-school", 0)
        self.other_school = self.create_user("other-school", 0)
        self.admin = self.create_user("head-admin", 3)
        self.authorize_as(self.school)

    def report_payload(self, **overrides):
        payload = {
            "choir_name": "头像测试团队",
            "name": "头像测试节目",
            "group": "大学组",
            "establishment": "管乐团",
            "contact_name": "联系人",
            "contact_phone": "13800000000",
            "time_length": 120,
            "person": [],
        }
        payload.update(overrides)
        return payload

    def test_head_validator_accepts_empty_and_configured_oss_cdn_urls(self):
        for value in (None, "", "   ", "http://avatars.example.com/a.png",
                      "https://avatars.example.com:8443/a.png",
                      "https://nested.cdn.example.com/a.png"):
            with self.subTest(value=value):
                self.assertTrue(valid_person_head(value))

    def test_head_validator_rejects_unconfigured_or_non_http_urls(self):
        for value in ("ftp://avatars.example.com/a.png", "https://example.com/a.png",
                      "/relative/avatar.png", "https:///missing-host.png", 123):
            with self.subTest(value=value):
                self.assertFalse(valid_person_head(value))

    def test_report_create_rejects_invalid_head_without_creating_records(self):
        result = self.json_request("post", "/api/school/report/create", self.report_payload(
            person=[{"name": "成员", "card": card_for("invalid-head"), "head": "https://example.com/a.png"}]
        ))

        self.assertEqual(result.json()["code"], 1)
        self.assertFalse(Report.objects.exists())
        self.assertFalse(Person.objects.filter(card=card_for("invalid-head")).exists())

    def test_same_card_without_person_id_creates_a_new_person(self):
        """【2026-09-23 口径】card 不再标识身份：没带 person_id 就必须新建一行。

        改前的同名同 card 会复用/覆盖他校那一行（并因 user_id 不符而报
        「该身份证已被使用」）。现在 card 只剩后 6 位、本来就无法标识个人，
        所以这里**既不该复用、也不该报错**，而是新建一条属于当前单位的人员，
        他校那一行一个字段都不能被动到。
        """
        other = Person.objects.create(
            name="张三", card=card_for("shared"), user_id=self.other_school.id,
            phone="other-phone",
        )
        result = self.json_request("post", "/api/school/report/create", self.report_payload(
            person=[{
                "name": "张三", "card": card_for("shared"), "user_id": self.school.id,
                "phone": "new-phone", "head": "https://avatars.example.com/a.png",
                "position": 0, "type": 0,
            }]
        ))

        self.assertEqual(result.json()["code"], 0)
        # 他校那一行原封不动
        other.refresh_from_db()
        self.assertEqual(other.user_id, self.other_school.id)
        self.assertEqual(other.phone, "other-phone")
        self.assertIsNone(other.head)
        # 本次报名新建了自己单位的一行
        created = Person.objects.exclude(pk=other.pk).get()
        self.assertEqual(created.name, "张三")
        self.assertEqual(created.card, card_for("shared"))
        self.assertEqual(created.user_id, self.school.id)   # 归属由服务端决定
        self.assertEqual(created.phone, "new-phone")
        link = ReportPerson.objects.filter(report_id__isnull=False).first()
        self.assertEqual(link.person_id, created.id)

    def test_client_cannot_reassign_person_owner_via_user_id(self):
        """`user_id` 不接受客户端写入 —— 否则等于把别人的 Person 过户到自己名下。"""
        other = Person.objects.create(
            name="李四", card=card_for("owned"), user_id=self.other_school.id
        )
        result = self.json_request("post", "/api/school/report/create", self.report_payload(
            person=[{"name": "李四", "card": card_for("owned"),
                     "user_id": self.school.id, "position": 0, "type": 0}]
        ))

        self.assertEqual(result.json()["code"], 0)
        other.refresh_from_db()
        self.assertEqual(other.user_id, self.other_school.id)

    def test_person_id_of_another_unit_is_rejected_explicitly(self):
        """显式 person_id 指向他校人员时必须明确报错，不许静默新建、也不许改归属。"""
        other = Person.objects.create(
            name="王五", card=card_for("foreign"), user_id=self.other_school.id
        )
        result = self.json_request("post", "/api/school/report/create", self.report_payload(
            person=[{"person_id": other.id, "name": "王五",
                     "card": card_for("foreign"), "position": 0, "type": 0}]
        ))

        self.assertEqual(result.json()["code"], 1)
        self.assertEqual(result.json()["msg"], "人员不属于当前单位")
        self.assertFalse(Report.objects.exists())
        other.refresh_from_db()
        self.assertEqual(other.user_id, self.other_school.id)
        self.assertEqual(Person.objects.count(), 1)   # 没有悄悄多出一行

    def test_person_id_of_same_unit_is_reused(self):
        """显式 person_id 且属于本单位 → 复用同一行，不新建。"""
        mine = Person.objects.create(
            name="赵六", card=card_for("mine"), user_id=self.school.id, phone="old"
        )
        result = self.json_request("post", "/api/school/report/create", self.report_payload(
            person=[{"person_id": mine.id, "name": "赵六", "card": card_for("mine"),
                     "phone": "new", "position": 0, "type": 0}]
        ))

        self.assertEqual(result.json()["code"], 0)
        self.assertEqual(Person.objects.count(), 1)
        mine.refresh_from_db()
        self.assertEqual(mine.phone, "new")      # 档案字段按本次提交更新
        self.assertEqual(mine.user_id, self.school.id)

    def test_unknown_person_id_is_rejected_explicitly(self):
        result = self.json_request("post", "/api/school/report/create", self.report_payload(
            person=[{"person_id": 999999, "name": "查无此人",
                     "card": card_for("ghost"), "position": 0, "type": 0}]
        ))

        self.assertEqual(result.json()["code"], 1)
        self.assertIn("人员不存在", result.json()["msg"])
        self.assertFalse(Person.objects.exists())

    def test_attach_failure_rolls_back_person_created_in_same_transaction(self):
        """ReportPerson 写入失败时，同一事务里已建好的 Person 必须一起回滚。

        position 传 None 会让 report_person.position 的 NOT NULL 约束报错 ——
        这个错发生在 Person 已经写库之后，正好用来验证「不留孤儿 Person」。
        """
        with self.assertRaises(IntegrityError):
            self.json_request("post", "/api/school/report/create", self.report_payload(
                person=[{"name": "孤儿候选", "card": card_for("orphan"),
                         "position": None, "type": 0}]
            ))

        self.assertFalse(Person.objects.filter(card=card_for("orphan")).exists())
        self.assertFalse(Report.objects.exists())

    def test_admin_person_update_rejects_invalid_head(self):
        person = Person.objects.create(
            name="管理员测试成员", card=card_for("admin-head"), user_id=self.school.id,
            head="https://avatars.example.com/original.png",
        )
        self.authorize_as(self.admin)

        result = self.json_request("put", "/api/admin/person", {
            "id": person.id, "head": "https://example.com/not-allowed.png",
        })

        self.assertEqual(result.json()["code"], 1)
        person.refresh_from_db()
        self.assertEqual(person.head, "https://avatars.example.com/original.png")


class AdminCommitteeReportExtensionTests(ApiTestCase):
    """管理员/委员会查看与代改任意报名（V2 扩展的 4 条接口）。"""

    def setUp(self):
        self.school = self.create_user("school", 0)
        self.admin = self.create_user("admin", 3)
        self.committee = self.create_user("committee", 2)
        self.report = self.make_report(self.school, status=1)

    def detail_routes(self):
        return [
            (self.admin, "/api/admin/report/{}"),
            (self.committee, "/api/committee/report/{}"),
        ]

    def update_routes(self):
        return [
            (self.admin, "/api/admin/report/update"),
            (self.committee, "/api/committee/report/update"),
        ]

    def test_any_report_detail_is_readable(self):
        for user, route in self.detail_routes():
            with self.subTest(user=user.username):
                self.authorize_as(user)
                result = self.client.get(route.format(self.report.id))
                self.assertEqual(result.status_code, 200)
                payload = result.json()
                self.assertEqual(payload["code"], 0)
                self.assertEqual(payload["data"]["name"], "测试节目")
                self.assertEqual(payload["data"]["user"]["id"], self.school.id)
                self.assertEqual(payload["data"]["status"], 1)

    def test_detail_of_missing_report_returns_success_with_null_data(self):
        self.authorize_as(self.admin)

        result = self.client.get("/api/admin/report/9999")

        self.assertEqual(result.json()["code"], 0)
        self.assertIsNone(result.json()["data"])

    def test_on_behalf_update_keeps_school_ownership_and_resets_review(self):
        for user, route in self.update_routes():
            with self.subTest(user=user.username):
                report = self.make_report(self.school, status=1)
                self.authorize_as(user)
                payload = {
                    "id": report.id,
                    "choir_name": "代改团队",
                    "name": "代改节目",
                    "group": "大学组",
                    "establishment": "管乐团",
                    "contact_name": "联系人",
                    "contact_phone": "13800000000",
                    "time_length": 120,
                    "person": [{"name": "新成员", "card": card_for("onbehalf"),
                                "position": 0, "type": 0}],
                }

                result = self.json_request("put", route, payload)

                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()["code"], 0)
                report.refresh_from_db()
                # Ownership and attribution stay with the school; the edit only
                # resets the review state exactly like a school-side edit does.
                self.assertEqual(report.user_id, self.school.id)
                self.assertEqual(report.name, "代改节目")
                self.assertEqual(report.status, 0)
                link = ReportPerson.objects.filter(report_id=report.id).first()
                self.assertIsNotNone(link)
                self.assertEqual(
                    Person.objects.get(pk=link.person_id).user_id, self.school.id
                )

    def test_on_behalf_update_cannot_reassign_user_id(self):
        self.authorize_as(self.admin)

        result = self.json_request("put", "/api/admin/report/update", {
            "id": self.report.id, "name": "改名", "user_id": self.admin.id,
        })

        self.assertEqual(result.json()["code"], 0)
        self.report.refresh_from_db()
        self.assertEqual(self.report.user_id, self.school.id)

    def test_on_behalf_update_of_missing_report_fails_cleanly(self):
        self.authorize_as(self.committee)

        result = self.json_request("put", "/api/committee/report/update",
                                   {"id": 9999, "name": "不存在"})

        self.assertEqual(result.json()["code"], 1)
        self.assertEqual(result.json()["msg"], "报名表不存在！")
