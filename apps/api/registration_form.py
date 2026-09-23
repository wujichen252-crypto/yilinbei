"""报名信息表 PDF 导出：按组委会《附件2》式样逐项排版，每张报名表一页。

字体按 0921 定稿通知 docx 还原：表格 仿宋_GB2312（标签加粗）、标题 方正小标宋
22pt（本机无此字体时用华文中宋替代）、「附件2」黑体 16pt。Windows 字体文件缺失
（如 Linux 生产环境）时整体回退 STSong-Light。

数据映射沿用 /api/export/report 原有口径：ReportPerson.position 0=正式队员、
1=预备队员、2=指挥、4=指导老师（指挥是教师 type=1 时，第一指导老师槽自动填
指挥本人，见 export_services.adviser_instructors）；乐器名经 _instrument_bucket
归一到官方表格的 17 个栏目（含长号）。reportlab 缺失时降级为 CSV 文本
（与 views.pdf_response 一致）。
"""
import io
import os

from django.http import HttpResponse

from apps.api.export_services import INSTRUMENTS

# 附件2 正式队员名单的 17 个乐器栏目；与 export_services.INSTRUMENTS 同源，
# 顺序为红头文件原文。表外乐器（如"次中音号"）仍由 _instrument_bucket 归到"其他"。
FORM_INSTRUMENTS = INSTRUMENTS
MEALS = ("11月20日午餐", "11月20日晚餐", "11月21日午餐", "11月21日晚餐",
         "11月22日午餐", "11月22日晚餐")
# 表头展示用固定换行（日期一行、"午餐/晚餐"一行），匹配数据仍用 MEALS
MEAL_LABELS = tuple(f"{label[:6]}<br/>{label[6:]}" for label in MEALS)
# 兼容 dinner_reservation 里的简写（如 "20午"），按 (日, 午/晚) 归位
MEAL_HINTS = (("20", "午"), ("20", "晚"), ("21", "午"), ("21", "晚"),
              ("22", "午"), ("22", "晚"))
TITLE_LINE_1 = "“意林杯”四川省第十二届管乐展示活动"
TITLE_LINE_2 = "报名信息表"
FORM_NOTES = (
    "备注：1. 如果需在成都理工大学食堂购票用餐，请备注时间并在对应位置写上就餐人数。",
    "2.10月24日前，网络报名成功后从系统导出并打印《“意林杯”四川省第十二届管乐展示活动报名信息表》，"
    "加盖公章后再扫描（拍照）上传系统；从报名系统上传参加展示活动的曲目视频；"
    "并上传分辨率为600dpi（JPEG或TIFF）的乐团集体照片，及300字以内的乐团简介。",
    "3.报名表的正式队员名单中，铜管乐团可以根据自己的编制填写相关乐器的参展人员名单，无关乐器可不填写。",
)


# docx 字体的本机替代（按优先级）；全部缺失时回退 STSong-Light
_FONT_FILES = (
    ("YLB-FangSong", "fangsong", ("C:/Windows/Fonts/simfang.ttf",
                                  "C:/Windows/Fonts/STFANGSO.TTF")),
    ("YLB-BiaoSong", "biaosong", ("C:/Windows/Fonts/STZHONGS.TTF",)),
    ("YLB-Hei", "hei", ("C:/Windows/Fonts/simhei.ttf",)),
)
_FACES = None


def _faces():
    """注册 docx 对应字体并缓存 {角色: 字体名}；加粗走同族映射（标签 <b>）。"""
    global _FACES
    if _FACES is not None:
        return _FACES
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    faces = {"fangsong": "STSong-Light", "biaosong": "STSong-Light", "hei": "STSong-Light"}
    for reg_name, role, candidates in _FONT_FILES:
        if reg_name in pdfmetrics.getRegisteredFontNames():
            faces[role] = reg_name
            continue
        for path in candidates:
            if os.path.exists(path):
                try:
                    pdfmetrics.registerFont(TTFont(reg_name, path))
                    faces[role] = reg_name
                except Exception:  # 字体文件异常时按缺失处理
                    pass
                break
    # 标签加粗：仿宋无粗体文件，用中宋做族内 bold 近似 Word 的合成加粗
    pdfmetrics.registerFontFamily(faces["fangsong"], normal=faces["fangsong"],
                                  bold=faces["biaosong"], italic=faces["fangsong"],
                                  boldItalic=faces["biaosong"])
    _FACES = faces
    return faces


_KINSOKU_EXTRA = "，；：？！、）》」』】〕〉»％‰"


def _cannot_start():
    import reportlab.lib.textsplit as textsplit
    return textsplit.ALL_CANNOT_START


def _enable_cjk_kinsoku():
    """补全 reportlab 避头点表：内置表缺「》，；」等全角标点，CJK 折行时
    会把闭合标点顶到行首（如「报名信息表》」被拆开）。幂等。"""
    import reportlab.lib.textsplit as textsplit
    import reportlab.platypus.paragraph as rl_paragraph

    merged = textsplit.ALL_CANNOT_START + "".join(
        c for c in _KINSOKU_EXTRA if c not in textsplit.ALL_CANNOT_START)
    textsplit.ALL_CANNOT_START = merged
    rl_paragraph.ALL_CANNOT_START = merged


def _wrap_cjk(text, font, size, max_width):
    """中文避头尾折行：标点不落行首时连同前一个字一起移到下一行（Word 式）。

    reportlab 的 cjkFragSplit 只悬挂一个标点、不链式回退，这里对整段自行
    预折行（备注等纯文本段），用 <br/> 输出固定行。
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth
    cannot_start = _cannot_start()
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if not cur or stringWidth(cur + ch, font, size) <= max_width:
            cur += ch
            continue
        nxt = ch  # 本字放不下；若它是行禁首标点，把当前行尾字一并带下来
        while cur and nxt[0] in cannot_start:
            nxt = cur[-1] + nxt
            cur = cur[:-1]
        # 西文/数字单词不拆（如 "600dpi"）：下行以西文开头时，行尾连续西文一并带下
        while cur and ord(nxt[0]) < 0x3000 and ord(cur[-1]) < 0x3000 and not cur[-1].isspace():
            nxt = cur[-1] + nxt
            cur = cur[:-1]
        lines.append(cur)
        cur = nxt
    if cur:
        lines.append(cur)
    return lines


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    faces = _faces()

    def make(name, size, font=None, alignment=TA_CENTER, leading=None):
        return ParagraphStyle(name, fontName=font or faces["fangsong"], fontSize=size,
                              leading=leading or size * 1.45, alignment=alignment,
                              textColor=colors.black, wordWrap="CJK")

    return {
        "title": make("title", 22, faces["biaosong"], leading=30),
        "attach": make("attach", 16, faces["hei"], TA_LEFT),
        "label": make("label", 10.5),
        "body": make("body", 10.5),
        "value": make("value", 10.5, alignment=TA_LEFT),
        "small": make("small", 10.5, alignment=TA_LEFT),
        "smallc": make("smallc", 10.5),
        "note": make("note", 10.5, alignment=TA_LEFT),
    }


def _meal_cells(report):
    """把 dinner_reservation（JSON 列表）填进 6 个用餐格。

    兼容三种写法：下标 0-5、完整文案（"11月20日午餐"）、简写（"20午"）。
    无法归位的条目返回给调用方放进备注。
    """
    from apps.api.export_services import _json_list
    cells = [""] * len(MEALS)
    leftovers = []
    for item in _json_list(report.dinner_reservation):
        text = str(item).strip()
        matched = False
        if text.isdigit() and 0 <= int(text) < len(MEALS):
            cells[int(text)] = "√"
            matched = True
        else:
            for index, label in enumerate(MEALS):
                if text == label or all(part in text for part in MEAL_HINTS[index]):
                    cells[index] = "√"
                    matched = True
                    break
        if not matched:
            leftovers.append(text)
    return cells, leftovers


def _checkline(options, value):
    """乐团类别 / 参展组别：命中项打 ■，其余保持 □（GB2312 字体内可用）。"""
    value = str(value or "").strip()
    # 必须整项相等："管乐团"是"铜管乐团"的子串，用 in 会把两者同时勾上
    return "　　".join(
        f"{option}■" if option == value else f"{option}□"
        for option in options
    )


def form_context(report):
    """把一张报名表整理成官方表格各栏的纯文本值（便于测试与渲染解耦）。"""
    from apps.api.export_services import _instrument_bucket, _person_name, adviser_instructors
    from apps.core.models import Person, ReportPerson, User

    links = list(ReportPerson.objects.filter(report_id=report.id))
    people = {p.id: p for p in Person.objects.filter(id__in=[x.person_id for x in links if x.person_id])}

    def members(position):
        return [people[x.person_id] for x in links if x.position == position and x.person_id in people]

    formal, reserve = members(0), members(1)
    conductors, teachers = members(2), members(4)

    # 附件2 口径：指挥是教师（关系行 type=1）时，第一指导老师槽即指挥本人，
    # 学校另报的指导老师顺延到第 2 槽（与后台报名数据导出同口径）
    link_types = {x.person_id: x.type for x in links}
    teachers = adviser_instructors(
        conductors, teachers,
        link_types.get(conductors[0].id) if len(conductors) == 1 else None)

    user = User.objects.filter(pk=report.user_id).first()
    school = report.school_name or getattr(user, "nickname", "") or ""

    instrument_names = {key: [] for key in FORM_INSTRUMENTS}
    for person in formal:
        bucket = _instrument_bucket(getattr(person, "instrument", ""))
        if bucket not in instrument_names:
            bucket = "其他"  # 表外乐器（如次中音号）统一并入"其他"栏
        instrument_names[bucket].append(_person_name(person))

    remark_parts = [str(report.remark or "")]
    cells, leftovers = _meal_cells(report)
    if leftovers:
        remark_parts.append("用餐预约：" + "、".join(leftovers))

    return {
        "school": school,
        "leader_name": report.contact_name or "",
        "leader_phone": report.contact_phone or "",
        "conductor_name": "、".join(_person_name(p) for p in conductors),
        "conductor_phone": "、".join(str(p.phone or "") for p in conductors if p.phone),
        "teacher_lines": [f"{i}. {_person_name(p)}" for i, p in enumerate(teachers, start=1)],
        "teacher_phones": [str(p.phone or "") for p in teachers],
        "type_line": _checkline(("管乐团", "铜管乐团"), report.establishment),
        "group_line": _checkline(("小学组", "中学组", "大学组"), report.group),
        "assigned_song": report.name1 or "",
        "optional_song": report.name or "",
        "headcount": f"正式队员 {len(formal)} 人，预备队员 {len(reserve)} 人",
        "instrument_cells": [f"{key}：{'、'.join(names)}" for key, names in instrument_names.items()],
        "reserve_names": "、".join(_person_name(p) for p in reserve),
        "remark": "\n".join(part for part in remark_parts if part),
        "meal_cells": cells,
    }


# 主表列宽：标签列 | 内容列 | 电话标签列 | 电话值列（内容区跨列总宽 146mm）
COLUMN_WIDTHS = [28, 66, 22, 58]
CONTENT_WIDTH = sum(COLUMN_WIDTHS[1:])
# 嵌套表放在跨列单元格里，要扣掉主表左右内边距，否则会溢出右边框
CELL_INNER = CONTENT_WIDTH - 10


def _report_story(report, styles, is_last):
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, Spacer, Table, TableStyle

    def cell(text, style="body"):
        return Paragraph(text, styles[style]) if str(text) else ""

    ctx = form_context(report)
    story = [
        Paragraph("附件2", styles["attach"]),
        Spacer(1, 6),
        Paragraph(TITLE_LINE_1, styles["title"]),
        Paragraph(TITLE_LINE_2, styles["title"]),
        Spacer(1, 10),
    ]

    # 指导老师：官方表格固定两个名额槽，各带一列联系电话；超出 2 人的并入第 2 槽。
    # 指挥为教师时第 1 槽已在 form_context 并入指挥本人（附件2 口径）
    lines, phones = ctx["teacher_lines"], ctx["teacher_phones"]
    teacher_slots = [(lines[i] if i < len(lines) else f"{i + 1}.",
                      phones[i] if i < len(phones) else "") for i in range(2)]
    if len(lines) > 2:
        teacher_slots[1] = ("、".join(lines[1:]), "、".join(p for p in phones[1:] if p))

    instrument_table = Table(
        [[cell(text, "small") for text in ctx["instrument_cells"][row * 3:row * 3 + 3]]
         for row in range((len(ctx["instrument_cells"]) + 2) // 3)],
        colWidths=[(CELL_INNER / 3) * mm] * 3,
    )
    instrument_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))

    meal_table = Table(
        [[cell(label, "smallc") for label in MEAL_LABELS],
         [cell(text, "body") for text in ctx["meal_cells"]]],
        colWidths=[(CONTENT_WIDTH / 6) * mm] * len(MEALS),
    )
    meal_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors_black()),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        # 填 √ 的空行加高一点，手写人数/勾选更从容
        ("TOPPADDING", (0, 1), (-1, 1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 11),
    ]))

    school_cell = Table(
        [[cell(ctx["school"], "body"), cell("（盖章）")]],
        colWidths=[(CELL_INNER - 36) * mm, 36 * mm],
    )
    school_cell.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))

    def lbl(text):
        """docx 中左列标签与「联系电话」为仿宋加粗（族内 bold 映射）。"""
        return Paragraph(f"<b>{text}</b>", styles["label"])

    rows = [
        [lbl("参展学校"), school_cell, "", ""],
        [lbl("领队姓名"), cell(ctx["leader_name"], "value"), lbl("联系电话"), cell(ctx["leader_phone"], "value")],
        [lbl("指挥"), cell(ctx["conductor_name"], "value"), lbl("联系电话"), cell(ctx["conductor_phone"], "value")],
        [lbl("指导老师<br/>（本校在职）"),
         cell(teacher_slots[0][0], "value"), lbl("联系电话"), cell(teacher_slots[0][1], "value")],
        ["", cell(teacher_slots[1][0], "value"), lbl("联系电话"), cell(teacher_slots[1][1], "value")],
        [lbl("乐团类别"), cell(ctx["type_line"], "value"), "", ""],
        [lbl("参展组别"), cell(ctx["group_line"], "value"), "", ""],
        [lbl("指定曲目"), cell(ctx["assigned_song"], "value"), "", ""],
        [lbl("自选曲目"), cell(ctx["optional_song"], "value"), "", ""],
        [lbl("参展人数"), cell(ctx["headcount"], "value"), "", ""],
        [lbl("正式队员<br/>名单（可单独表格提供）"), instrument_table, "", ""],
        [lbl("预备队员<br/>名　　单"), cell(ctx["reserve_names"], "value"), "", ""],
        [lbl("备注"), cell(ctx["remark"].replace("\n", "<br/>"), "value"), "", ""],
        [lbl("用餐预约"), meal_table, "", ""],
    ]
    table = Table(rows, colWidths=[w * mm for w in COLUMN_WIDTHS], repeatRows=0)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors_black()),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 0), (-1, -1), _faces()["fangsong"]),
        ("FONTSIZE", (0, 0), (-1, -1), 10.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        # 用餐格嵌套表铺满整个跨列单元格，让内外边框重合（docx 中没有额外的框）
        ("LEFTPADDING", (1, 13), (3, 13), 0),
        ("RIGHTPADDING", (1, 13), (3, 13), 0),
        ("TOPPADDING", (1, 13), (3, 13), 0),
        ("BOTTOMPADDING", (1, 13), (3, 13), 0),
        ("SPAN", (0, 3), (0, 4)),  # 指导老师标签纵跨两个名额槽
        ("SPAN", (1, 0), (3, 0)),
        ("SPAN", (1, 5), (3, 5)),
        ("SPAN", (1, 6), (3, 6)),
        ("SPAN", (1, 7), (3, 7)),
        ("SPAN", (1, 8), (3, 8)),
        ("SPAN", (1, 9), (3, 9)),
        ("SPAN", (1, 10), (3, 10)),
        ("SPAN", (1, 11), (3, 11)),
        ("SPAN", (1, 12), (3, 12)),
        ("SPAN", (1, 13), (3, 13)),
    ]))
    story.append(table)
    story.append(Spacer(1, 8))
    # 备注段自行避头尾预折行：reportlab 的 CJK 折行不链式回退，「》，」
    # 连排时第二个标点仍会顶到行首；行宽按 A4-2×17mm 再扣 Frame 默认
    # 左右各 6pt 内边距，否则预折行超宽会被 CJK 兜底二次折行
    from reportlab.lib.pagesizes import A4
    faces = _faces()
    note_width = A4[0] - 34 * mm - 12
    for note in FORM_NOTES:
        wrapped = "<br/>".join(_wrap_cjk(note, faces["fangsong"], 10.5, note_width))
        story.append(Paragraph(wrapped, styles["note"]))
    if not is_last:
        story.append(PageBreak())
    return story


def colors_black():
    from reportlab.lib import colors
    return colors.black


def _render_pdf(reports) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    from apps.api.export_services import _cjk_pdf_font

    _cjk_pdf_font()
    _enable_cjk_kinsoku()
    styles = _styles()

    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4,
                            leftMargin=17 * mm, rightMargin=17 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title=TITLE_LINE_2)
    reports = list(reports)
    story = []
    for index, report in enumerate(reports):
        story.extend(_report_story(report, styles, is_last=index == len(reports) - 1))
    if not story:
        story = [Paragraph("暂无报名表", styles["body"])]
    doc.build(story)
    return output.getvalue()


def registration_form_response(reports, filename="报名信息表.pdf"):
    try:
        content = _render_pdf(reports)
    except ImportError:
        lines = ["参赛学校,自选曲目,参展组别,领队姓名,联系电话"]
        for item in reports:
            lines.append(",".join([item.school_name or "", item.name or "",
                                   item.group or "", item.contact_name or "",
                                   item.contact_phone or ""]))
        content = "\n".join(lines).encode()
    result = HttpResponse(content, content_type="application/pdf")
    result["Content-Disposition"] = f'attachment; filename="{filename}"'
    return result
