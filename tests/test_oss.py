import json
from datetime import datetime
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
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

    def test_student_photo_biz_uses_100kb_limit(self):
        """STS 链路与代理链路共用 OSS_BIZ_RULES，师生照片在这条链路上同样是 100KB。

        本链路的大小取自客户端自报的 fileSize，属既有架构（本次不修改）。
        """
        with mock.patch("apps.api.views._assume_oss_role",
                        return_value=FAKE_CREDENTIALS):
            ok = self.request_token(biz="student_photo", filename="123456.jpg",
                                    contentType="image/jpeg", fileSize=102400)
            over = self.request_token(biz="student_photo", filename="123456.jpg",
                                      contentType="image/jpeg", fileSize=102401)
        self.assertEqual(ok.json()["code"], 0)
        self.assertEqual(over.json()["code"], 1)
        self.assertIn("100KB", over.json()["msg"])
        self.assertTrue(ok.json()["data"]["key"].startswith("student_photo/"))

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


class FakeBucket:
    def __init__(self):
        self.puts = []

    def put_object(self, key, data, headers=None):
        self.puts.append((key, data, headers))


@override_settings(**OSS_SETTINGS)
class OssProxyUploadTests(ApiTestCase):
    """POST /api/oss/upload（小文件后端代理上传），oss2 全程 mock。"""

    def setUp(self):
        self.user = self.create_user("devschool", 0)
        self.authorize_as(self.user)
        self.bucket = FakeBucket()

    def post_file(self, biz="image", name="头像.png",
                  content=b"\x89PNG\r\n\x1a\nfake", content_type="image/png"):
        payload = {"biz": biz,
                   "file": SimpleUploadedFile(name, content, content_type=content_type)}
        with mock.patch("apps.api.views._oss_bucket", return_value=self.bucket):
            return self.client.post("/api/oss/upload", payload)

    def test_uploads_and_returns_url_key_pair(self):
        result = self.post_file()
        self.assertEqual(result.status_code, 200)
        payload = result.json()
        self.assertEqual(payload["code"], 0)
        data = payload["data"]
        self.assertEqual(set(data), {"url", "key", "filename", "size"})
        today = datetime.now().strftime("%Y%m%d")
        self.assertTrue(data["key"].startswith(f"image/{today}/"))
        self.assertTrue(data["key"].endswith(".png"))
        self.assertTrue(data["url"].startswith("https://yilinbei-oss.oss-cn-chengdu.aliyuncs.com/"))
        self.assertTrue(data["url"].endswith(data["key"]))
        # 服务器确实执行了 put_object，并带上浏览器声明的 MIME
        key, content, headers = self.bucket.puts[0]
        self.assertEqual(key, data["key"])
        self.assertEqual(headers, {"Content-Type": "image/png"})

    def test_video_biz_must_go_direct_upload(self):
        result = self.post_file(biz="video", name="a.mp4", content_type="video/mp4")
        self.assertEqual(result.json()["code"], 1)

    def test_rejects_unknown_biz(self):
        result = self.post_file(biz="anything")
        self.assertEqual(result.json()["code"], 1)

    def test_rejects_oversize_image(self):
        result = self.post_file(content=b"x" * (1024 * 1024 + 1))
        payload = result.json()
        self.assertEqual(payload["code"], 1)
        self.assertIn("1MB", payload["msg"])

    def test_image_still_accepts_exactly_1mb(self):
        """回归：领队/成员头像走的 image 通道仍是 1MB，不能被师生照片的改动收紧。"""
        result = self.post_file(content=b"x" * (1024 * 1024))
        self.assertEqual(result.json()["code"], 0)

    # --- 师生照片（biz=student_photo）：独立 100KB 上限 -------------------------
    # 100KB = 100 * 1024 = 102400 bytes（本项目口径，非 100 * 1000）。

    def test_student_photo_accepts_under_100kb(self):
        result = self.post_file(biz="student_photo", name="123456.jpg",
                                content=b"x" * 102399)
        self.assertEqual(result.json()["code"], 0)

    def test_student_photo_accepts_exactly_100kb(self):
        result = self.post_file(biz="student_photo", name="123456.jpg",
                                content=b"x" * 102400)
        payload = result.json()
        self.assertEqual(payload["code"], 0)
        self.assertTrue(payload["data"]["key"].startswith("student_photo/"))

    def test_student_photo_rejects_just_over_100kb(self):
        result = self.post_file(biz="student_photo", name="123456.jpg",
                                content=b"x" * 102401)
        payload = result.json()
        self.assertEqual(payload["code"], 1)
        self.assertIn("100KB", payload["msg"])

    def test_student_photo_rejects_disallowed_extension(self):
        """师生照片沿用「JPG/PNG」类型范围，.gif 仍被拒。"""
        result = self.post_file(biz="student_photo", name="123456.gif",
                                content=b"x" * 1024)
        self.assertEqual(result.json()["code"], 1)

    def test_student_photo_accepts_png(self):
        result = self.post_file(biz="student_photo", name="张三123456.png",
                                content=b"x" * 1024)
        self.assertEqual(result.json()["code"], 0)

    def test_rejects_disallowed_extension(self):
        result = self.post_file(name="evil.exe", content_type="image/png")
        payload = result.json()
        self.assertEqual(payload["code"], 1)
        self.assertIn("不支持的文件类型", payload["msg"])

    def test_missing_file_fails_cleanly(self):
        result = self.client.post("/api/oss/upload", {"biz": "image"})
        self.assertEqual(result.json()["code"], 1)

    def test_requires_configuration(self):
        with override_settings(ALIYUN_OSS_ACCESS_KEY_ID=""):
            result = self.client.post("/api/oss/upload", {"biz": "image"})
        payload = result.json()
        self.assertEqual(payload["code"], 1)
        self.assertIn("未配置", payload["msg"])

    def test_requires_auth(self):
        self.clear_authorization()
        result = self.client.post("/api/oss/upload", {"biz": "image"})
        self.assertEqual(result.status_code, 401)
