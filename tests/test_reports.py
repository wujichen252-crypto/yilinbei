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
        Person.objects.create(
            name="原姓名", card="conflict-card", user_id=self.school.id
        )
        payload = self.report_payload(
            person=[
                {"name": "先创建", "card": "new-card", "position": 0, "type": 0},
                {"name": "错误姓名", "card": "conflict-card", "position": 1, "type": 0},
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
        Person.objects.create(
            name="身份证本人", card="conflict-card", user_id=self.school.id
        )
        payload = self.report_payload(
            id=report.id,
            name="不应保存的新节目名",
            person=[
                {"name": "临时成员", "card": "temporary-card", "position": 0, "type": 0},
                {"name": "姓名不符", "card": "conflict-card", "position": 1, "type": 0},
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

    def test_reused_card_keeps_original_identity_but_refreshes_optional_fields(self):
        person = Person.objects.create(
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
        person.refresh_from_db()
        self.assertEqual(person.name, "身份证本人")
        self.assertEqual(person.user_id, self.other_school.id)
        self.assertEqual(person.phone, "new-phone")
        self.assertEqual(person.head, "https://avatars.example.com/a.png")

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
