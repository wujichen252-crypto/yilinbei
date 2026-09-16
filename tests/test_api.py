import json

from django.contrib.auth.hashers import make_password
from django.test import TestCase

from apps.core.models import Person, Report, ReportPerson, Ticket, User


class LaravelCompatibilityTests(TestCase):
    def setUp(self):
        self.school = User.objects.create(username="school", password=make_password("pw"), nickname="学校", type=0)
        self.admin = User.objects.create(username="admin", password=make_password("pw"), nickname="管理员", type=3)

    def post_json(self, path, value):
        return self.client.post(path, data=json.dumps(value), content_type="application/json")

    def login(self, username="school"):
        result = self.post_json("/api/login", {"username": username, "password": "pw"})
        self.assertEqual(result.status_code, 200)
        payload = result.json()
        self.assertEqual(payload["code"], 0)
        self.client.defaults["HTTP_AUTHORIZATION"] = "Bearer " + payload["data"]["token"]

    def test_login_contract_and_role_denial(self):
        result = self.post_json("/api/login", {"username": "missing", "password": "pw"})
        self.assertEqual(result.json(), {"code": 1, "msg": "用户不存在", "data": None})
        self.login()
        result = self.client.get("/api/admin/index/total")
        self.assertEqual(result.status_code, 403)
        self.assertEqual(result.json()["error"], "无该页面操作权限！")

    def test_report_create_deduplicates_people_and_preserves_page_shape(self):
        self.login()
        report = {
            "choir_name": "测试团", "name": "测试曲目", "group": "大学组", "establishment": "管乐团",
            "contact_name": "联系人", "contact_phone": "13800000000", "time_length": 120,
            "person": [{"name": "张三", "card": "510000000000000000", "position": 0, "type": 0}],
        }
        result = self.post_json("/api/school/report/create", report)
        self.assertEqual(result.json()["code"], 0)
        self.assertEqual(Person.objects.count(), 1)
        self.assertEqual(ReportPerson.objects.count(), 1)
        result = self.client.get("/api/school/report/list")
        self.assertEqual(set(result.json()), {"data", "count", "code", "msg"})
        self.assertEqual(result.json()["count"], 1)

    def test_ticket_public_list_and_booking(self):
        Ticket.objects.create(type_name="测试场次", number=1)
        result = self.client.get("/api/ticket/list?type_name=测试场次")
        self.assertEqual(result.json()["code"], 0)
        result = self.post_json("/api/ticket/make", {"ticket_id": 1, "name": "张三", "card": "1", "phone": "2"})
        self.assertEqual(result.json()["code"], 0)
        duplicate = self.post_json("/api/ticket/make", {"ticket_id": 1, "name": "张三", "card": "1", "phone": "2"})
        self.assertEqual(duplicate.json()["code"], 1)
