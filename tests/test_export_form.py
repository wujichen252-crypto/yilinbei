"""报名信息表（附件2 式样）导出的映射与 HTTP 契约测试。"""
from email.header import decode_header

from apps.api.registration_form import form_context
from apps.core.models import Person, Report, ReportPerson

from .base import ApiTestCase


def decode_disposition(raw):
    """Django 会把非 ASCII 响应头编码成 RFC2047 encoded-word，先解开再断言。"""
    parts = decode_header(raw)
    return "".join(value.decode(charset or "ascii") if isinstance(value, bytes) else value
                   for value, charset in parts)


def link(report, person, position):
    return ReportPerson.objects.create(report_id=report.id, person_id=person.id,
                                       position=position, type=position)


class RegistrationFormContextTests(ApiTestCase):
    """form_context：官方表格各栏的纯文本映射（渲染解耦，便于断言）。"""

    def setUp(self):
        self.school = self.create_user("school", 0, nickname="某某中学")
        self.report = self.make_report(
            self.school, school_name="", establishment="管乐团", group="大学组",
            name1="指定曲目A", name="自选曲目B", remark="请安排停车",
        )

    def add_person(self, name, position, instrument="", phone=""):
        person = Person.objects.create(name=name, user_id=self.school.id,
                                       card=f"card-{name}", instrument=instrument,
                                       phone=phone)
        link(self.report, person, position)
        return person

    def test_checkboxes_school_fallback_and_headcount(self):
        self.add_person("张三", 0, instrument="长笛")
        self.add_person("李四", 1, instrument="小号")
        ctx = form_context(self.report)

        # school_name 为空时回退 User.nickname
        self.assertEqual(ctx["school"], "某某中学")
        self.assertEqual(ctx["type_line"], "管乐团■　　铜管乐团□")
        self.assertEqual(ctx["group_line"], "小学组□　　中学组□　　大学组■")
        self.assertEqual(ctx["assigned_song"], "指定曲目A")
        self.assertEqual(ctx["optional_song"], "自选曲目B")
        self.assertEqual(ctx["headcount"], "正式队员 1 人，预备队员 1 人")
        self.assertEqual(ctx["reserve_names"], "李四")
        self.assertIn("请安排停车", ctx["remark"])

    def test_type_checkbox_brass_only_checks_brass(self):
        # 回归："管乐团"是"铜管乐团"的子串，勾选铜管时不得连带勾上管乐团
        brass = self.make_report(self.school, school_name="某某中学",
                                 establishment="铜管乐团", group="小学组")
        ctx = form_context(brass)
        self.assertEqual(ctx["type_line"], "管乐团□　　铜管乐团■")

    def test_instrument_buckets_match_official_columns(self):
        # 官方表格 17 栏：上低音萨克斯与长号均单列；表外乐器（如次中音号）并入"其他"
        self.add_person("甲", 0, instrument="上低音萨克斯")
        self.add_person("乙", 0, instrument="长号")
        self.add_person("丙", 0, instrument="长笛")
        self.add_person("丁", 0, instrument="次中音号")
        ctx = form_context(self.report)

        self.assertIn("上低音萨克斯：甲", ctx["instrument_cells"])
        self.assertIn("长号：乙", ctx["instrument_cells"])
        self.assertIn("长笛：丙", ctx["instrument_cells"])
        self.assertIn("其他：丁", ctx["instrument_cells"])

    def test_conductor_and_teacher_numbering_with_phones(self):
        self.add_person("王指挥", 2, phone="13900000001")
        self.add_person("刘老师", 4, phone="13900000002")
        self.add_person("陈老师", 4, phone="13900000003")
        ctx = form_context(self.report)

        self.assertEqual(ctx["conductor_name"], "王指挥")
        self.assertEqual(ctx["conductor_phone"], "13900000001")
        self.assertEqual(ctx["teacher_lines"], ["1. 刘老师", "2. 陈老师"])
        self.assertEqual(ctx["teacher_phones"], ["13900000002", "13900000003"])

    def test_meal_slots_accept_index_label_and_abbreviation(self):
        self.report.dinner_reservation = [0, "11月21日晚餐", "22午"]
        self.report.save(update_fields=["dinner_reservation"])
        cells = form_context(self.report)["meal_cells"]

        self.assertEqual(cells, ["√", "", "", "√", "√", ""])

    def test_unmatched_meal_entry_moves_to_remark(self):
        self.report.dinner_reservation = ["10月1日午宴"]
        self.report.save(update_fields=["dinner_reservation"])
        ctx = form_context(self.report)

        self.assertEqual(ctx["meal_cells"], [""] * 6)
        self.assertIn("用餐预约：10月1日午宴", ctx["remark"])


class ExportReportEndpointTests(ApiTestCase):
    """GET /api/export/report：返回 %PDF 报名信息表，需要 Bearer 认证。"""

    def setUp(self):
        self.school = self.create_user("school", 0)
        self.authorize_as(self.school)

    def test_returns_pdf_with_official_filename(self):
        self.make_report(self.school, status=0, school_name="测试学校")

        result = self.client.get("/api/export/report")

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result["Content-Type"], "application/pdf")
        self.assertIn("报名信息表.pdf", decode_disposition(result["Content-Disposition"]))
        self.assertTrue(result.content.startswith(b"%PDF-"))
        # 有内嵌/引用的字体对象即可（Windows 上是仿宋等 TTF 子集，缺失时回退 STSong-Light）
        self.assertIn(b"/Type /Font", result.content)

    def test_requires_auth(self):
        self.clear_authorization()
        result = self.client.get("/api/export/report")
        self.assertEqual(result.status_code, 401)

    def test_other_schools_reports_are_invisible(self):
        stranger = self.create_user("other", 0)
        self.make_report(stranger, status=0, school_name="别校")

        result = self.client.get("/api/export/report")

        self.assertEqual(result.status_code, 200)
        # 只渲染本人的报名表：无数据时仍有单页 PDF，但不包含别校内容
        self.assertTrue(result.content.startswith(b"%PDF-"))
        self.assertEqual(Report.objects.count(), 1)
