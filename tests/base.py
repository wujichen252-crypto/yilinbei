import json

from django.test import TestCase

from apps.core.models import LiveReport, PersonalAccessToken, Report, User


class ApiTestCase(TestCase):
    """Small helpers shared by the API contract tests."""

    def create_user(self, username, user_type=0, password="pw", **overrides):
        values = {
            "nickname": overrides.pop("nickname", username),
            "type": user_type,
            **overrides,
        }
        return User.objects.create_user(username, password, **values)

    def authorize_as(self, user):
        token, plain = PersonalAccessToken.issue(user)
        self.client.defaults["HTTP_AUTHORIZATION"] = "Bearer " + plain
        return token, plain

    def clear_authorization(self):
        self.client.defaults.pop("HTTP_AUTHORIZATION", None)

    def json_request(self, method, path, payload=None, **extra):
        request = getattr(self.client, method.lower())
        return request(
            path,
            data=json.dumps(payload or {}),
            content_type="application/json",
            **extra,
        )

    def make_report(self, user, **overrides):
        values = {
            "user_id": user.id,
            "choir_name": "测试团队",
            "name": "测试节目",
            "group": "大学组",
            "establishment": "管乐团",
            "contact_name": "联系人",
            "contact_phone": "13800000000",
            "time_length": 120,
        }
        values.update(overrides)
        return Report.objects.create(**values)

    def make_live_report(self, user, **overrides):
        values = {
            "user_id": user.id,
            "report_id": overrides.pop("report_id", 1),
            "name": "现场节目",
            "choir_name": "现场团队",
            "district_or_school_name": "测试学校",
            "group": "大学组",
            "contact_name": "联系人",
            "contact_phone": "13800000000",
        }
        values.update(overrides)
        return LiveReport.objects.create(**values)
