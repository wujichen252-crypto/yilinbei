import json

from django.test import TestCase

from apps.core.models import Person, Report, ReportDraft, ReportPerson
from apps.core.report_drafts import (REPORT_ALLOWED_GROUPS, DraftError, InvalidSubmission,
                                     assert_group_allowed, decode_payload, encode_payload,
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

    # ----- 回归：P0-2 300 字乐团简介应能暂存和提交 -----
    def test_long_desc_up_to_1000_chars_is_accepted(self):
        payload = self.payload(desc="简" * 300)
        created = self.json_request("post", "/api/school/report/drafts", {"payload": payload})
        self.assertEqual(created.status_code, 200)
        draft_id = created.json()["data"]["draft_id"]
        submit = self.json_request(
            "post", "/api/school/report/drafts/%s/submit" % draft_id,
            {"version": 1})
        self.assertEqual(submit.status_code, 200)
        self.assertEqual(Report.objects.get().desc, "简" * 300)

    def test_desc_over_1000_chars_is_rejected(self):
        payload = self.payload(desc="简" * 1001)
        result = self.json_request("post", "/api/school/report/drafts", {"payload": payload})
        self.assertEqual(result.status_code, 400)

    # ----- 回归：P0-1 新增页不得覆盖 edit-draft 绑定的正式报名 -----
    def test_new_draft_does_not_touch_edit_draft(self):
        # 先造一份被驳回的正式报名，走 edit-draft 绑定草稿
        report = self.make_report(self.school, status=-1, name="原报名名称")
        edit = self.json_request(
            "post", "/api/school/reports/%s/edit-draft" % report.id, {})
        self.assertEqual(edit.status_code, 200)
        edit_draft_id = edit.json()["data"]["draft_id"]

        # 再从新增页 POST 一份全新内容
        result = self.json_request("post", "/api/school/report/drafts", {
            "payload": self.payload(name="全新报名")
        })
        self.assertEqual(result.status_code, 200)
        new_draft_id = result.json()["data"]["draft_id"]

        # 两条草稿必须是不同的行；原报名和 edit-draft 都不受影响
        self.assertNotEqual(edit_draft_id, new_draft_id)
        report.refresh_from_db()
        self.assertEqual(report.name, "原报名名称")
        edit_draft = ReportDraft.objects.get(pk=int(edit_draft_id))
        self.assertEqual(edit_draft.report_id, report.id)
        new_draft = ReportDraft.objects.get(pk=int(new_draft_id))
        self.assertIsNone(new_draft.report_id)

    # ----- 回归：P1-1 重复进入 edit-draft 不得冲掉用户暂存 -----
    def test_edit_draft_preserves_user_edits_on_second_entry(self):
        report = self.make_report(self.school, status=-1, name="原报名名称")
        first = self.json_request(
            "post", "/api/school/reports/%s/edit-draft" % report.id, {})
        self.assertEqual(first.status_code, 200)
        draft_id = first.json()["data"]["draft_id"]
        version = first.json()["data"]["version"]
        # 用户改了一半（PUT 更新草稿）
        self.json_request(
            "put", "/api/school/report/drafts/%s" % draft_id,
            {"version": version, "payload": self.payload(name="用户改了一半")})
        # 再点一次「修改报名」
        second = self.json_request(
            "post", "/api/school/reports/%s/edit-draft" % report.id, {})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["data"]["draft_id"], draft_id)
        self.assertEqual(second.json()["data"]["payload"]["name"], "用户改了一半")

    # ----- 回归：P0-3 每校限报一支，草稿提交路径也应拦住 -----
    def test_draft_submit_rejects_second_report_for_same_school(self):
        self.make_report(self.school, status=0)
        created = self.json_request("post", "/api/school/report/drafts", {
            "payload": self.payload()
        }).json()["data"]
        submit = self.json_request(
            "post", "/api/school/report/drafts/%s/submit" % created["draft_id"],
            {"version": created["version"]})
        self.assertEqual(submit.status_code, 409)
        self.assertEqual(submit.json().get("code"), "REPORT_QUOTA_EXCEEDED")
        self.assertEqual(Report.objects.count(), 1)

    # ----- 回归：配额的单位是「组别」不是「账号」，小学组与中学组可各报一支 -----
    # 口径依据：src/components/common/HaveToRead.vue §二段 2 的【2026-09-23 口径变更】——
    # 「每所学校每个组别限报一支，小学组、中学组可各报一支（最多两支），大学组限报一支」。
    # 修复前 assert_report_quota 只按 user_id 计数，导致报完中学组的学校再也报不了小学组
    # （用户实际遇到：提交小学组草稿被拒，msg=「每所学校限报一支队伍，您已有报名记录」）。
    def test_city_can_submit_one_report_per_group(self):
        self.make_report(self.city, status=0, group="中学组")
        self.authorize_as(self.city)

        # 已有中学组一支 → 小学组仍应提交成功（这正是用户报不上来的那条）
        created = self.json_request("post", "/api/city/report/drafts", {
            "payload": self.payload(group="小学组")
        }).json()["data"]
        first = self.json_request(
            "post", "/api/city/report/drafts/%s/submit" % created["draft_id"],
            {"version": created["version"]})
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(Report.objects.filter(user_id=self.city.id).count(), 2)

        # 但同一组别的第二支仍要被拦住（额度仍是每「组别」1，没有放开）
        again = self.json_request("post", "/api/city/report/drafts", {
            "payload": self.payload(group="小学组")
        }).json()["data"]
        second = self.json_request(
            "post", "/api/city/report/drafts/%s/submit" % again["draft_id"],
            {"version": again["version"]})
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json().get("code"), "REPORT_QUOTA_EXCEEDED")
        self.assertIn("小学组", second.json().get("msg", ""))
        self.assertEqual(Report.objects.filter(user_id=self.city.id).count(), 2)

    # ----- 回归：市级渠道不得报送大学组（渠道 × 组别的归属校验）-----
    # 口径：组委会 2026-09-23「city 渠道不能出现大学组，铜管乐团同规则」。
    #
    # 归属校验故意抛 InvalidSubmission(400) 而不是 ReportQuotaExceeded(409)。
    # 前端 normalizeDraftError 只对**已登记的错误码**单独分支，未登记的码退到按 HTTP
    # 状态兜底：409 会被判成「版本冲突」，弹的是硬编码的「该草稿已在其他页面或设备更新」
    # 外加一个解决不了这个问题的「重新加载服务器草稿」按钮；400 则落到 INVALID，
    # 走 OrchestraForm.notifyDraftError 的兜底分支原样显示 msg。所以 400 才让用户看得见原因。
    def test_city_cannot_submit_university_group(self):
        self.authorize_as(self.city)
        created = self.json_request("post", "/api/city/report/drafts", {
            "payload": self.payload(group="大学组")
        }).json()["data"]
        submit = self.json_request(
            "post", "/api/city/report/drafts/%s/submit" % created["draft_id"],
            {"version": created["version"]})
        self.assertEqual(submit.status_code, 400, submit.content)
        self.assertEqual(submit.json().get("code"), "SUBMISSION_VALIDATION_FAILED")
        # 文案必须点出是哪个组别不行，否则用户不知道该改成什么
        self.assertIn("大学组", submit.json().get("msg", ""))
        # 不得留下正式报名
        self.assertFalse(Report.objects.filter(user_id=self.city.id).exists())
        # 草稿要留在「编辑中」：整块事务回滚了，用户改完组别还能再提交，不会卡死
        draft = ReportDraft.objects.get(pk=int(created["draft_id"]))
        self.assertEqual(draft.state, ReportDraft.STATE_EDITING)
        self.assertEqual(draft.version, created["version"])

    # ----- 回归：P2-2 非对象 JSON body 应回 400 -----
    def test_non_object_json_body_returns_400(self):
        for bad_body in ('"abc"', '[1, 2, 3]', '3'):
            result = self.client.post(
                "/api/school/report/drafts",
                data=bad_body,
                content_type="application/json",
            )
            self.assertEqual(result.status_code, 400, "body=%s" % bad_body)


class GroupEligibilityTests(TestCase):
    """直接测 REPORT_ALLOWED_GROUPS / assert_group_allowed 的分支，不走 HTTP。

    上面那条 API 测试只能证明「市级 + 大学组被拦」，证不了「高校端没被顺手拦住」——
    而「高校端不加校验」是刻意的决定，必须钉住，否则将来有人补全这张表时会误伤高校端。
    """

    def test_city_rejects_university_group(self):
        for scope in (1,):
            with self.assertRaises(InvalidSubmission):
                assert_group_allowed(scope, "大学组")

    def test_city_still_allows_elementary_and_middle(self):
        assert_group_allowed(1, "小学组")   # 不抛即通过
        assert_group_allowed(1, "中学组")

    def test_unlisted_scope_is_not_restricted(self):
        # scope 0（高校端）本次明确不加校验；scope 4（省级）是西部音乐周遗留，本届无省级端。
        # 两者都不在表里 → 一律放行。新增渠道忘了配表时也是这个后果（不校验），不会误拦用户。
        assert_group_allowed(0, "小学组")
        assert_group_allowed(0, "任意组别")
        assert_group_allowed(4, "任意组别")
        self.assertNotIn(0, REPORT_ALLOWED_GROUPS)
        self.assertNotIn(4, REPORT_ALLOWED_GROUPS)

    def test_missing_group_falls_through_to_required_field_check(self):
        # 组别为空不在本函数拦 —— 必填校验归 parse_submission_payload，
        # 否则同一个错误会出现两句不同的文案。
        assert_group_allowed(1, None)
        assert_group_allowed(1, "")
