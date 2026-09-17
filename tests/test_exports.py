from apps.api.export_services import (
    admin_data1_rows,
    admin_data2_rows,
    report_data_rows,
    seconds_to_human,
)
from apps.core.models import Files, Person, ReportPerson

from .base import ApiTestCase


class ExportCompatibilityTests(ApiTestCase):
    def setUp(self):
        self.school = self.create_user("school", 0)

    def test_time_and_report_export_transformations_match_laravel(self):
        self.assertEqual(seconds_to_human(0), "0秒")
        self.assertEqual(seconds_to_human(61), "1分1秒")
        report = self.make_report(self.school, group="大学组", time_length=61, name1="指定曲")
        person = Person.objects.create(
            name="正式队员", user_id=self.school.id, card="export-card", instrument="长笛"
        )
        ReportPerson.objects.create(report_id=report.id, person_id=person.id, position=0, type=0)
        rows = report_data_rows([report])
        self.assertEqual(rows[0][0], "所属单位")
        self.assertEqual(rows[1][4], 2403000000 + report.id)
        self.assertEqual(rows[1][11], "1分1秒")
        self.assertEqual(rows[1][15], "正式队员")

    def test_admin_exports_keep_column_counts_and_file_urls(self):
        report = self.make_report(self.school, status=1, time_length=120)
        image = Files.objects.create(
            user_id=self.school.id, filename="image.jpg", type="image/jpeg", size=1, url="https://image"
        )
        video = Files.objects.create(
            user_id=self.school.id, filename="video.mp4", type="video/mp4", size=2, url="https://video"
        )
        report.spectrum, report.file = image.id, video.id
        report.save(update_fields=["spectrum", "file"])
        self.assertEqual(len(admin_data1_rows([report])[0]), 19)
        data2 = admin_data2_rows([report])
        self.assertEqual(len(data2[0]), 35)
        self.assertEqual(data2[1][16], "2分0秒")


class PdfExportTests(ApiTestCase):
    """HTTP-level checks for the two reportlab endpoints (first coverage there).

    The STSong-Light assertion relies on reportlab's default pageCompression=0,
    which keeps font resource names visible in the raw bytes.
    """

    def setUp(self):
        self.school = self.create_user("school", 0)
        self.authorize_as(self.school)

    def test_export_report_returns_pdf_with_cjk_font_resource(self):
        self.make_report(self.school, status=0, school_name="测试学校", name1="月亮之歌")

        result = self.client.get("/api/export/report")

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result["Content-Type"], "application/pdf")
        self.assertTrue(result.content.startswith(b"%PDF-"))  # not the CSV fallback
        self.assertIn(b"STSong-Light", result.content)

    def test_export_person_returns_pdf_and_requires_auth(self):
        report = self.make_report(self.school, status=0)
        person = Person.objects.create(
            name="正式队员", user_id=self.school.id, card="pdf-card", school="测试学校"
        )
        ReportPerson.objects.create(report_id=report.id, person_id=person.id, position=0, type=0)

        result = self.client.get("/api/export/person")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result["Content-Type"], "application/pdf")
        self.assertTrue(result.content.startswith(b"%PDF-"))
        self.assertIn(b"STSong-Light", result.content)

        self.clear_authorization()
        self.assertEqual(self.client.get("/api/export/person").status_code, 401)
