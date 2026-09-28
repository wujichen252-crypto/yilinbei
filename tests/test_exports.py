from apps.api.export_services import (
    INSTRUMENTS,
    admin_data1_rows,
    admin_data2_rows,
    report_data_rows,
    seconds_to_human,
)
from apps.core.models import Person, ReportPerson

from .base import ApiTestCase, card_for


class AdminExportAppendixAlignmentTests(ApiTestCase):
    """data1/data2 与《附件2 报名信息表》逐栏对齐：领队/指挥/双指导老师槽、
    参展人数、按乐器正式队员名单、备注、用餐预约归位；乐器统计只数正式队员。

    夹具遵守 0009 迁移的库级规则：小学组指挥必须是教师（type=1）且教师指挥时
    指导老师最多 1 人；溢出并入第 2 槽的路径只能在无指挥的报名上出现。
    指挥是教师时第一指导老师槽自动填指挥本人（adviser_instructors，附件2 口径）。

    【data2 列序（master → zyr 合并后 39 栏）】在 master 的 37 栏基础上，把 zyr 的
    两张身份证列插回各自人物列附近：
        8  指挥身份证后6位（全部指挥，按署名顺序用「、」连接）
        13 指导老师身份证后6位（全部指导老师，同上）
    两张身份证栏的单元格文本带一个前缀单引号（沿用改造前的既有写法，见
    export_services._export_card），所以断言里写 "'" + card。
    本文件的人员 card 一律用 card_for()：zyr 的 6 位口径下 "card-1" 这类占位串非法。
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
        self.cards = {}

        def person(name, instrument="", phone=""):
            card = self.cards[name] = card_for("exp-" + name)
            return Person.objects.create(
                name=name, user_id=self.school.id, card=card,
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
        # 教师指挥兼任第一指导老师槽（附件2 口径），另报的「老师一」顺延到第 2 槽
        self.assertEqual(row[6:10], ["指挥甲", "13900000002", "老师一", "13900000003"])
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
        # 两张身份证后 6 位栏（只存后 6 位 + 既有前缀单引号）。教师指挥会自动填进
        # 第 1 指导老师槽，所以指导老师栏是「指挥甲、老师一」两张卡按槽位顺序相连。
        self.assertEqual(row[8], "'" + self.cards["指挥甲"])
        self.assertEqual(row[13], "'" + self.cards["指挥甲"] + "、'" + self.cards["老师一"])
        self.assertEqual(row[9:13], ["指挥甲", "13900000002", "老师一", "13900000003"])
        self.assertEqual(row[18], "正式队员 3 人，预备队员 1 人")
        # 长笛槽只有正式队员甲乙（预备队员丁的长笛不计入）；次中音号归入其他
        flute = row[19 + INSTRUMENTS.index("长笛")]
        other = row[19 + INSTRUMENTS.index("其他")]
        self.assertEqual((flute, other), ("2", "1"))
        self.assertEqual(row[36], 3)   # 合计 = 正式队员数（保持原实现的数字单元格）
        self.assertEqual(row[37:39], ["周五到达", "11月20日午餐、11月21日晚餐"])

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

    def test_meal_counts_render_into_meal_text(self):
        # 人数格（dinner_reservation_counts）：时段名后补「（N人）」；
        # 纯勾选时段维持旧文本；同格既有勾选又有人数时人数优先
        report = self.make_report(self.school, remark="", dinner_reservation=["21晚"],
                                  dinner_reservation_counts=[12, 0, None, 8])
        row = admin_data1_rows([report])[1]
        self.assertEqual(row[17], "")
        self.assertEqual(row[18], "11月20日午餐（12人）、11月21日晚餐（8人）")
        row2 = admin_data2_rows([report])[1]
        self.assertEqual(row2[38], "11月20日午餐（12人）、11月21日晚餐（8人）")

    # --- 署名排序（report_person.signature_order）---------------------------

    def add_instructor(self, report, name, phone, signature_order=None):
        person = Person.objects.create(name=name, user_id=self.school.id,
                                       card=card_for("exp-" + name), phone=phone)
        ReportPerson.objects.create(report_id=report.id, person_id=person.id,
                                    position=4, type=0, signature_order=signature_order)
        return person

    def test_data1_orders_instructors_by_signature_order(self):
        # 署名序号决定指导老师槽位先后：老师乙=2 先提交，老师甲=1 仍占第 1 槽
        report = self.make_report(self.school, choir_name="署名排序团队", dinner_reservation=[])
        self.add_instructor(report, "老师乙", "13900000012", signature_order=2)
        self.add_instructor(report, "老师甲", "13900000011", signature_order=1)
        row = admin_data1_rows([report])[1]
        self.assertEqual(row[6:10], ["老师甲", "13900000011", "老师乙", "13900000012"])

    def test_data2_orders_instructors_by_signature_order(self):
        report = self.make_report(self.school, choir_name="署名排序团队", dinner_reservation=[])
        self.add_instructor(report, "老师乙", "13900000012", signature_order=2)
        self.add_instructor(report, "老师甲", "13900000011", signature_order=1)
        row = admin_data2_rows([report])[1]
        self.assertEqual(row[9:13], ["老师甲", "13900000011", "老师乙", "13900000012"])
        # 身份证栏与槽位同序（无指挥的报名：只有两位指导老师）
        self.assertEqual(row[13], "'" + card_for("exp-老师甲") + "、'" + card_for("exp-老师乙"))

    def test_instructor_without_signature_order_falls_after_numbered(self):
        # 未填序号的按提交顺序排在全部已填序号之后
        report = self.make_report(self.school, choir_name="署名排序团队", dinner_reservation=[])
        self.add_instructor(report, "老师丙", "13900000013")                      # 无号，先提交
        self.add_instructor(report, "老师甲", "13900000011", signature_order=1)   # 有号
        self.add_instructor(report, "老师丁", "13900000014")                      # 无号，后提交
        row = admin_data1_rows([report])[1]
        self.assertEqual(row[6], "老师甲")                 # 槽1 = 最小序号
        self.assertEqual(row[8], "老师丙、老师丁")          # 槽2 = 无号者按提交顺序

    # --- 指挥按自己的署名序号落位（不再无条件占第 1 槽）---------------------

    def add_conductor(self, report, name, phone, signature_order=None):
        person = Person.objects.create(name=name, user_id=self.school.id,
                                       card=card_for("exp-" + name), phone=phone)
        ReportPerson.objects.create(report_id=report.id, person_id=person.id,
                                    position=2, type=1, signature_order=signature_order)
        return person

    def test_teacher_conductor_with_signature_order_2_lands_second_slot(self):
        # 指挥填了序号 2：让位给序号 1 的老师，自己落第 2 槽（data1/data2 同口径）
        report = self.make_report(self.school, choir_name="指挥落位团队", dinner_reservation=[])
        self.add_conductor(report, "王指挥", "13900000021", signature_order=2)
        self.add_instructor(report, "陈老师", "13900000023", signature_order=1)
        row = admin_data1_rows([report])[1]
        self.assertEqual(row[6:10], ["陈老师", "13900000023", "王指挥", "13900000021"])
        row2 = admin_data2_rows([report])[1]
        self.assertEqual(row2[9:13], ["陈老师", "13900000023", "王指挥", "13900000021"])
        # 身份证栏也跟着署名顺序：指挥栏是他本人的卡；指导老师栏按槽位顺序相连
        self.assertEqual(row2[8], "'" + card_for("exp-王指挥"))
        self.assertEqual(row2[13], "'" + card_for("exp-陈老师") + "、'" + card_for("exp-王指挥"))

    def test_teacher_conductor_with_signature_order_1_stays_first(self):
        # 序号 1 → 第 1 槽：与旧的「教师指挥固定占第 1 槽」结果一致，依据从身份换成序号
        report = self.make_report(self.school, choir_name="指挥落位团队", dinner_reservation=[])
        self.add_conductor(report, "王指挥", "13900000021", signature_order=1)
        self.add_instructor(report, "陈老师", "13900000023", signature_order=2)
        row = admin_data1_rows([report])[1]
        self.assertEqual(row[6:10], ["王指挥", "13900000021", "陈老师", "13900000023"])

    def test_teacher_conductor_without_signature_order_still_first(self):
        # 老数据（指挥没填序号）：保持「占第 1 槽」的原口径，其余老师顺延
        report = self.make_report(self.school, choir_name="指挥落位团队", dinner_reservation=[])
        self.add_conductor(report, "王指挥", "13900000021")
        self.add_instructor(report, "陈老师", "13900000023", signature_order=1)
        row = admin_data1_rows([report])[1]
        self.assertEqual(row[6:10], ["王指挥", "13900000021", "陈老师", "13900000023"])


class ExportCompatibilityTests(ApiTestCase):
    def setUp(self):
        self.school = self.create_user("school", 0)

    def test_time_and_report_export_transformations_match_laravel(self):
        self.assertEqual(seconds_to_human(0), "0秒")
        self.assertEqual(seconds_to_human(61), "1分1秒")
        report = self.make_report(self.school, group="大学组", time_length=61, name1="指定曲")
        person = Person.objects.create(
            name="正式队员", user_id=self.school.id, card=card_for("export"), instrument="长笛"
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
        # 39 = master 的 37 栏 + zyr 的两张「身份证后6位」栏（8 与 13）
        self.assertEqual(len(data2[0]), 39)
        self.assertEqual(data2[0][8], "指挥身份证后6位")
        self.assertEqual(data2[0][13], "指导老师身份证后6位")
        # 乐器 17 栏前移一位由「曲子时长」换成「参展人数」；时长移到 data1 尾部辅助区
        self.assertEqual(data2[1][18], "正式队员 0 人，预备队员 0 人")
        self.assertEqual(admin_data1_rows([report])[1][21], "2分0秒")

    def test_admin_data2_headings_spell_out_full_instrument_names(self):
        """The data2 heading row must not regress to truncated instrument names."""

        headings = admin_data2_rows([self.make_report(self.school)])[0]

        self.assertEqual(len(headings), 39)
        # Columns 19..35 are the instrument totals, sitting just before "合计".
        self.assertEqual(headings[19:36], list(INSTRUMENTS))
        self.assertEqual(headings[36], "合计")

        # The five columns that used to ship truncated; indexes are the real
        # positions in the heading row (乐器区从 19 起，故为 19 + 乐器下标).
        for full_name in ("低音单簧管", "中音萨克斯", "次中音萨克斯",
                          "上低音萨克斯", "低音大提琴"):
            self.assertEqual(headings[19 + INSTRUMENTS.index(full_name)], full_name)

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
        self.make_report(self.school, status=1, school_name="测试学校", name1="月亮之歌")

        result = self.client.get("/api/export/report")

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result["Content-Type"], "application/pdf")
        self.assertTrue(result.content.startswith(b"%PDF-"))  # not the CSV fallback
        # 字体资源对象存在即可：报名信息表导出按 docx 优先内嵌仿宋等 TTF，
        # 字体文件缺失的环境回退 STSong-Light，两者都带 /Type /Font
        self.assertIn(b"/Type /Font", result.content)

    def test_export_person_returns_pdf_and_requires_auth(self):
        report = self.make_report(self.school, status=1)
        person = Person.objects.create(
            name="正式队员", user_id=self.school.id, card=card_for("pdf"), school="测试学校"
        )
        ReportPerson.objects.create(report_id=report.id, person_id=person.id, position=0, type=0)

        result = self.client.get("/api/export/person")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result["Content-Type"], "application/pdf")
        self.assertTrue(result.content.startswith(b"%PDF-"))
        self.assertIn(b"STSong-Light", result.content)

        self.clear_authorization()
        self.assertEqual(self.client.get("/api/export/person").status_code, 401)
