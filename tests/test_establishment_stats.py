"""首页乐团类别统计（管乐团/铜管乐团 × 审核状态）接口契约测试。"""
from .base import ApiTestCase

ROUTES = {
    1: "/api/city/index/establishment",
    2: "/api/committee/index/establishment",
    3: "/api/admin/index/establishment",
    5: "/api/primary/index/establishment",
}


def row_by_name(payload, name):
    return next(row for row in payload["data"] if row["name"] == name)


class EstablishmentStatsTests(ApiTestCase):
    def setUp(self):
        self.users = {
            1: self.create_user("city", 1),
            2: self.create_user("committee", 2),
            3: self.create_user("admin", 3),
            5: self.create_user("primary", 5),
        }
        self.city = self.users[1]
        # 市州自己的报名：管乐团驳回1 + 待审核1，铜管乐团通过1
        self.make_report(self.city, establishment="管乐团", status=-1)
        self.make_report(self.city, establishment="管乐团", status=0)
        self.make_report(self.city, establishment="铜管乐团", status=1)
        # 别家（其他市州）的报名：管乐团1 —— 不得计入市州端
        other = self.create_user("city2", 1)
        self.make_report(other, establishment="管乐团", status=1)
        # 脏值 establishment 不落入任何一行
        self.make_report(self.city, establishment=" ", status=1)

    def test_admin_counts_all_reports_by_establishment_and_status(self):
        self.authorize_as(self.users[3])
        payload = self.client.get(ROUTES[3]).json()["data"]

        band = row_by_name(payload, "管乐团")
        self.assertEqual([band["total"], band["data1"], band["data2"], band["data3"]],
                         [3, 1, 1, 1])  # 全量：含两个市州的报名
        brass = row_by_name(payload, "铜管乐团")
        self.assertEqual([brass["total"], brass["data1"], brass["data2"], brass["data3"]],
                         [1, 0, 0, 1])
        self.assertEqual(len(payload["data"]), 2)

    def test_committee_sees_same_totals_as_admin(self):
        self.authorize_as(self.users[2])
        payload = self.client.get(ROUTES[2]).json()["data"]

        self.assertEqual(row_by_name(payload, "管乐团")["total"], 3)

    def test_city_scope_is_own_reports_only(self):
        self.authorize_as(self.city)
        payload = self.client.get(ROUTES[1]).json()["data"]

        band = row_by_name(payload, "管乐团")
        self.assertEqual([band["total"], band["data1"], band["data2"], band["data3"]],
                         [2, 1, 1, 0])  # 不含 city2 的通过报名
        self.assertEqual(row_by_name(payload, "铜管乐团")["total"], 1)

    def test_primary_mirrors_city_scope(self):
        # 中小学端完整镜像市州端面板：同样只统计本账号报送的报名
        self.make_report(self.users[5], establishment="管乐团", status=1)
        self.make_report(self.users[5], establishment="铜管乐团", status=0)
        self.authorize_as(self.users[5])
        payload = self.client.get(ROUTES[5]).json()["data"]

        band = row_by_name(payload, "管乐团")
        self.assertEqual([band["total"], band["data1"], band["data2"], band["data3"]],
                         [1, 0, 0, 1])
        self.assertEqual(row_by_name(payload, "铜管乐团")["total"], 1)

    def test_role_gate_matches_other_dashboards(self):
        school = self.create_user("school", 0)
        self.authorize_as(school)
        for route in ROUTES.values():
            result = self.client.get(route)
            self.assertEqual(result.status_code, 403)
            self.assertEqual(result.json()["code"], "FORBIDDEN")
