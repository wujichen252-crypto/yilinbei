"""0007 指挥/指导老师规则的接口层表现：IntegrityError 转为 code=1 的友好提示。

规则本体在迁移 0007（部分唯一索引 + 触发器），这里只验证 API 契约：
create/update/草稿提交三条写入路径的报错文案，以及合法提交与回滚行为不受影响。
"""
from apps.core.models import Report, ReportDraft

from .base import ApiTestCase


class ReportRuleFriendlyErrorTests(ApiTestCase):
    def setUp(self):
        self.school = self.create_user("rule-school", 0)
        self.authorize_as(self.school)

    def payload(self, group="大学组", people=None, **overrides):
        value = {
            "choir_name": "规则测试团队",
            "name": "规则测试节目",
            "group": group,
            "establishment": "管乐团",
            "contact_name": "联系人",
            "contact_phone": "13800000000",
            "time_length": 120,
            "dinner_reservation": [],
            "person": people if people is not None else [],
        }
        value.update(overrides)
        return value

    def test_valid_submission_still_succeeds(self):
        payload = self.payload(people=[
            {"name": "王指挥", "card": "rule-card-1", "position": 2, "type": 1},
            {"name": "刘老师", "card": "rule-card-2", "position": 4, "type": 1},
        ])
        result = self.json_request("post", "/api/school/report/create", payload)

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["code"], 0)
        self.assertEqual(Report.objects.count(), 1)

    def test_teacher_conductor_with_two_instructors_is_rejected_with_message(self):
        payload = self.payload(people=[
            {"name": "王指挥", "card": "rule-card-1", "position": 2, "type": 1},
            {"name": "刘老师", "card": "rule-card-2", "position": 4, "type": 1},
            {"name": "陈老师", "card": "rule-card-3", "position": 4, "type": 1},
        ])
        result = self.json_request("post", "/api/school/report/create", payload)

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["code"], 1)
        self.assertEqual(result.json()["msg"], "指挥是教师时，指导老师最多 1 人")
        self.assertFalse(Report.objects.exists())

    def test_primary_school_student_conductor_is_rejected_with_message(self):
        payload = self.payload(group="小学组", people=[
            {"name": "学生指挥", "card": "rule-card-1", "position": 2, "type": 0},
            {"name": "刘老师", "card": "rule-card-2", "position": 4, "type": 1},
        ])
        result = self.json_request("post", "/api/school/report/create", payload)

        self.assertEqual(result.json()["code"], 1)
        self.assertEqual(result.json()["msg"], "中小学组别只能由教师担任指挥")

    def test_double_conductor_maps_the_unique_index_to_a_message(self):
        payload = self.payload(people=[
            {"name": "甲指挥", "card": "rule-card-1", "position": 2, "type": 1},
            {"name": "乙指挥", "card": "rule-card-2", "position": 2, "type": 0},
            {"name": "刘老师", "card": "rule-card-3", "position": 4, "type": 1},
        ])
        result = self.json_request("post", "/api/school/report/create", payload)

        self.assertEqual(result.json()["code"], 1)
        self.assertEqual(result.json()["msg"], "每张报名表只能有 1 名指挥")

    def test_failed_update_returns_message_and_keeps_report_untouched(self):
        report = self.make_report(self.school, status=1)
        payload = self.payload(
            id=report.id, name="不应保存的新名字", people=[
                {"name": "学生指挥", "card": "rule-card-1", "position": 2, "type": 0},
                {"name": "刘老师", "card": "rule-card-2", "position": 4, "type": 0},
                {"name": "陈老师", "card": "rule-card-3", "position": 4, "type": 0},
                {"name": "赵老师", "card": "rule-card-4", "position": 4, "type": 0},
            ])
        result = self.json_request("put", "/api/school/report/update", payload)

        self.assertEqual(result.json()["code"], 1)
        self.assertEqual(result.json()["msg"], "指挥是学生时，指导老师最多 2 人")
        report.refresh_from_db()
        self.assertEqual(report.name, "测试节目")  # make_report 默认名，未被改写
        self.assertEqual(report.status, 1)         # 重置为 0 的改动也随事务回滚

    def test_draft_submit_returns_draft_error_with_message(self):
        created = self.json_request("post", "/api/school/report/drafts", {
            "payload": self.payload(people=[
                {"name": "学生指挥", "card": "rule-card-1", "position": 2, "type": 0},
                {"name": "刘老师", "card": "rule-card-2", "position": 4, "type": 0},
                {"name": "陈老师", "card": "rule-card-3", "position": 4, "type": 0},
                {"name": "赵老师", "card": "rule-card-4", "position": 4, "type": 0},
            ])
        }).json()["data"]
        result = self.json_request(
            "post", "/api/school/report/drafts/%s/submit" % created["draft_id"],
            {"version": created["version"]})

        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["code"], "DRAFT_DATA_INTEGRITY_ERROR")
        self.assertEqual(result.json()["msg"], "指挥是学生时，指导老师最多 2 人")
        self.assertFalse(Report.objects.exists())
        self.assertEqual(ReportDraft.objects.get().state, ReportDraft.STATE_EDITING)
