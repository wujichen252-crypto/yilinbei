from apps.api.export_services import (
    INSTRUMENTS,
    admin_data1_rows,
    admin_data2_rows,
    report_data_rows,
    seconds_to_human,
)
from apps.core.models import Person, ReportPerson

from .base import ApiTestCase


class AdminExportAppendixAlignmentTests(ApiTestCase):
    """data1/data2 与《附件2 报名信息表》逐栏对齐：领队/指挥/双指导老师槽、
    参展人数、按乐器正式队员名单、备注、用餐预约归位；乐器统计只数正式队员。

    夹具遵守 0007 迁移的库级规则：小学组指挥必须是教师（type=1）且教师指挥时
    指导老师最多 1 人；溢出并入第 2 槽的路径只能在无指挥的报名上出现。
    """

    def setUp(self):
        self.school = self.create_user("school", 0)
        self.report = self.make_report(
            self.school,
            school_name="参展小学", establishment="管乐团", group="小学组",
            name="自选曲", name1="指定曲", time_length=61,
            contact_name="王领队", contact_phone="13900000001",
            contact_way="成都市某路1号", desc="乐团简介", status=1,
            remark="周五到达",
            dinner_reservation=["0", "21晚"],
        )
        cards = iter(range(100))

        def person(name, instrument="", phone=""):
            return Person.objects.create(
                name=name, user_id=self.school.id, card=f"card-{next(cards)}",
                instrument=instrument, phone=phone,
            )

        def link(report, position, people, type=0):
            for p in people:
                ReportPerson.objects.create(report_id=report.id, person_id=p.id, position=position, type=type)

        link(self.report, 0, [person("甲", "长笛"), person("乙", "长笛"), person("丙", "次中音号")])
        link(self.report, 1, [person("丁", "长笛")])            # 预备队员，不计入乐器统计
        link(self.report, 2, [person("指挥甲", phone="13900000002")], type=1)   # 小学组：教师指挥
        link(self.report, 4, [person("老师一", phone="13900000003")])
        # 无指挥的大学组报名：3 名指导老师验证「其余并入第 2 槽」
        self.overflow = self.make_report(self.school, choir_name="溢出团队", dinner_reservation=[])
        link(self.overflow, 4, [person("老师二", phone="13900000004"),
                                person("老师三", phone="13900000005")])

    def test_data1_carries_every_appendix_field(self):
        row = admin_data1_rows([self.report])[1]
        self.assertEqual(row[1], "参展小学")
        self.assertEqual(row[2:6], ["王领队", "13900000001", "指挥甲", "13900000002"])
        self.assertEqual(row[6:10], ["老师一", "13900000003", "", ""])
        self.assertEqual(row[10:14], ["管乐团", "小学组", "指定曲", "自选曲"])
        self.assertEqual(row[14], "正式队员 3 人，预备队员 1 人")
        self.assertEqual(row[15], "长笛：甲、乙\n其他：丙")   # 次中音号归入其他；预备队员丁不进名单
        self.assertEqual(row[16], "丁")
        self.assertEqual(row[17:19], ["周五到达", "11月20日午餐、11月21日晚餐"])
        self.assertEqual(row[19:25], ["school", "测试团队", "1分1秒", "成都市某路1号", "乐团简介", "已通过"])

    def test_data1_merges_extra_advisers_into_second_slot(self):
        row = admin_data1_rows([self.overflow])[1]
        self.assertEqual(row[4:6], ["", ""])   # 无指挥
        self.assertEqual(row[6:10], ["老师二", "13900000004", "老师三", "13900000005"])

    def test_data2_counts_formal_members_only_per_appendix(self):
        row = admin_data2_rows([self.report])[1]
        self.assertEqual(row[4:8], ["王领队", "13900000001", "指挥甲", "13900000002"])
        self.assertEqual(row[8:12], ["老师一", "13900000003", "", ""])
        self.assertEqual(row[16], "正式队员 3 人，预备队员 1 人")
        # 长笛槽只有正式队员甲乙（预备队员丁的长笛不计入）；次中音号归入其他
        flute = row[17 + INSTRUMENTS.index("长笛")]
        other = row[17 + INSTRUMENTS.index("其他")]
        self.assertEqual((flute, other), ("2", "1"))
        self.assertEqual(row[34], 3)   # 合计 = 正式队员数（保持原实现的数字单元格）
        self.assertEqual(row[35:37], ["周五到达", "11月20日午餐、11月21日晚餐"])

    def test_empty_person_groups_render_blank_appendix_cells(self):
        report = self.make_report(self.school, remark="", dinner_reservation=[])
        row = admin_data1_rows([report])[1]
        self.assertEqual(row[4:10], ["", "", "", "", "", ""])   # 指挥/指导老师槽全空
        self.assertEqual(row[14], "正式队员 0 人，预备队员 0 人")
        self.assertEqual((row[15], row[16], row[17], row[18]), ("", "", "", ""))

    def test_unmatched_meal_entries_fall_back_into_remark(self):
        report = self.make_report(self.school, remark="", dinner_reservation=["周末加餐"])
        row = admin_data1_rows([report])[1]
        self.assertEqual(row[18], "")                              # 6 个时段无一命中
        self.assertEqual(row[17], "用餐预约：周末加餐")            # 并入备注（与报名信息表 PDF 同口径）


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

    def test_admin_export_shapes_follow_appendix_two(self):
        """data1/data2 列数与关键位对齐《附件2 报名信息表》。"""
        report = self.make_report(self.school, status=1, time_length=120)
        self.assertEqual(len(admin_data1_rows([report])[0]), 25)
        data2 = admin_data2_rows([report])
        self.assertEqual(len(data2[0]), 37)
        # 乐器 17 栏前移一位由「曲子时长」换成「参展人数」；时长移到 data1 尾部辅助区
        self.assertEqual(data2[1][16], "正式队员 0 人，预备队员 0 人")
        self.assertEqual(admin_data1_rows([report])[1][21], "2分0秒")

    def test_admin_data2_headings_spell_out_full_instrument_names(self):
        """The data2 heading row must not regress to truncated instrument names."""

        headings = admin_data2_rows([self.make_report(self.school)])[0]

        self.assertEqual(len(headings), 37)
        # Columns 17..33 are the instrument totals, sitting just before "合计".
        self.assertEqual(headings[17:34], list(INSTRUMENTS))
        self.assertEqual(headings[34], "合计")

        # The five columns that used to ship truncated; indexes are the real
        # positions in the heading row.
        for index, full_name in (
            (20, "低音单簧管"),
            (21, "中音萨克斯"),
            (22, "次中音萨克斯"),
            (23, "上低音萨克斯"),
            (32, "低音大提琴"),
        ):
            self.assertEqual(headings[index], full_name)

        # List membership is exact, so the full names above are not matches.
        for truncated in ("低音单簧", "中音萨克", "次中音萨", "上低音萨", "低音大提"):
            self.assertNotIn(truncated, headings)


class PdfExportTests(ApiTestCase):
    """HTTP-level checks for the two reportlab endpoints (first coverage there).

    The font-object assertion only requires a /Type /Font resource: the report
    form embeds Windows TTF faces when available and falls back to the
    non-embedded STSong-Light CID font elsewhere.
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
        # 字体资源对象存在即可：报名信息表导出按 docx 优先内嵌仿宋等 TTF，
        # 字体文件缺失的环境回退 STSong-Light，两者都带 /Type /Font
        self.assertIn(b"/Type /Font", result.content)

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
