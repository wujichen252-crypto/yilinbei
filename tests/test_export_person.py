"""`/api/export/person` 的 Laravel 对齐测试。

基准是重构前 Laravel `ExportController::exportReportPerson` + `person.blade.php`
的**实际输出**。凡是与旧版不一致的行为都算回归，即使 Django 这边的写法看起来
更合理（例如「指导教师不该出现在这张表里」「性别应该按男/女读」）。

PDF 里的中文经 CID 字体输出，内容流又是 ASCII85+Flate 压缩的，所以断言 PDF 正文
必须先把流解出来再按 UTF-16BE 字节匹配（见 `_pdf_text`）。
"""
import base64
import re
import zlib

from apps.api.person_export import (
    HEADINGS,
    attachment_header,
    export_code,
    group_label,
    person_export_blocks,
)
from apps.core.models import Person, ReportPerson

from .base import ApiTestCase


def _pdf_streams(content: bytes) -> bytes:
    """解出 PDF 的全部内容流（reportlab 默认 ASCII85 + Flate）。"""
    decoded = b""
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", content, re.S):
        blob = match.group(1).rstrip(b"\r\n")
        try:
            raw = (base64.a85decode(blob, adobe=True) if blob.endswith(b"~>")
                   else base64.a85decode(blob, adobe=False, ignorechars=b" \r\n\t"))
            decoded += zlib.decompress(raw)
        except (ValueError, zlib.error):
            continue
    return decoded


def _pdf_text(content: bytes) -> bytes:
    """把内容流里的 `(...) Tj` 字面量还原成原始字节，用于中文匹配。"""
    out = bytearray()
    for literal in re.findall(rb"\((?:\\.|[^\\()])*\)\s*Tj", _pdf_streams(content)):
        body = literal[: literal.rindex(b")")]
        body = body[body.index(b"(") + 1:]
        index = 0
        while index < len(body):
            char = body[index:index + 1]
            if char != b"\\":
                out += char
                index += 1
                continue
            nxt = body[index + 1:index + 2]
            if nxt.isdigit():
                out.append(int(body[index + 1:index + 4], 8))
                index += 4
            else:
                out += {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b",
                        b"f": b"\f"}.get(nxt, nxt)
                index += 2
    return bytes(out)


class PersonExportMappingTests(ApiTestCase):
    """单元格取值的 Laravel 语义（不经过 PDF，直接断言数据结构）。"""

    def setUp(self):
        self.school = self.create_user("school", 0)

    def _row(self, person_overrides=None, position=0, person_type=0, card="c-1"):
        """建一个人 + 一张报名表，返回那个人在导出结果里的那一行。

        Report 按 id ASC 输出，所以刚建的那张一定在最后。
        """
        person = Person.objects.create(
            name="张三", user_id=self.school.id, card=card, **(person_overrides or {}))
        report = self.make_report(self.school)
        ReportPerson.objects.create(report_id=report.id, person_id=person.id,
                                    position=position, type=person_type)
        return person_export_blocks(self.school.id)[-1]["rows"][0]

    def test_type_maps_to_chinese_and_falls_back_to_dash(self):
        person = Person.objects.create(name="张三", user_id=self.school.id, card="t-1")
        report = self.make_report(self.school)
        for person_type, expected in ((0, "学生"), (1, "教师"), (2, "-"), (9, "-")):
            ReportPerson.objects.create(report_id=report.id, person_id=person.id,
                                        position=0, type=person_type)
        rows = person_export_blocks(self.school.id)[0]["rows"]
        self.assertEqual([row[5] for row in rows], ["学生", "教师", "-", "-"])
        self.assertNotIn(0, [row[5] for row in rows])

    def test_position_maps_to_chinese_and_falls_back_to_dash(self):
        person = Person.objects.create(name="张三", user_id=self.school.id, card="p-1")
        report = self.make_report(self.school)
        for position in (0, 1, 2, 3, 4, 7):
            ReportPerson.objects.create(report_id=report.id, person_id=person.id,
                                        position=position, type=0)
        rows = person_export_blocks(self.school.id)[0]["rows"]
        self.assertEqual([row[6] for row in rows],
                         ["正式队员", "预备队员", "指挥", "伴奏", "指导教师", "-"])

    def test_gender_uses_php_loose_comparison_against_one(self):
        """Blade 是 `gender == 1 ? '女' : '男'`，所以「女」这个值也输出「男」。"""
        for index, (stored, expected) in enumerate(
                (("1", "女"), (1, "女"), ("0", "男"), ("男", "男"),
                 ("女", "男"), (None, "男"), ("", "男"))):
            with self.subTest(gender=stored):
                row = self._row({"gender": stored}, card="g-%s" % index)
                self.assertEqual(row[2], expected)

    def test_missing_person_keeps_the_row_with_dash_placeholders(self):
        """Blade 用 `?? '-'` 兜底，Person 缺失时行不能消失。"""
        report = self.make_report(self.school)
        ReportPerson.objects.create(report_id=report.id, person_id=None,
                                    position=2, type=1)
        rows = person_export_blocks(self.school.id)[0]["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], [1, "-", "男", "-", "-", "教师", "指挥", "-"])

    def test_zero_values_survive_but_null_becomes_dash(self):
        """`?? '-'` 只换 null；年龄 0 必须原样输出。"""
        person = Person.objects.create(name="张三", user_id=self.school.id,
                                       card="z-1", age=0, school="", remark=None)
        report = self.make_report(self.school)
        ReportPerson.objects.create(report_id=report.id, person_id=person.id,
                                    position=0, type=0)
        row = person_export_blocks(self.school.id)[0]["rows"][0]
        self.assertEqual(row[3], "0")      # age=0 不能变成空
        self.assertEqual(row[4], "")       # 空字符串保持空，不是 '-'
        self.assertEqual(row[7], "-")      # null 才是 '-'

    def test_sequence_restarts_for_each_report(self):
        person = Person.objects.create(name="张三", user_id=self.school.id, card="s-1")
        first = self.make_report(self.school, choir_name="第一团")
        second = self.make_report(self.school, choir_name="第二团")
        for report, count in ((first, 3), (second, 2)):
            for _ in range(count):
                ReportPerson.objects.create(report_id=report.id, person_id=person.id,
                                            position=0, type=0)
        blocks = person_export_blocks(self.school.id)
        self.assertEqual([[row[0] for row in b["rows"]] for b in blocks], [[1, 2, 3], [1, 2]])

    def test_teachers_are_not_filtered_out(self):
        person = Person.objects.create(name="李老师", user_id=self.school.id, card="t-9")
        report = self.make_report(self.school)
        ReportPerson.objects.create(report_id=report.id, person_id=person.id,
                                    position=4, type=1)
        rows = person_export_blocks(self.school.id)[0]["rows"]
        self.assertEqual([row[6] for row in rows], ["指导教师"])

    def test_group_label_replicates_php7_loose_comparison(self):
        """group 存的是中文，PHP 7 下 `"大学组" == 0` 为真 → 恒命中第一个分支。"""
        for stored in ("大学组", "小学组", "中学组", "", None):
            with self.subTest(group=stored):
                self.assertEqual(group_label(stored), "中小学组")
        for stored, expected in (("0", "中小学组"), ("1", "大学组"),
                                 ("2", "中小学教师组"), ("3", "高校教师组")):
            with self.subTest(group=stored):
                self.assertEqual(group_label(stored), expected)

    def test_export_code_uses_the_raw_group(self):
        report = self.make_report(self.school, group="大学组")
        self.assertEqual(export_code(report), 240 + report.id)
        numeric = self.make_report(self.school, group="3")
        self.assertEqual(export_code(numeric), 2403000000 + numeric.id)

    def test_only_own_reports_below_status_zero_are_included(self):
        other = self.create_user("other", 0)
        person = Person.objects.create(name="张三", user_id=self.school.id, card="o-1")
        mine = self.make_report(self.school, status=0)
        self.make_report(self.school, status=-1)
        self.make_report(other, status=0)
        ReportPerson.objects.create(report_id=mine.id, person_id=person.id,
                                    position=0, type=0)
        blocks = person_export_blocks(self.school.id)
        self.assertEqual([b["code"] for b in blocks], [240 + mine.id])


class PersonExportPdfTests(ApiTestCase):
    def setUp(self):
        self.school = self.create_user("school", 0, nickname="某某中学")
        self.authorize_as(self.school)

    def _make_person(self, report, name, position, person_type=0):
        person = Person.objects.create(name=name, user_id=self.school.id,
                                       card="%s-%s" % (name, report.id),
                                       age=17, school="测试学校")
        return ReportPerson.objects.create(report_id=report.id, person_id=person.id,
                                           position=position, type=person_type)

    def test_page_is_a4_landscape(self):
        report = self.make_report(self.school)
        self._make_person(report, "张三", 0)
        content = self.client.get("/api/export/person").content
        box = re.search(rb"/MediaBox \[ 0 0 ([\d.]+) ([\d.]+) \]", content)
        self.assertIsNotNone(box, "PDF 缺少 MediaBox")
        width, height = float(box.group(1)), float(box.group(2))
        self.assertAlmostEqual(width, 841.89, places=1)   # A4 横向：宽 > 高
        self.assertAlmostEqual(height, 595.28, places=1)
        self.assertGreater(width, height)

    def test_content_disposition_exposes_the_chinese_filename(self):
        report = self.make_report(self.school)
        self._make_person(report, "张三", 0)
        disposition = self.client.get("/api/export/person")["Content-Disposition"]
        self.assertTrue(disposition.isascii(), "整条头必须是 ASCII，否则会被 MIME 编码")
        self.assertNotIn("=?", disposition, "不能出现 RFC 2047 编码字")
        self.assertIn("filename*=UTF-8''%E5%8F%82%E6%BC%94%E4%BA%BA%E5%91%98"
                      "%E4%BF%A1%E6%81%AF%E8%A1%A8.pdf", disposition)

    def test_pdf_carries_title_headings_and_report_blocks(self):
        first = self.make_report(self.school, choir_name="第一合唱团", name="曲目甲")
        second = self.make_report(self.school, choir_name="第二合唱团", name="曲目乙")
        self._make_person(first, "张三", 0)
        self._make_person(second, "李四", 4, person_type=1)

        result = self.client.get("/api/export/person")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result["Content-Type"], "application/pdf")
        self.assertTrue(result.content.startswith(b"%PDF-"))
        self.assertIn(b"STSong-Light", result.content)

        text = _pdf_text(result.content)
        for expected in (["西部学校音乐周展演活动参演人员信息采集表", "某某中学（盖章）"]
                         + list(HEADINGS)
                         + ["组别", "中小学组", "合唱团名称", "第一合唱团", "第二合唱团",
                            "节目名称", "曲目甲", "曲目乙", "报送代码",
                            "正式队员", "指导教师", "学生", "教师",
                            str(240 + first.id), str(240 + second.id)]):
            with self.subTest(expected=expected):
                self.assertIn(expected.encode("utf-16-be"), text)

        # 反向对照：证明上面的匹配不是「什么都能命中」
        self.assertNotIn("曲目丙".encode("utf-16-be"), text)
        self.assertNotIn(b" | ", text)  # 旧的纯文本实现用 " | " 拼行

    def test_each_report_starts_on_its_own_page(self):
        first = self.make_report(self.school)
        second = self.make_report(self.school)
        self._make_person(first, "张三", 0)
        self._make_person(second, "李四", 0)
        content = self.client.get("/api/export/person").content
        self.assertEqual(len(re.findall(rb"/Type /Page[^s]", content)), 2)

    def test_long_remark_is_not_truncated(self):
        report = self.make_report(self.school)
        remark = "备注很长的一段中文内容" * 12
        person = Person.objects.create(name="张三", user_id=self.school.id,
                                       card="long-1", remark=remark)
        ReportPerson.objects.create(report_id=report.id, person_id=person.id,
                                    position=0, type=0)
        text = _pdf_text(self.client.get("/api/export/person").content)
        self.assertIn(remark.encode("utf-16-be"), text)

    def test_attachment_header_shape(self):
        header = attachment_header("参演人员信息表.pdf")
        self.assertEqual(
            header,
            'attachment; filename="export-person.pdf"; '
            "filename*=UTF-8''%E5%8F%82%E6%BC%94%E4%BA%BA%E5%91%98%E4%BF%A1%E6%81%AF%E8%A1%A8.pdf")
