from apps.core.models import Crew, Files, Leader, ScanFiles

from .base import ApiTestCase


class FileAndScanApiTests(ApiTestCase):
    def setUp(self):
        self.school = self.create_user("school", 0)
        self.other = self.create_user("other", 0)
        self.admin = self.create_user("admin", 3)

    def test_file_create_forces_authenticated_owner_and_list_selects_ids(self):
        self.authorize_as(self.school)
        created = self.json_request(
            "post",
            "/api/file/create",
            {
                "user_id": self.other.id,
                "filename": "报名表.pdf",
                "type": "application/pdf",
                "size": 12.5,
                "url": "https://files.example/report.pdf",
            },
        )
        other_file = Files.objects.create(
            user_id=self.other.id,
            filename="其他.pdf",
            type="application/pdf",
            size=1,
            url="https://files.example/other.pdf",
        )

        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["code"], 0)
        file_id = created.json()["data"]["id"]
        self.assertEqual(Files.objects.get(pk=file_id).user_id, self.school.id)
        listing = self.client.get("/api/file/list", {"ids[]": [file_id]}).json()
        self.assertEqual([item["id"] for item in listing["data"]], [file_id])
        self.assertNotIn(other_file.id, [item["id"] for item in listing["data"]])

    def test_scan_upsert_ignores_supplied_user_id(self):
        self.authorize_as(self.school)
        first = self.json_request(
            "post",
            "/api/scan/cau",
            {"user_id": self.other.id, "type": 1, "files": [{"id": 10}]},
        )
        second = self.json_request(
            "post", "/api/scan/cau", {"type": 1, "files": [{"id": 11}]}
        )

        self.assertEqual(first.json()["msg"], "新建成功!")
        self.assertEqual(second.json()["msg"], "修改成功!")
        self.assertEqual(ScanFiles.objects.count(), 1)
        scan = ScanFiles.objects.get()
        self.assertEqual(scan.user_id, self.school.id)
        self.assertEqual(scan.files, [{"id": 11}])

    def test_scan_file_lookup_prevents_horizontal_access_for_regular_user(self):
        own = ScanFiles.objects.create(
            user_id=self.school.id, type=0, files=[{"id": "own"}]
        )
        other = ScanFiles.objects.create(
            user_id=self.other.id, type=0, files=[{"id": "other"}]
        )
        self.authorize_as(self.school)

        regular = self.client.get(
            "/api/scan/files", {"user_id": self.other.id, "type": 0}
        )
        self.authorize_as(self.admin)
        privileged = self.client.get(
            "/api/scan/files", {"user_id": self.other.id, "type": 0}
        )

        self.assertEqual(regular.json()["data"]["id"], own.id)
        self.assertEqual(privileged.json()["data"]["id"], other.id)


class LiveReportOwnershipTests(ApiTestCase):
    def setUp(self):
        self.owner = self.create_user("owner", 0)
        self.other = self.create_user("other", 0)
        self.own_report = self.make_live_report(self.owner, report_id=10)
        self.other_report = self.make_live_report(self.other, report_id=20, name="他人节目")
        self.other_leader = Leader.objects.create(
            live_report_id=self.other_report.id,
            name="原领队",
            gender=0,
            linkman=1,
            age=40,
            card="leader-card",
            phone="13800000000",
        )
        self.authorize_as(self.owner)

    def test_get_and_list_are_scoped_to_authenticated_owner(self):
        listing = self.client.get("/api/live/list").json()
        own = self.client.get(f"/api/live/{self.own_report.id}")
        denied = self.client.get(f"/api/live/{self.other_report.id}")

        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["data"][0]["id"], self.own_report.id)
        self.assertEqual(own.json()["code"], 0)
        self.assertEqual(denied.json()["code"], 1)
        self.assertEqual(denied.json()["data"], None)

    def test_update_cannot_change_or_replace_another_users_children(self):
        result = self.json_request(
            "put",
            "/api/live/",
            {
                "id": self.other_report.id,
                "name": "被篡改",
                "leader": [],
                "crew": [],
            },
        )

        self.assertEqual(result.json()["code"], 1)
        self.other_report.refresh_from_db()
        self.assertEqual(self.other_report.name, "他人节目")
        self.assertTrue(Leader.objects.filter(pk=self.other_leader.id).exists())

    def test_owner_update_replaces_children_and_resets_status(self):
        old_crew = Crew.objects.create(
            live_report_id=self.own_report.id, name="旧成员", card="old-card"
        )
        self.own_report.status = 1
        self.own_report.save(update_fields=["status"])

        result = self.json_request(
            "put",
            "/api/live/",
            {
                "id": self.own_report.id,
                "name": "更新现场节目",
                "leader": [
                    {
                        "name": "新领队",
                        "gender": 1,
                        "linkman": 1,
                        "age": 35,
                        "card": "new-leader",
                        "phone": "13900000000",
                    }
                ],
                "crew": [{"name": "新成员", "card": "new-crew"}],
            },
        )

        self.assertEqual(result.json()["code"], 0)
        self.own_report.refresh_from_db()
        self.assertEqual(self.own_report.name, "更新现场节目")
        self.assertEqual(self.own_report.status, 0)
        self.assertFalse(Crew.objects.filter(pk=old_crew.id).exists())
        self.assertEqual(
            list(Crew.objects.filter(live_report_id=self.own_report.id).values_list("card", flat=True)),
            ["new-crew"],
        )
        self.assertEqual(
            list(Leader.objects.filter(live_report_id=self.own_report.id).values_list("card", flat=True)),
            ["new-leader"],
        )
