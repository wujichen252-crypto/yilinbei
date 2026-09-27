from django.test import override_settings

from apps.core.models import Person, Report, ReportPerson
from apps.core.services import valid_person_head

from .base import ApiTestCase


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
        # 身份查重已取消（后六位允许重复、不复用不拒绝）：改用「后一项身份证为空」
        # 触发整批前置校验失败，验证任何一项不合法时全批不落库
        payload = self.report_payload(
            person=[
                {"name": "先创建", "card": "new-card", "position": 0, "type": 0},
                {"name": "后创建", "card": " ", "position": 1, "type": 0},
            ]
        )

        result = self.json_request("post", "/api/school/report/create", payload)

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["code"], 1)
        self.assertFalse(Report.objects.exists())
        self.assertFalse(Person.objects.filter(card="new-card").exists())

    def test_failed_update_keeps_report_links_and_people_unchanged(self):
        report = self.make_report(self.school, name="原节目", status=1)
        old_person = Person.objects.create(
            name="原成员", card="old-card", user_id=self.school.id
        )
        old_link = ReportPerson.objects.create(
            report_id=report.id, person_id=old_person.id, position=0, type=0
        )
        # 身份查重已取消：改用「后一项身份证为空」触发失败
        payload = self.report_payload(
            id=report.id,
            name="不应保存的新节目名",
            person=[
                {"name": "临时成员", "card": "temporary-card", "position": 0, "type": 0},
                {"name": "缺身份证", "card": " ", "position": 1, "type": 0},
            ],
        )

        result = self.json_request("put", "/api/school/report/update", payload)

        self.assertEqual(result.json()["code"], 1)
        report.refresh_from_db()
        old_link.refresh_from_db()
        self.assertEqual(report.name, "原节目")
        self.assertEqual(report.status, 1)
        self.assertIsNone(old_link.deleted_at)
        self.assertFalse(Person.objects.filter(card="temporary-card").exists())

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
            name="旧成员", card="old-card", user_id=self.school.id
        )
        old_link = ReportPerson.objects.create(
            report_id=report.id, person_id=old_person.id, position=0, type=0
        )
        payload = self.report_payload(
            id=report.id,
            name="更新后的节目",
            person=[
                {"name": "新成员", "card": "new-card", "position": 2, "type": 1}
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
        self.assertEqual(Person.objects.get(pk=links[0].person_id).card, "new-card")

    def test_create_coerces_signature_order_leniently(self):
        # 直传路径宽松规整："3"→3；0/布尔归 None（不新增报错面，草稿路径才严格 400）
        payload = self.report_payload(person=[
            {"name": "老师A", "card": "sig-a", "position": 4, "type": 1, "signature_order": "3"},
            {"name": "老师B", "card": "sig-b", "position": 4, "type": 1, "signature_order": 0},
            {"name": "老师C", "card": "sig-c", "position": 4, "type": 1, "signature_order": True},
        ])

        result = self.json_request("post", "/api/school/report/create", payload)

        self.assertEqual(result.json()["code"], 0)
        cards = {p.id: p.card for p in Person.objects.all()}
        orders = {cards[link.person_id]: link.signature_order
                  for link in ReportPerson.objects.all()}
        self.assertEqual(orders, {"sig-a": 3, "sig-b": None, "sig-c": None})

    def test_create_coerces_display_order_leniently(self):
        # 直传路径宽松规整："2"→2；与 signature_order 的关键差异：0 是合法行下标必须保留
        payload = self.report_payload(person=[
            {"name": "老师A", "card": "do-a", "position": 4, "type": 1, "display_order": "2"},
            {"name": "老师B", "card": "do-b", "position": 4, "type": 1, "display_order": 0},
            {"name": "老师C", "card": "do-c", "position": 4, "type": 1, "display_order": -1},
            {"name": "老师D", "card": "do-d", "position": 4, "type": 1, "display_order": True},
        ])

        result = self.json_request("post", "/api/school/report/create", payload)

        self.assertEqual(result.json()["code"], 0)
        cards = {p.id: p.card for p in Person.objects.all()}
        orders = {cards[link.person_id]: link.display_order
                  for link in ReportPerson.objects.all()}
        self.assertEqual(orders, {"do-a": 2, "do-b": 0, "do-c": None, "do-d": None})

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
        decoy = Person.objects.create(name="占位", card="decoy-card", user_id=self.school.id)
        other = self.make_report(self.school, choir_name="占位团队")
        for _ in range(4):
            ReportPerson.objects.create(report_id=other.id, person_id=decoy.id,
                                        position=0, type=0)
        self.report = self.make_report(self.school, status=-1, remark="驳回：信息有误")
        self.people = {}
        self.links = {}
        for name, card, position, ptype in (
            ("张三", "500101200001011234", 0, 0),
            ("李四", "500101200001022345", 2, 1),
            ("王五", "500101200001023456", 4, 1),
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
        # 详情回显形整单提交：改张三身份证（18 位→后六位）+ 删王五
        payload = self.payload(person=[
            self.echo_item("张三", card="510101199001019999"),
            self.echo_item("李四"),
        ])

        result = self.json_request("put", "/api/committee/report/update", payload)

        self.assertEqual(result.json()["code"], 0)
        zhang = self.people["张三"]
        zhang.refresh_from_db()
        self.assertEqual(zhang.card, "019999")               # 原地更新，行 id 不变
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
            self.echo_item("张三", card="510101199001019999", with_person_id=False),
            self.echo_item("李四", with_person_id=False),
        ])

        result = self.json_request("put", "/api/committee/report/update", payload)

        self.assertEqual(result.json()["code"], 1)
        self.assertIn("人员不存在", result.json()["msg"])
        zhang = self.people["张三"]
        zhang.refresh_from_db()
        self.assertEqual(zhang.card, "500101200001011234")   # 未被改写
        self.assertTrue(Person.objects.filter(name="王五").exists())    # 删除未发生
        self.assertEqual(self.report.status, -1)             # 连状态都没动

    def test_update_without_person_ids_recreates_people(self):
        # 不带 id 的既定口径（后六位、无查重）：一律新建，不再被引用的旧行由
        # 孤儿清理回收。行 id 会变 —— 前端想保住行 id 就必须回传 Person id。
        payload = self.payload(person=[
            {"name": "张三", "card": "500101200001011234", "position": 0, "type": 0},
            {"name": "李四", "card": "500101200001022345", "position": 2, "type": 1},
        ])

        result = self.json_request("put", "/api/committee/report/update", payload)

        self.assertEqual(result.json()["code"], 0)
        self.assertFalse(Person.objects.filter(name="王五").exists())
        self.assertFalse(Person.objects.filter(pk=self.people["张三"].id).exists())
        active = ReportPerson.objects.filter(report_id=self.report.id)
        self.assertEqual({Person.objects.get(pk=l.person_id).name for l in active},
                         {"张三", "李四"})


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
            person=[{"name": "成员", "card": "invalid-head-card", "head": "https://example.com/a.png"}]
        ))

        self.assertEqual(result.json()["code"], 1)
        self.assertFalse(Report.objects.exists())
        self.assertFalse(Person.objects.filter(card="invalid-head-card").exists())

    def test_same_card_from_another_school_creates_independent_record(self):
        # 身份查重已取消：同卡号（后六位）被另一学校提交 → 新建独立记录，
        # 原记录（含归属学校）原样保留，不再复用/刷新
        original = Person.objects.create(
            name="身份证本人", card="reused-card", user_id=self.other_school.id,
            phone="old-phone",
        )
        result = self.json_request("post", "/api/school/report/create", self.report_payload(
            person=[{
                "name": "身份证本人", "card": "reused-card", "user_id": self.school.id,
                "phone": "new-phone", "head": "https://avatars.example.com/a.png",
                "position": 0, "type": 0,
            }]
        ))

        self.assertEqual(result.json()["code"], 0)
        original.refresh_from_db()
        self.assertEqual(original.user_id, self.other_school.id)
        self.assertEqual(original.phone, "old-phone")
        self.assertFalse(original.head)
        created = Person.objects.get(card="reused-card", user_id=self.school.id)
        self.assertNotEqual(created.id, original.id)
        self.assertEqual(created.phone, "new-phone")
        self.assertEqual(created.head, "https://avatars.example.com/a.png")

    def test_admin_person_update_rejects_invalid_head(self):
        person = Person.objects.create(
            name="管理员测试成员", card="admin-head-card", user_id=self.school.id,
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
                    "person": [{"name": "新成员", "card": "onbehalf-card",
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
