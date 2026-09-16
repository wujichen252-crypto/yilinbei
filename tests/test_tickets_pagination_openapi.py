from datetime import datetime
from unittest.mock import patch

from django.utils import timezone

from apps.core.models import Ticket, TicketSubscribe

from .base import ApiTestCase


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
        first = self.book("张三", "card-1")
        duplicate = self.book("张三", "card-1")

        self.assertEqual(first.json()["code"], 0)
        self.assertEqual(duplicate.json()["code"], 1)
        self.assertEqual(duplicate.json()["msg"], "已预约成功该场观展！")
        self.assertEqual(TicketSubscribe.objects.count(), 1)

    def test_capacity_rejects_a_different_identity_after_last_seat(self):
        self.book("张三", "card-1", remote_addr="10.0.0.1")

        full = self.book("李四", "card-2", remote_addr="10.0.0.2")

        self.assertEqual(full.json()["code"], 1)
        self.assertEqual(full.json()["msg"], "已预约满！")
        self.assertEqual(TicketSubscribe.objects.count(), 1)
        self.assertEqual(TicketSubscribe.objects.get().ip, "10.0.0.1")

    def test_public_lookup_returns_booking_and_nested_ticket(self):
        booked = self.book("张三", "card-1").json()["data"]

        message = self.client.get(f"/api/ticket/message/{booked['code']}").json()
        mine = self.client.get(
            "/api/ticket/my", {"name": "张三", "card": "card-1"}
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
