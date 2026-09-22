"""Export row builders and workbook writers for the Laravel-compatible API.

The Laravel application uses ``maatwebsite/excel`` for the XLSX endpoints.  A
small service module keeps the Django Ninja views thin while retaining the
original column order, labels and value transformations.  All data access is
through the Django ORM; no database-specific SQL is required.
"""

from __future__ import annotations

import io
import json
from collections import defaultdict
from typing import Iterable, Mapping, Optional, Sequence

from django.http import HttpResponse

from apps.core.models import Draw, Files, Person, Report, ReportPerson, User


def _cjk_pdf_font():
    """Reportlab's built-in Adobe CJK face; no external font file required.

    Non-embedded (viewer supplies the glyphs); registration is idempotent.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    name = "STSong-Light"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(name))
    return name


REPORT_DATA_HEADINGS = [
    "所属单位", "乐团名称", "自选曲目名称", "指定曲目名称", "参报代码",
    "参展学校名称", "描述", "组别", "领队姓名", "领队电话", "联系地址或邮箱",
    "节目时长", "集体照", "视频文件", "状态", "正式队员", "预备队员", "指挥", "指导教师",
]

ADMIN_DATA1_HEADINGS = [
    "序号", "所属单位", "乐团名称", "自选曲目", "指定曲目", "类型", "参演组别",
    "节目时长", "参展学校名称", "领队姓名", "领队电话", "联系地址", "用餐预约",
    "乐团简介", "状态", "正式队员", "预备队员", "指挥", "指导教师",
]

ADMIN_DATA2_HEADINGS = [
    "序号", "报名学校", "参展学校名称", "乐团名称", "领队", "领队电话", "指挥",
    "指挥电话", "指挥身份证", "指导老师", "指导老师电话", "指导老师身份证", "乐团类型",
    "参演组别", "指定曲目", "自选曲目", "曲子时长", "短笛", "长笛", "单簧管",
    "低音单簧", "中音萨克", "次中音萨", "上低音萨", "双簧管", "大管", "小号",
    "长号", "圆号", "上低音号", "大号", "打击乐", "低音大提", "其他", "合计",
]

INSTRUMENTS = (
    "短笛", "长笛", "单簧管", "低音单簧管", "中音萨克斯", "次中音萨克斯",
    "上低音萨克斯", "双簧管", "大管", "小号", "长号", "圆号", "上低音号", "大号",
    "打击乐", "低音大提琴", "其他",
)


def seconds_to_human(seconds) -> str:
    """Match Laravel's ``SToHis`` helper (whole seconds, Chinese suffixes)."""

    try:
        value = int(seconds or 0)
    except (TypeError, ValueError):
        value = 0
    minutes, remainder = divmod(value, 60)
    return f"{minutes}分{remainder}秒" if minutes else f"{remainder}秒"


def status_label(status) -> str:
    return {0: "待审核", 1: "已通过", -1: "未通过"}.get(status, "-")


def group_number(group) -> Optional[int]:
    return {"小学组": 1, "中学组": 2, "大学组": 3}.get(group)


def export_code(group, report_id: int):
    """Build the report code used by ``Api\\ExportController``.

    Unknown group values are left as a string rather than producing an invalid
    integer.  The Laravel code's known production groups all map to 1..3.
    """

    number = group_number(group)
    if number is None:
        return "-"
    return int(f"240{number}000000") + int(report_id)


def _json_list(value):
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return [value]
        return parsed if isinstance(parsed, list) else [parsed]
    return [value]


def _person_name(person):
    return getattr(person, "name", "") if person else ""


def _snapshot(reports: Iterable[Report]):
    """Materialise related objects once, preserving Laravel eager-load order."""

    records = list(reports)
    report_ids = [item.id for item in records]
    links = list(ReportPerson.objects.filter(report_id__in=report_ids).order_by("id")) if report_ids else []
    person_ids = [link.person_id for link in links if link.person_id is not None]
    people = {person.id: person for person in Person.objects.filter(id__in=person_ids)} if person_ids else {}
    user_ids = [item.user_id for item in records if item.user_id is not None]
    users = {user.id: user for user in User.all_objects.filter(id__in=user_ids)} if user_ids else {}
    file_ids = [x for item in records for x in (item.file, item.spectrum) if x is not None]
    files = {item.id: item for item in Files.objects.filter(id__in=file_ids)} if file_ids else {}
    grouped = defaultdict(list)
    for link in links:
        grouped[link.report_id].append((link, people.get(link.person_id)))
    return records, grouped, users, files


def _members(grouped, report_id):
    """Return names grouped by Laravel ``ReportPerson.position`` values."""

    result = defaultdict(list)
    for link, person in grouped.get(report_id, []):
        if person is not None:
            result[int(link.position)].append(person)
    return result


def _joined(members, position):
    return "、".join(_person_name(person) for person in members.get(position, []))


def report_data_rows(reports: Iterable[Report]) -> list[list]:
    """Rows for ``GET /export/data`` (the 19-column Laravel export)."""

    records, grouped, users, files = _snapshot(reports)
    rows = [REPORT_DATA_HEADINGS.copy()]
    for item in records:
        members = _members(grouped, item.id)
        user = users.get(item.user_id)
        spectrum = files.get(item.spectrum)
        video = files.get(item.file)
        rows.append([
            getattr(user, "nickname", "") if user else "",
            item.choir_name or "",
            item.name or "",
            item.name1 or "",
            export_code(item.group, item.id),
            item.school_name or "",
            item.desc or "",
            item.group or "",
            item.contact_name or "",
            item.contact_phone or "",
            item.contact_way or "",
            seconds_to_human(item.time_length),
            getattr(spectrum, "url", "") if spectrum else "",
            getattr(video, "url", "") if video else "",
            status_label(item.status),
            _joined(members, 0),
            _joined(members, 1),
            _joined(members, 2),
            _joined(members, 4),
        ])
    return rows


def admin_data1_rows(reports: Iterable[Report]) -> list[list]:
    """Rows for ``GET /admin/export/data1``."""

    records, grouped, users, _files = _snapshot(reports)
    rows = [ADMIN_DATA1_HEADINGS.copy()]
    for index, item in enumerate(records, start=1):
        members = _members(grouped, item.id)
        user = users.get(item.user_id)
        dinner = _json_list(item.dinner_reservation)
        rows.append([
            index,
            getattr(user, "nickname", "") if user else "",
            item.choir_name or "",
            item.name or "",
            item.name1 or "",
            item.establishment or "",
            item.group or "",
            seconds_to_human(item.time_length),
            item.school_name or "",
            item.contact_name or "",
            item.contact_phone or "",
            item.contact_way or "",
            "、".join(str(x) for x in dinner),
            item.desc or "",
            status_label(item.status),
            _joined(members, 0),
            _joined(members, 1),
            _joined(members, 2),
            _joined(members, 4),
        ])
    return rows


def _instrument_bucket(instrument):
    """Map known input spellings to the source export's instrument columns."""

    value = str(instrument or "").strip()
    aliases = {
        "低音单簧": "低音单簧管", "中音萨克": "中音萨克斯", "次中音萨": "次中音萨克斯",
        "上低音萨": "上低音萨克斯", "低音大提": "低音大提琴",
    }
    value = aliases.get(value, value)
    return value if value in INSTRUMENTS else "其他"


def admin_data2_rows(reports: Iterable[Report]) -> list[list]:
    """Rows for ``GET /admin/export/data2`` including instrument totals."""

    records, grouped, users, _files = _snapshot(reports)
    rows = [ADMIN_DATA2_HEADINGS.copy()]
    for index, item in enumerate(records, start=1):
        members = _members(grouped, item.id)
        user = users.get(item.user_id)
        instrument_counts = {key: 0 for key in INSTRUMENTS}
        conduct_name = conduct_phone = conduct_card = ""
        faculty = []
        for person in members.get(0, []) + members.get(1, []):
            instrument_counts[_instrument_bucket(getattr(person, "instrument", ""))] += 1
        for person in members.get(2, []):
            # Laravel assigns the last conductor when malformed duplicate data
            # exists; retaining that behavior is useful for old records.
            conduct_name = getattr(person, "name", "") or ""
            conduct_phone = getattr(person, "phone", "") or ""
            conduct_card = "'" + str(getattr(person, "card", "") or "")
        faculty = members.get(4, [])
        faculty_info = faculty[0] if faculty else None
        # Source behavior skips the conductor-named first adviser when a second
        # adviser exists.  Missing advisers are represented by empty cells.
        if faculty_info and _person_name(faculty_info) == conduct_name and len(faculty) > 1:
            faculty_info = faculty[1]
        faculty_name = getattr(faculty_info, "name", "") if faculty_info else ""
        faculty_phone = getattr(faculty_info, "phone", "") if faculty_info else ""
        faculty_card = "'" + str(getattr(faculty_info, "card", "") or "") if faculty_info else ""
        rows.append([
            index,
            getattr(user, "nickname", "") if user else "",
            item.school_name or "",
            item.choir_name or "",
            item.contact_name or "",
            item.contact_phone or "",
            conduct_name,
            conduct_phone,
            conduct_card,
            faculty_name,
            faculty_phone,
            faculty_card,
            item.establishment or "",
            item.group or "",
            item.name1 or "",
            item.name or "",
            seconds_to_human(item.time_length),
            *(str(instrument_counts[key]) for key in INSTRUMENTS),
            sum(instrument_counts.values()),
        ])
    return rows


def _safe_title(title: str, fallback: str = "Sheet") -> str:
    title = str(title or fallback)
    # Excel worksheet names cannot contain these characters and are limited to
    # 31 characters.  Laravel's configured titles are already valid, but this
    # keeps old/custom data from making the response fail.
    for char in "[]:*?/\\":
        title = title.replace(char, "_")
    return title[:31] or fallback


def workbook_response(
    sheets: Sequence[tuple[str, Sequence[Sequence]]],
    filename: str,
    *,
    draw_style: bool = False,
) -> HttpResponse:
    """Serialize one or more row matrices into a downloadable XLSX response."""

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, Side
        from openpyxl.utils import get_column_letter

        workbook = Workbook()
        first = True
        for title, rows in sheets:
            sheet = workbook.active if first else workbook.create_sheet()
            first = False
            sheet.title = _safe_title(title)
            for row in rows:
                sheet.append(list(row))
            if draw_style:
                thin = Side(style="thin", color="000000")
                border = Border(left=thin, right=thin, top=thin, bottom=thin)
                max_row = max(1, sheet.max_row)
                for row in sheet.iter_rows(min_row=1, max_row=max_row, min_col=1, max_col=3):
                    for cell in row:
                        cell.alignment = Alignment(horizontal="center", vertical="center")
                        cell.border = border
                for row_index in range(1, max_row + 1):
                    sheet.row_dimensions[row_index].height = 25
                for cell in sheet[1]:
                    cell.font = Font(bold=True, color="000000")
                if max_row >= 2:
                    for cell in sheet[2]:
                        cell.font = Font(bold=True, color="000000")
                sheet.merge_cells("A1:C1")
                sheet.column_dimensions["A"].width = 10
                sheet.column_dimensions["B"].width = 50
                sheet.column_dimensions["C"].width = 10
            else:
                sheet.freeze_panes = "A2"
                for cell in sheet[1] if sheet.max_row else []:
                    cell.font = Font(bold=True)
                for column in sheet.columns:
                    values = [len(str(cell.value or "")) for cell in column]
                    width = min(50, max(12, max(values or [0]) + 2))
                    sheet.column_dimensions[get_column_letter(column[0].column)].width = width
        output = io.BytesIO()
        workbook.save(output)
        content = output.getvalue()
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    except ImportError:
        # Keep the same graceful degradation as the existing endpoint helper.
        import csv

        output = io.StringIO()
        writer = csv.writer(output)
        for _title, rows in sheets:
            writer.writerows(rows)
        content = output.getvalue().encode("utf-8-sig")
        content_type = "text/csv"
    result = HttpResponse(content, content_type=content_type)
    result["Content-Disposition"] = f'attachment; filename="{filename}"'
    return result


def reports_export_response(reports: Iterable[Report], filename: str = "数据导出.xlsx") -> HttpResponse:
    return workbook_response([("数据导出", report_data_rows(reports))], filename)


def admin_data1_response(reports: Iterable[Report], filename: str = "数据导出.xlsx") -> HttpResponse:
    return workbook_response([("数据导出", admin_data1_rows(reports))], filename)


def admin_data2_response(reports: Iterable[Report], filename: str = "数据导出.xlsx") -> HttpResponse:
    return workbook_response([("数据导出", admin_data2_rows(reports))], filename)


DRAW_TYPES = {
    1: "大学组 (非专业组)",
    2: "大学组（专业组）",
    3: "教师组",
    4: "中小学组",
}


def draw_type_label(draw_type) -> str:
    try:
        return DRAW_TYPES.get(int(draw_type), "")
    except (TypeError, ValueError):
        return ""


def draw_rows(draws: Iterable[Draw], draw_type=None) -> list[list]:
    rows = [["序号", "合唱团名称", "抽签序号"]]
    for index, item in enumerate(draws, start=1):
        rows.append([index, item.name or "", item.index if item.index is not None else ""])
    return rows


def draw_response(draws: Iterable[Draw], draw_type: int, filename: Optional[str] = None) -> HttpResponse:
    label = draw_type_label(draw_type)
    rows = [[f"{label}现场展演抽签顺序表", "", ""]] + draw_rows(draws, draw_type)
    return workbook_response([(f"{label}现场展演抽签顺序表", rows)], filename or f"{label}现场展演抽签顺序表.xlsx", draw_style=True)


def draw_all_response(draws_by_type: Mapping[int, Iterable[Draw]], filename: str = "所有类别抽签排序表.xlsx") -> HttpResponse:
    sheets = []
    titles = {1: "大学组 (非专业组)", 2: "大学组（专业组）", 3: "教师组", 4: "中小学组"}
    for draw_type in (1, 2, 3, 4):
        label = draw_type_label(draw_type)
        rows = [[f"{label}现场展演抽签顺序表", "", ""]] + draw_rows(draws_by_type.get(draw_type, []), draw_type)
        sheets.append((titles[draw_type], rows))
    return workbook_response(sheets, filename, draw_style=True)

