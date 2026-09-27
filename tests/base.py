import hashlib
import json

from django.test import TestCase

from apps.core.models import LiveReport, PersonalAccessToken, Report, User

# ---------------------------------------------------------------------------
# 身份证测试数据：2026-09-23 起 card 只接受「后 6 位」（5 位数字 + 末位数字或 X）。
#
# 旧测试里到处都是 `card="old-card"`、`card="conflict-card"` 这类占位串 —— 它们在新
# 口径下全是非法值，任何提交都会被格式校验挡下，于是**测试失败的原因与被测行为无关**。
# 所以统一换成 card_for("old") 这种写法：可读的标签 → 合法且互不相同的 6 位值。
#
# 【保证互不相同，不是靠概率】用 sha1 取模会撞（生日问题，几十个标签就有千分之几
# 的概率），而且撞了之后现象是「两个本该不同的人被当成同一个」—— 恰好是本轮改造
# 最怕的那类静默错配。所以这里维护一个进程内注册表，撞了就往后挪一位，确保唯一。
_CARD_LABELS = {}
_CARD_USED = set()


def card_for(label):
    """把测试用标签映射成合法的身份证后 6 位。同一标签恒定返回同一值。"""
    if label not in _CARD_LABELS:
        value = int(hashlib.sha1(str(label).encode("utf-8")).hexdigest(), 16) % 1000000
        while "%06d" % value in _CARD_USED:
            value = (value + 1) % 1000000
        _CARD_USED.add("%06d" % value)
        _CARD_LABELS[label] = "%06d" % value
    return _CARD_LABELS[label]


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
