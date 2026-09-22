"""参演人员信息表 PDF 导出：严格复刻 Laravel ExportController::exportReportPerson。

基准是重构前 Laravel 7 的**实际运行结果**，不是它的设计意图。有两处 PHP 语义
必须照抄，否则输出会与旧版不一致：

1. 组别分支 `if ($item->group == 0) ... elseif ...`：`report.group` 实际存的是
   中文字符串（Laravel 迁移注释 `'节目组别 小学组 中学组 大学组'`）。PHP 7 的
   松散比较会把非数字字符串转成 0，所以 `"大学组" == 0` 为真，**第一个分支恒命中**，
   组别一律显示「中小学组」（PHP 8 下则一个分支都不命中）。基准取 PHP 7。
2. 报送代码 `(int)(('240' . $item->group) . '000000') + $item->id` 用的是**原始
   group**，不是 `getGroupNumber()` 的结果。中文字符串被 `(int)` 截成 0，所以实际
   结果是 `240 + report.id`（`/api/export/data` 用的才是 2403000000 那一套）。

这两处都是 Laravel 既有问题，本次为严格兼容而保持原行为，不做修正。
"""
import io
import re
from urllib.parse import quote
from xml.sax.saxutils import escape

from django.http import HttpResponse

TITLE = "西部学校音乐周展演活动参演人员信息采集表"
HEADINGS = ("序号", "姓名", "性别", "年龄", "学校名称", "身份", "角色", "备注")
# Laravel 的组别分支链，顺序即 if/elseif 顺序
GROUP_BRANCHES = (
    (0, "中小学组"),
    (1, "大学组"),
    (2, "中小学教师组"),
    (3, "高校教师组"),
)
POSITION_BRANCHES = (
    (0, "正式队员"),
    (1, "预备队员"),
    (2, "指挥"),
    (3, "伴奏"),
    (4, "指导教师"),
)
TYPE_BRANCHES = ((0, "学生"), (1, "教师"))

# 浏览器兼容的文件名：ASCII 回退名 + RFC 5987 的 filename*
FALLBACK_FILENAME = "export-person.pdf"

# A4 横向（297×210mm）去掉左右各 12mm 页边距后的内容宽度
CONTENT_WIDTH_MM = 273
# 8 列宽度，合计 CONTENT_WIDTH_MM
COLUMN_WIDTHS = (14, 30, 16, 16, 72, 24, 26, 75)
# 信息块列宽比例，同 Blade 的 16% / 29% / 29% / 24%
INFO_WIDTH_RATIOS = (0.16, 0.29, 0.29, 0.24)

_NUMERIC_PREFIX = re.compile(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))")


def _php_number(value):
    """PHP 把字符串当数字用时取的前导数字前缀；取不到就是 0（如 "男" -> 0）。"""
    match = _NUMERIC_PREFIX.match(str(value))
    if not match:
        return 0
    return float(match.group(1))


def _php_int(value):
    """PHP 的 `(int)` 强制转换：取前导数字前缀并向零截断，取不到就是 0。"""
    return int(_php_number(value))


def _php_loose_equals_int(value, number):
    """PHP 7 的 `$value == <int>`。

    PHP 7 在「字符串 vs 整数」比较时把字符串转成数字再比，所以
    `"大学组" == 0` 为真；PHP 8 改成了把整数转成字符串再比，结果相反。
    Laravel 7 运行在 PHP 7 上，因此按 PHP 7 复刻。
    """
    if value is None:
        return number == 0
    if isinstance(value, bool):
        return int(value) == number
    if isinstance(value, (int, float)):
        return value == number
    return _php_number(value) == number


def _switch(value, branches, default="-"):
    """PHP `switch ($value)` 的等价物（用松散比较，且 null 命中 case 0）。"""
    for number, label in branches:
        if _php_loose_equals_int(value, number):
            return label
    return default


def _dash(value):
    """Blade 的 `?? '-'`：只把 null 换成 '-'，空字符串和 0 都原样保留。"""
    return "-" if value is None else str(value)


def _gender_label(value):
    """Blade: `$person->personInfo->gender == 1 ? '女' : '男'`（null 也走「男」）。"""
    return "女" if _php_loose_equals_int(value, 1) else "男"


def group_label(raw):
    """复刻 Laravel 的组别分支链（PHP 7 下第一个分支恒命中）。"""
    for number, label in GROUP_BRANCHES:
        if _php_loose_equals_int(raw, number):
            return label
    return _dash(raw)


def export_code(report):
    """Laravel: (int)(('240' . $item->group) . '000000') + $item->id（原始 group）。"""
    raw_group = "" if report.group is None else str(report.group)
    return _php_int("240" + raw_group + "000000") + report.id


def person_export_blocks(user_id):
    """按 Laravel 的顺序取数并算出每一格要显示的文本。

    与旧版一致的取数口径：Report(user_id=调用者, status>=0) ORDER BY id ASC；
    ReportPerson 不额外排序；Person 缺失时**不丢行**（Blade 用 `?? '-'` 兜底）。
    """
    from apps.core.models import Person, Report, ReportPerson

    reports = Report.objects.filter(user_id=user_id, status__gte=0).order_by("id")
    blocks = []
    for report in reports:
        links = list(ReportPerson.objects.filter(report_id=report.id))
        people = {p.id: p for p in Person.objects.filter(
            id__in=[link.person_id for link in links if link.person_id])}
        rows = []
        for index, link in enumerate(links):
            # Blade 的 $index+1：每份报名表重新从 1 开始编号
            person = people.get(link.person_id)
            rows.append([
                index + 1,
                _dash(getattr(person, "name", None)),
                # Blade 没有 ?? 兜底，所以 Person 缺失时性别仍会输出「男」
                _gender_label(getattr(person, "gender", None)),
                _dash(getattr(person, "age", None)),
                _dash(getattr(person, "school", None)),
                _switch(link.type, TYPE_BRANCHES),
                _switch(link.position, POSITION_BRANCHES),
                _dash(getattr(person, "remark", None)),
            ])
        blocks.append({
            "group": group_label(report.group),
            "choir_name": report.choir_name,
            "name": report.name,
            "code": export_code(report),
            "rows": rows,
        })
    return blocks


def attachment_header(filename, fallback=FALLBACK_FILENAME):
    """RFC 5987 的中文文件名，整条头保持纯 ASCII。

    Django 3.2 的 `HttpResponse.__setitem__` 会把非 latin-1 的头值做 RFC 2047
    编码（`=?utf-8?b?...?=`），而浏览器不解析 Content-Disposition 里的编码字，
    中文文件名会丢，所以改用 ASCII 回退名 + `filename*=UTF-8''`。
    """
    return 'attachment; filename="%s"; filename*=UTF-8\'\'%s' % (fallback, quote(filename, safe=""))


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.styles import ParagraphStyle

    def make(name, size, alignment=TA_CENTER):
        # wordWrap="CJK"：中文没有空格，不设这个长文本会顶出单元格而不是换行
        return ParagraphStyle(name, fontName="STSong-Light", fontSize=size,
                              leading=size * 1.45, alignment=alignment,
                              wordWrap="CJK", textColor=colors.black)

    return {
        "title": make("title", 20),
        "stamp": make("stamp", 16, TA_LEFT),
        "info": make("info", 16, TA_LEFT),
        "head": make("head", 12),
        "cell": make("cell", 12),
    }


def _cell(text, style):
    """Blade 的 `{{ }}` 会做 HTML 转义，这里同样转义后再交给 reportlab。"""
    from reportlab.platypus import Paragraph
    return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)


def _info_table(block, styles):
    """组别 / 合唱团名称 / 节目名称 / 报送代码，列宽比例同 Blade 的 16:29:29:24。"""
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle

    cells = [
        _cell("组别：%s" % _dash(block["group"]), styles["info"]),
        _cell("合唱团名称：%s" % _dash(block["choir_name"]), styles["info"]),
        _cell("节目名称：%s" % _dash(block["name"]), styles["info"]),
        _cell("报送代码：%s" % _dash(block["code"]), styles["info"]),
    ]
    table = Table([cells],
                  colWidths=[CONTENT_WIDTH_MM * r * mm for r in INFO_WIDTH_RATIOS])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def _member_table(block, styles):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle

    rows = [[_cell(text, styles["head"]) for text in HEADINGS]]
    for row in block["rows"]:
        rows.append([_cell(value, styles["cell"]) for value in row])
    table = Table(rows, colWidths=[w * mm for w in COLUMN_WIDTHS], repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
        ("FONTSIZE", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def person_export_pdf(user, blocks) -> bytes:
    from reportlab.lib.units import mm
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate,
                                    Spacer)

    from apps.api.export_services import _cjk_pdf_font

    _cjk_pdf_font()
    styles = _styles()

    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=landscape(A4),
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm,
                            title=TITLE)
    # 标题与盖章行在 Blade 里位于 @foreach 之外，整份文档只出现一次
    story = [
        Paragraph(escape(TITLE), styles["title"]),
        Spacer(1, 12),
        _cell("%s（盖章）" % (getattr(user, "nickname", "") or ""), styles["stamp"]),
        Spacer(1, 14),
    ]
    for index, block in enumerate(blocks):
        story.append(_info_table(block, styles))
        story.append(Spacer(1, 6))
        story.append(_member_table(block, styles))
        if index < len(blocks) - 1:
            story.append(PageBreak())  # 每份报名表独立成页
    doc.build(story)
    return output.getvalue()


def _csv_fallback(blocks) -> bytes:
    lines = [",".join(HEADINGS)]
    for block in blocks:
        for row in block["rows"]:
            lines.append(",".join(str(value) for value in row))
    return "\n".join(lines).encode("utf-8-sig")


def person_export_response(user, blocks, filename="参演人员信息表.pdf"):
    try:
        content = person_export_pdf(user, blocks)
    except ImportError:  # reportlab 缺失时的降级，沿用 registration_form 的既有做法
        content = _csv_fallback(blocks)
    result = HttpResponse(content, content_type="application/pdf")
    result["Content-Disposition"] = attachment_header(filename)
    return result
