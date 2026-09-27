"""can_report_twice 报名特许的端到端测试（2026-09-27 口径）。

默认：每所学校（高校端 type=0 / 中小学端 type=5）限报一支队伍、只能参加一个组别。
特许 can_report_twice=True（授予中小学合并办学的学校）：可报两支，每个组别仍限一支。
"""
from apps.core.models import Report, User

from .base import ApiTestCase


class ReportTwiceQuotaTests(ApiTestCase):
    def setUp(self):
        self.school = self.create_user("quota-school", User.TYPE_SCHOOL)
        self.merged_school = self.create_user(
            "quota-merged-school", User.TYPE_SCHOOL, can_report_twice=True)
        self.merged_primary = self.create_user(
            "quota-merged-primary", User.TYPE_PRIMARY_SECONDARY, can_report_twice=True)
        self.admin = self.create_user("quota-admin", User.TYPE_ADMIN)

    def payload(self, **overrides):
        value = {
            "choir_name": "特许团队",
            "name": "特许节目",
            "group": "大学组",
            "establishment": "管乐团",
            "contact_name": "联系人",
            "contact_phone": "13800000000",
            "time_length": 120,
            "dinner_reservation": [],
            "person": [],
        }
        value.update(overrides)
        return value

    def submit_draft(self, prefix, payload):
        created = self.json_request(
            "post", prefix + "/report/drafts", {"payload": payload}).json()["data"]
        return self.json_request(
            "post", "%s/report/drafts/%s/submit" % (prefix, created["draft_id"]),
            {"version": created["version"]})

    # ----- 高校端（scope 0）：直提交流 -----
    def test_school_direct_create_second_group_rejected_by_default(self):
        self.authorize_as(self.school)
        first = self.json_request("post", "/api/school/report/create",
                                  self.payload(group="大学组"))
        self.assertEqual(first.json()["code"], 0)
        # 换一个组别也不行 —— 默认账号总量就是 1
        second = self.json_request("post", "/api/school/report/create",
                                   self.payload(group="中学组"))
        self.assertEqual(second.json()["code"], 1)
        self.assertIn("限报一支", second.json()["msg"])
        self.assertEqual(Report.objects.filter(user_id=self.school.id).count(), 1)

    def test_merged_school_direct_create_two_groups_and_group_unique(self):
        self.authorize_as(self.merged_school)
        first = self.json_request("post", "/api/school/report/create",
                                  self.payload(group="大学组"))
        self.assertEqual(first.json()["code"], 0, first.content)

        # 总量未满但组别撞车：同组别第二支要被拦，文案点明组别
        same_group = self.json_request("post", "/api/school/report/create",
                                       self.payload(group="大学组"))
        self.assertEqual(same_group.json()["code"], 1)
        self.assertIn("大学组", same_group.json()["msg"])
        self.assertEqual(Report.objects.filter(user_id=self.merged_school.id).count(), 1)

        # 不同组别第二支放行（scope 0 不做组别白名单校验）
        second = self.json_request("post", "/api/school/report/create",
                                   self.payload(group="中学组"))
        self.assertEqual(second.json()["code"], 0, second.content)
        self.assertEqual(Report.objects.filter(user_id=self.merged_school.id).count(), 2)

        # 第三支：账号总量 2 封顶
        third = self.json_request("post", "/api/school/report/create",
                                  self.payload(group="小学组"))
        self.assertEqual(third.json()["code"], 1)
        self.assertIn("两支", third.json()["msg"])
        self.assertEqual(Report.objects.filter(user_id=self.merged_school.id).count(), 2)

    # ----- 高校端（scope 0）：草稿提交流 -----
    def test_merged_school_draft_flow_allows_two_different_groups(self):
        self.authorize_as(self.merged_school)
        first = self.submit_draft("/api/school", self.payload(group="大学组"))
        self.assertEqual(first.status_code, 200, first.content)
        second = self.submit_draft("/api/school", self.payload(group="中学组"))
        self.assertEqual(second.status_code, 200, second.content)
        third = self.submit_draft("/api/school", self.payload(group="小学组"))
        self.assertEqual(third.status_code, 409)
        self.assertEqual(third.json()["code"], "REPORT_QUOTA_EXCEEDED")
        self.assertEqual(Report.objects.filter(user_id=self.merged_school.id).count(), 2)

    # ----- 中小学端（scope 5）：特许学校同组别第二支必须被拦 -----
    def test_merged_primary_same_group_second_rejected(self):
        self.make_report(self.merged_primary, status=0, group="中学组")
        self.authorize_as(self.merged_primary)
        submit = self.submit_draft("/api/primary", self.payload(group="中学组"))
        self.assertEqual(submit.status_code, 409)
        self.assertEqual(submit.json().get("code"), "REPORT_QUOTA_EXCEEDED")
        self.assertIn("中学组", submit.json().get("msg", ""))
        self.assertEqual(Report.objects.filter(user_id=self.merged_primary.id).count(), 1)

    # ----- 驳回后重新提交不得把两支改成同一组别（update_rejected 路径的组别唯一闸）-----
    def test_merged_primary_rejected_resubmit_cannot_duplicate_other_group(self):
        rejected = self.make_report(self.merged_primary, status=-1, group="小学组", name="小学队")
        self.make_report(self.merged_primary, status=0, group="中学组", name="中学队")
        self.authorize_as(self.merged_primary)

        edit = self.json_request(
            "post", "/api/primary/reports/%s/edit-draft" % rejected.id, {}).json()["data"]
        draft_id = edit["draft_id"]
        updated = self.json_request(
            "put", "/api/primary/report/drafts/%s" % draft_id,
            {"version": edit["version"], "payload": self.payload(group="中学组")})
        self.assertEqual(updated.status_code, 200)

        submit = self.json_request(
            "post", "/api/primary/report/drafts/%s/submit" % draft_id,
            {"version": updated.json()["data"]["version"]})
        self.assertEqual(submit.status_code, 409)
        self.assertEqual(submit.json().get("code"), "REPORT_QUOTA_EXCEEDED")
        self.assertIn("中学组", submit.json().get("msg", ""))
        # 被驳回的报名保持驳回态，未被改成中学组
        rejected.refresh_from_db()
        self.assertEqual(rejected.status, -1)
        self.assertEqual(rejected.group, "小学组")
        self.assertEqual(
            Report.objects.filter(user_id=self.merged_primary.id, group="中学组").count(), 1)

    # ----- 特许字段只能由管理员授予，学校自助修改无效 -----
    def test_school_cannot_grant_flag_to_itself(self):
        self.authorize_as(self.school)
        result = self.json_request("put", "/api/user", {"can_report_twice": True})
        self.assertEqual(result.json()["code"], 0)
        self.school.refresh_from_db()
        self.assertFalse(self.school.can_report_twice)

    def test_admin_can_toggle_flag_with_various_input_shapes(self):
        self.authorize_as(self.admin)
        for raw in (True, 1, "1", "true", "on"):
            result = self.json_request("put", "/api/admin/user/", {
                "id": self.school.id, "can_report_twice": raw})
            self.assertEqual(result.json()["code"], 0, result.content)
            self.school.refresh_from_db()
            self.assertTrue(self.school.can_report_twice, raw)
        for raw in (False, 0, "0", "false", "", "随便写"):
            self.json_request("put", "/api/admin/user/", {
                "id": self.school.id, "can_report_twice": raw})
            self.school.refresh_from_db()
            self.assertFalse(self.school.can_report_twice, raw)

    def test_admin_can_create_user_with_flag(self):
        self.authorize_as(self.admin)
        result = self.json_request("post", "/api/admin/user/", {
            "username": "new-merged", "password": "pw", "nickname": "合并学校",
            "type": User.TYPE_PRIMARY_SECONDARY, "can_report_twice": 1,
        })
        self.assertEqual(result.json()["code"], 0, result.content)
        created = User.objects.get(username="new-merged")
        self.assertTrue(created.can_report_twice)

    # ----- 字段随登录与 /api/user 下发，供前端显示「再报一支」入口 -----
    def test_flag_is_returned_by_login_and_user_info(self):
        login = self.json_request("post", "/api/login", {
            "username": "quota-merged-school", "password": "pw"})
        self.assertEqual(login.json()["code"], 0)
        self.assertTrue(login.json()["data"]["user"]["can_report_twice"])

        self.authorize_as(self.school)
        info = self.client.get("/api/user")
        self.assertEqual(info.json()["code"], 0)
        self.assertIn("can_report_twice", info.json()["data"])
        self.assertFalse(info.json()["data"]["can_report_twice"])
