import json
from datetime import datetime
from unittest import mock

from django.test import override_settings

from .base import ApiTestCase

FAKE_CREDENTIALS = {
    "Credentials": {
        "AccessKeyId": "STS.test-id",
        "AccessKeySecret": "test-secret",
        "SecurityToken": "CAIS-test-token",
        "Expiration": "2026-09-21T17:00:00Z",
    }
}

OSS_SETTINGS = {
    "ALIYUN_OSS_ACCESS_KEY_ID": "LTAI-test",
    "ALIYUN_OSS_ACCESS_KEY_SECRET": "secret",
    "ALIYUN_OSS_BUCKET": "yilinbei-oss",
    "ALIYUN_OSS_REGION": "oss-cn-chengdu",
    "ALIYUN_OSS_ENDPOINT": "oss-cn-chengdu.aliyuncs.com",
    "ALIYUN_OSS_HOST": "",
    "ALIYUN_OSS_STS_ROLE_ARN": "acs:ram::123456:role/yilinbei-upload-role",
    "ALIYUN_OSS_STS_EXPIRE": 3600,
}


@override_settings(**OSS_SETTINGS)
class OssTokenTests(ApiTestCase):
    """POST /api/oss/token（方案 B：STS 直传）契约测试，AssumeRole 全程 mock。"""

    def setUp(self):
        self.user = self.create_user("devschool", 0)
        self.authorize_as(self.user)

    def request_token(self, **payload):
        body = {"biz": "video", "filename": "演出视频.mp4",
                "contentType": "video/mp4", "fileSize": 734003200}
        body.update(payload)
        return self.json_request("post", "/api/oss/token", body)

    def test_issues_frozen_contract_fields(self):
        with mock.patch("apps.api.views._assume_oss_role",
                        return_value=FAKE_CREDENTIALS) as assume:
            result = self.request_token()
        self.assertEqual(result.status_code, 200)
        payload = result.json()
        self.assertEqual(payload["code"], 0)
        data = payload["data"]
        self.assertEqual(set(data), {"accessKeyId", "accessKeySecret",
                                     "securityToken", "expiration", "region",
                                     "bucket", "endpoint", "host", "key"})
        self.assertEqual(data["accessKeyId"], "STS.test-id")
        self.assertEqual(data["securityToken"], "CAIS-test-token")
        self.assertEqual(data["region"], "oss-cn-chengdu")
        self.assertEqual(data["bucket"], "yilinbei-oss")
        self.assertEqual(data["endpoint"], "oss-cn-chengdu.aliyuncs.com")
        self.assertEqual(data["host"], "https://yilinbei-oss.oss-cn-chengdu.aliyuncs.com")

        today = datetime.now().strftime("%Y%m%d")
        self.assertTrue(data["key"].startswith(f"video/{today}/"),
                        "key 必须是 {biz}/{YYYYMMDD}/{uuid}.mp4 形态")
        self.assertTrue(data["key"].endswith(".mp4"))

        # 会话策略必须收敛到本次上传的前缀，且只给上传相关动作
        policy = assume.call_args[0][0]
        statement = policy["Statement"][0]
        self.assertEqual(statement["Resource"],
                         [f"acs:oss:*:*:yilinbei-oss/video/{today}/*"])
        self.assertEqual(set(statement["Action"]),
                         {"oss:PutObject", "oss:AbortMultipartUpload",
                          "oss:ListParts", "oss:ListMultipartUploads"})

    def test_rejects_unknown_biz(self):
        result = self.request_token(biz="anything")
        self.assertEqual(result.json()["code"], 1)

    def test_rejects_oversize_video(self):
        result = self.request_token(fileSize=701 * 1024 * 1024)
        payload = result.json()
        self.assertEqual(payload["code"], 1)
        self.assertIn("700MB", payload["msg"])

    def test_rejects_zero_and_garbage_size(self):
        self.assertEqual(self.request_token(fileSize=0).json()["code"], 1)
        self.assertEqual(self.request_token(fileSize="abc").json()["code"], 1)

    def test_rejects_disallowed_content_type(self):
        result = self.request_token(contentType="application/octet-stream")
        self.assertEqual(result.json()["code"], 1)

    def test_image_biz_uses_1mb_limit(self):
        with mock.patch("apps.api.views._assume_oss_role",
                        return_value=FAKE_CREDENTIALS):
            ok = self.request_token(biz="image", filename="a.png",
                                    contentType="image/png", fileSize=1024 * 1024)
            over = self.request_token(biz="image", filename="a.png",
                                      contentType="image/png", fileSize=1024 * 1024 + 1)
        self.assertEqual(ok.json()["code"], 0)
        self.assertEqual(over.json()["code"], 1)

    def test_requires_configuration(self):
        with override_settings(ALIYUN_OSS_ACCESS_KEY_ID="", ALIYUN_OSS_STS_ROLE_ARN=""):
            result = self.request_token()
        payload = result.json()
        self.assertEqual(payload["code"], 1)
        self.assertIn("未配置", payload["msg"])

    def test_assume_role_failure_returns_failure(self):
        with mock.patch("apps.api.views._assume_oss_role",
                        side_effect=Exception("sts down")):
            result = self.request_token()
        payload = result.json()
        self.assertEqual(payload["code"], 1)
        self.assertNotIn("sts down", payload["msg"])

    def test_requires_auth(self):
        self.clear_authorization()
        result = self.json_request("post", "/api/oss/token", {})
        self.assertEqual(result.status_code, 401)
