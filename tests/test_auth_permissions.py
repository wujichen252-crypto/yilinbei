from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from apps.core.models import PersonalAccessToken

from .base import ApiTestCase


class AuthenticationContractTests(ApiTestCase):
    def setUp(self):
        self.school = self.create_user("school", 0, nickname="学校")

    def test_login_rejects_bad_password_without_issuing_token(self):
        result = self.json_request(
            "post", "/api/login", {"username": "school", "password": "wrong"}
        )

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"code": 1, "msg": "账号或密码错误", "data": None})
        self.assertFalse(PersonalAccessToken.objects.exists())

    def test_missing_and_invalid_bearer_tokens_are_unauthorized(self):
        missing = self.client.get("/api/file/list")
        invalid = self.client.get(
            "/api/file/list", HTTP_AUTHORIZATION="Bearer definitely-invalid"
        )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(invalid.status_code, 401)

    def test_logout_revokes_only_the_presented_token(self):
        first, first_plain = PersonalAccessToken.issue(self.school)
        second, second_plain = PersonalAccessToken.issue(self.school)
        self.client.defaults["HTTP_AUTHORIZATION"] = "Bearer " + first_plain

        result = self.client.post("/api/logout")

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["code"], 0)
        self.assertFalse(PersonalAccessToken.objects.filter(pk=first.pk).exists())
        self.assertTrue(PersonalAccessToken.objects.filter(pk=second.pk).exists())
        self.assertEqual(self.client.get("/api/file/list").status_code, 401)
        self.client.defaults["HTTP_AUTHORIZATION"] = "Bearer " + second_plain
        self.assertEqual(self.client.get("/api/file/list").status_code, 200)

    @override_settings(TOKEN_TTL_HOURS=4)
    def test_expired_token_is_rejected_and_removed(self):
        token, plain = PersonalAccessToken.issue(self.school)
        PersonalAccessToken.objects.filter(pk=token.pk).update(
            last_used_at=timezone.now() - timedelta(hours=5)
        )
        self.client.defaults["HTTP_AUTHORIZATION"] = "Bearer " + plain

        result = self.client.get("/api/file/list")

        self.assertEqual(result.status_code, 401)
        self.assertFalse(PersonalAccessToken.objects.filter(pk=token.pk).exists())


class RolePermissionTests(ApiTestCase):
    def setUp(self):
        self.users = {
            0: self.create_user("school", 0),
            1: self.create_user("city", 1),
            2: self.create_user("committee", 2),
            3: self.create_user("admin", 3),
            4: self.create_user("province", 4),
        }

    def test_each_role_can_reach_its_own_dashboard_only(self):
        routes = {
            0: "/api/school/index/total",
            1: "/api/city/index/total",
            2: "/api/committee/index/total",
            3: "/api/admin/index/total",
            4: "/api/province/index/total",
        }

        for user_type, own_route in routes.items():
            with self.subTest(user_type=user_type, route=own_route):
                self.authorize_as(self.users[user_type])
                own = self.client.get(own_route)
                self.assertEqual(own.status_code, 200)
                self.assertEqual(own.json()["code"], 0)
                other_route = routes[(user_type + 1) % len(routes)]
                denied = self.client.get(other_route)
                self.assertEqual(denied.status_code, 403)
                self.assertEqual(denied.json(), {"error": "无该页面操作权限！"})

    def test_percent_dashboards_apply_the_same_role_middleware(self):
        self.authorize_as(self.users[1])

        school = self.client.get("/api/school/index/percent")
        province = self.client.get("/api/province/index/percent")

        self.assertEqual(school.status_code, 403)
        self.assertEqual(province.status_code, 403)

    def test_profile_update_cannot_modify_another_account(self):
        attacker = self.users[0]
        victim = self.users[1]
        self.authorize_as(attacker)

        result = self.json_request(
            "put", "/api/user", {"id": victim.id, "nickname": "被篡改"}
        )

        victim.refresh_from_db()
        self.assertEqual(victim.nickname, "city")
        if result.status_code == 200:
            self.assertEqual(result.json()["code"], 1)
        else:
            self.assertEqual(result.status_code, 403)

    def test_profile_update_cannot_escalate_own_role(self):
        user = self.users[0]
        self.authorize_as(user)

        result = self.json_request(
            "put", "/api/user", {"id": user.id, "nickname": "新名称", "type": 3}
        )

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["code"], 0)
        user.refresh_from_db()
        self.assertEqual(user.nickname, "新名称")
        self.assertEqual(user.type, 0)
