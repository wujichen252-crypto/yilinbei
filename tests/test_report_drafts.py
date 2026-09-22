import json

from apps.core.models import Person, Report, ReportDraft, ReportPerson
from apps.core.report_drafts import (DraftError, decode_payload, encode_payload,
                                     normalize_draft_payload)

from .base import ApiTestCase


class DraftPayloadTests(ApiTestCase):
    def test_codec_preserves_unicode_and_rejects_non_finite_values(self):
        payload = {"name": "中文", "card": "00123"}
        self.assertEqual(decode_payload(encode_payload(payload)), payload)
        with self.assertRaises(ValueError):
            encode_payload({"value": float("nan")})
        with self.assertRaises(DraftError):
            decode_payload('{"value":NaN}')

    def test_normalizer_rejects_unknown_fields_and_loose_integers(self):
        with self.assertRaises(Exception):
            normalize_draft_payload({"unexpected": 1})
        with self.assertRaises(Exception):
            normalize_draft_payload({"time_length": True})
        with self.assertRaises(Exception):
            normalize_draft_payload({"file": "/tmp/file.pdf"})


class DraftApiTests(ApiTestCase):
    def setUp(self):
        self.school = self.create_user("draft-school", 0)
        self.other_school = self.create_user("draft-other-school", 0)
        self.city = self.create_user("draft-city", 1)
        self.authorize_as(self.school)

    def payload(self, **overrides):
        value = {
            "choir_name": "暂存团队",
            "name": "暂存节目",
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

    def test_draft_allows_incomplete_person_role_fields(self):
        payload = self.payload(person=[{"name": "暂未分类", "card": "draft-card"}])
        result = self.json_request("post", "/api/school/report/drafts", {"payload": payload})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["code"], 0)
        self.assertEqual(result.json()["data"]["version"], 1)
        data = result.json()["data"]
        draft = ReportDraft.objects.get(pk=int(data["draft_id"]))
        self.assertEqual(draft.version, 1)
        self.assertFalse(Report.objects.exists())
        self.assertFalse(Person.objects.exists())
        self.assertFalse(ReportPerson.objects.exists())

        result = self.json_request("put", "/api/school/report/drafts/%s" % draft.id, {
            "version": 1,
            "payload": self.payload(name="第二次暂存"),
        })
        self.assertEqual(result.status_code, 200)
        draft.refresh_from_db()
        self.assertEqual(draft.version, 2)
        self.assertEqual(json.loads(draft.payload)["name"], "第二次暂存")

    def test_old_version_returns_conflict_and_other_user_cannot_read(self):
        created = self.json_request("post", "/api/school/report/drafts", {
            "payload": self.payload()
        }).json()["data"]
        path = "/api/school/report/drafts/%s" % created["draft_id"]
        current = self.json_request("put", path, {
            "version": 1, "payload": self.payload(name="current")
        })
        self.assertEqual(current.status_code, 200)
        conflict = self.json_request("put", path, {
            "version": 1, "payload": self.payload(name="stale")
        })
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["data"]["server_version"], 2)

        self.authorize_as(self.other_school)
        hidden = self.client.get(path)
        self.assertEqual(hidden.status_code, 404)

    def test_submit_is_idempotent_and_creates_one_report(self):
        created = self.json_request("post", "/api/school/report/drafts", {
            "payload": self.payload()
        }).json()["data"]
        path = "/api/school/report/drafts/%s/submit" % created["draft_id"]
        first = self.json_request("post", path, {"version": created["version"]})
        self.assertEqual(first.status_code, 200)
        report_id = first.json()["data"]["report_id"]
        self.assertEqual(Report.objects.count(), 1)

        second = self.json_request("post", path, {"version": created["version"]})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["data"]["report_id"], report_id)
        self.assertEqual(Report.objects.count(), 1)
        self.assertEqual(ReportDraft.objects.get().state, ReportDraft.STATE_SUBMITTED)

    def test_city_scope_cannot_access_school_draft(self):
        created = self.json_request("post", "/api/school/report/drafts", {
            "payload": self.payload()
        }).json()["data"]
        self.authorize_as(self.city)
        result = self.client.get("/api/city/report/drafts/%s" % created["draft_id"])
        self.assertEqual(result.status_code, 404)
