from datetime import datetime
from unittest.mock import patch

from django.utils import timezone

from apps.core.models import Ticket, TicketSubscribe

from .base import ApiTestCase, card_for


class TicketCapacityTests(ApiTestCase):
    def setUp(self):
        self.ticket = Ticket.objects.create(
            type_name="测试场次", ticket_session="上午场", time="9月16日", number=1
        )

    def book(self, name, card, remote_addr="127.0.0.1"):
        with patch(
            "apps.api.views.timezone.now",
            return_value=timezone.make_aware(datetime(2026, 1, 1, 12, 0, 0)),
        ):
            return self.json_request(
                "post",
                "/api/ticket/make",
                {
                    "ticket_id": self.ticket.id,
                    "name": name,
                    "card": card,
                    "phone": "13800000000",
                },
                REMOTE_ADDR=remote_addr,
            )

    def test_duplicate_identity_is_rejected_before_capacity_check(self):
        """同名 + 同后 6 位 + 同场次 = 重复预约，在容量判断之前就被拦下。"""
        first = self.book("张三", card_for("ticket1"))
        duplicate = self.book("张三", card_for("ticket1"))

        self.assertEqual(first.json()["code"], 0)
        self.assertEqual(duplicate.json()["code"], 1)
        self.assertEqual(duplicate.json()["msg"], "已预约成功该场观展！")
        self.assertEqual(TicketSubscribe.objects.count(), 1)

    def test_same_last6_with_different_name_is_not_a_duplicate(self):
        """【2026-09-23 口径变更】card 只剩后 6 位，撞号是常态，判重必须带上姓名。

        改前按 (场次, card) 判重 —— 两个只是碰巧同后 6 位的真人，第二个会被
        直接回「已预约成功该场观展！」，而他其实从没预约过。这条测试锁住修复。
        """
        first = self.book("张三", card_for("ticket1"), remote_addr="10.0.0.1")
        other = self.book("李四", card_for("ticket1"), remote_addr="10.0.0.2")
        # 本用例的场次容量是 1，所以第二个人应当撞在容量上，而不是被判重复
        self.assertEqual(first.json()["code"], 0)
        self.assertEqual(other.json()["msg"], "已预约满！")
        self.assertEqual(TicketSubscribe.objects.count(), 1)

    def test_capacity_rejects_a_different_identity_after_last_seat(self):
        self.book("张三", card_for("ticket1"), remote_addr="10.0.0.1")

        full = self.book("李四", card_for("ticket2"), remote_addr="10.0.0.2")

        self.assertEqual(full.json()["code"], 1)
        self.assertEqual(full.json()["msg"], "已预约满！")
        self.assertEqual(TicketSubscribe.objects.count(), 1)
        self.assertEqual(TicketSubscribe.objects.get().ip, "10.0.0.1")

    def test_invalid_card_is_rejected_and_normalized_value_is_stored(self):
        """卡号必须是后 6 位；末位小写 x 归一成大写 X 后入库。"""
        bad = self.book("张三", "12345")
        self.assertEqual(bad.json()["code"], 1)
        self.assertEqual(bad.json()["msg"], "身份证后6位应为6位，前5位为数字，末位为数字或X")

        ok = self.book("张三", "12345x")
        self.assertEqual(ok.json()["code"], 0)
        self.assertEqual(TicketSubscribe.objects.get().card, "12345X")

    def test_lookup_accepts_lowercase_x(self):
        """查询同样要归一：库里存大写 X，用户输小写 x 必须能查到。"""
        self.book("张三", card_for("ticket1"))
        stored = TicketSubscribe.objects.get().card
        # 造一个末位是 X 的场景，验证大小写不敏感
        TicketSubscribe.objects.filter(pk=TicketSubscribe.objects.get().pk).update(
            card="12345X"
        )

        hit = self.client.get("/api/ticket/my", {"name": "张三", "card": "12345x"}).json()
        miss = self.client.get("/api/ticket/my", {"name": "张三", "card": "12345"}).json()

        self.assertEqual(len(hit["data"]), 1)
        self.assertEqual(hit["data"][0]["card"], "12345X")
        self.assertEqual(miss["data"], [])   # 格式不合法 → 空结果，不是 500
        self.assertEqual(len(stored), 6)

    def test_public_lookup_returns_booking_and_nested_ticket(self):
        booked = self.book("张三", card_for("ticket1")).json()["data"]

        message = self.client.get(f"/api/ticket/message/{booked['code']}").json()
        mine = self.client.get(
            "/api/ticket/my", {"name": "张三", "card": card_for("ticket1")}
        ).json()

        self.assertEqual(message["code"], 0)
        self.assertEqual(message["data"]["ticket"]["id"], self.ticket.id)
        self.assertEqual(len(mine["data"]), 1)
        self.assertEqual(mine["data"][0]["ticket"]["id"], self.ticket.id)


class PaginationAndSearchTests(ApiTestCase):
    def setUp(self):
        self.school = self.create_user("school", 0)
        self.other = self.create_user("other", 0)
        for number in range(1, 14):
            self.make_report(
                self.school,
                name=f"节目{number:02d}",
                status=number % 2,
                group="大学组" if number <= 10 else "教师组",
            )
        self.make_report(self.other, name="不应出现")
        self.authorize_as(self.school)

    def test_report_pagination_has_stable_count_scope_and_order(self):
        page = self.client.get(
            "/api/school/report/list", {"limit": 5, "page": 2}
        ).json()

        self.assertEqual(set(page), {"data", "count", "code", "msg"})
        self.assertEqual(page["count"], 13)
        self.assertEqual([item["name"] for item in page["data"]], [
            "节目06", "节目07", "节目08", "节目09", "节目10"
        ])

    def test_search_status_group_and_out_of_range_page(self):
        filtered = self.client.get(
            "/api/school/report/list",
            {"keyword": "节目1", "status": 1, "group": "教师组", "limit": 20},
        ).json()
        empty = self.client.get(
            "/api/school/report/list", {"limit": 5, "page": 99}
        ).json()

        self.assertEqual(filtered["count"], 2)
        self.assertEqual([item["name"] for item in filtered["data"]], ["节目11", "节目13"])
        self.assertEqual(empty["count"], 13)
        self.assertEqual(empty["data"], [])

    def test_invalid_pagination_values_fall_back_to_first_ten(self):
        page = self.client.get(
            "/api/school/report/list", {"limit": "bad", "page": "bad"}
        ).json()

        self.assertEqual(page["count"], 13)
        self.assertEqual(len(page["data"]), 10)
        self.assertEqual(page["data"][0]["name"], "节目01")


class OpenApiContractTests(ApiTestCase):
    def test_schema_and_docs_are_exposed_with_bearer_security(self):
        schema_response = self.client.get("/api/openapi.json")
        docs_response = self.client.get("/api/docs")

        self.assertEqual(schema_response.status_code, 200)
        self.assertEqual(docs_response.status_code, 200)
        schema = schema_response.json()
        self.assertEqual(schema["openapi"], "3.0.2")
        self.assertEqual(schema["info"]["title"], "YLB Government Program API")
        required_paths = {
            "/api/login",
            "/api/logout",
            "/api/file/create",
            "/api/scan/files",
            "/api/live/{id}",
            "/api/school/report/create",
            "/api/ticket/make",
        }
        self.assertTrue(required_paths.issubset(schema["paths"]))
        self.assertEqual(
            schema["components"]["securitySchemes"]["BearerAuth"],
            {"type": "http", "scheme": "bearer"},
        )
        self.assertEqual(
            schema["paths"]["/api/logout"]["post"]["security"],
            [{"BearerAuth": []}],
        )
        self.assertNotIn("security", schema["paths"]["/api/login"]["post"])
        self.assertNotIn("security", schema["paths"]["/api/ticket/make"]["post"])
