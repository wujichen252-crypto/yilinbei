"""Django Ninja endpoints retaining the Laravel API contract.

The source API intentionally returns HTTP 200 for most business failures.  The
helpers below preserve that convention while Django authentication and role
denials use 401/403 as the original middleware did.
"""
import csv
import io
import json
import logging
import uuid
from datetime import datetime

from django.conf import settings
from django.db import connection, transaction
from django.db.models import F, Q
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import NinjaAPI
from ninja.errors import HttpError

from apps.core.models import (Crew, Draw, Files, Leader, LiveReport, Logs,
                              Person, PersonalAccessToken, Report, ReportDraft,
                              ReportPerson, Recommend, ScanFiles, Ticket,
                              TicketSubscribe, User)
from apps.core.report_drafts import (
    DraftError, DraftIntegrityError, DraftNotFound, DraftConflict,
    ReportNotRejected,
    SubmissionError, build_payload_from_report, create_or_get_draft,
    create_report_from_submission, decode_payload, draft_summary,
    encode_payload, normalize_draft_payload, parse_submission_payload,
    update_draft, update_rejected_report_from_submission,
)
from apps.core.services import (attach_report_people, failure, list_page,
                                live_report_dict, model_dict, new_code,
                                parse_body, report_dict, store_people,
                                success, user_dict, valid_person_head,
                                verify_user_password, write_log)

from .auth import BearerAuth
from .export_services import (
    _cjk_pdf_font,
    admin_data1_response,
    admin_data2_response,
    draw_all_response,
    draw_response,
    reports_export_response,
)
from .registration_form import registration_form_response

api = NinjaAPI(title="YLB Government Program API", version="1.0.0",
               description="Laravel 7 compatibility API migrated to Django Ninja")
auth = BearerAuth()


def response(data, status=200):
    return JsonResponse(data, safe=True, status=status)


def body(request):
    return parse_body(request)


def role_error(request, expected):
    user = request.auth
    if not user or user.type != expected:
        return response({"error": "无该页面操作权限！"}, 403)
    return None


def serialize_user_list(qs):
    return [user_dict(x) for x in qs]


def request_ids(request, data=None):
    values = request.GET.getlist("ids[]") + request.GET.getlist("ids")
    if data and isinstance(data.get("ids"), list):
        values += data["ids"]
    return [int(v) for v in values if str(v).isdigit()]


def report_queryset(request, current_user=None):
    qs = Report.objects.all().order_by("id")
    if current_user:
        qs = qs.filter(user_id=current_user.id)
    keyword = request.GET.get("keyword")
    if keyword:
        if current_user:
            qs = qs.filter(name__icontains=keyword)
        else:
            qs = qs.filter(Q(name__icontains=keyword) | Q(choir_name__icontains=keyword))
    if request.GET.get("status") not in (None, ""):
        qs = qs.filter(status=request.GET.get("status"))
    if request.GET.get("group") not in (None, ""):
        qs = qs.filter(group=request.GET.get("group"))
    return qs


def report_page(request, current_user=None, descending=False):
    qs = report_queryset(request, current_user)
    if descending:
        qs = qs.order_by("-id")
    return response(list_page(qs, request, report_dict))


def create_report(request, province=False):
    user = request.auth
    data = body(request)
    if province and Report.objects.filter(user_id=user.id).count() >= 8:
        return response(failure("目前您的单位已超报送限制,无法再继续进行报送!"))
    people = data.get("person", [])
    with transaction.atomic():
        ok, stored = store_people(user, people)
        if not ok:
            transaction.set_rollback(True)
            return response(failure(stored))
        values = {f.name for f in Report._meta.fields}
        values -= {"id", "created_at", "updated_at", "deleted_at", "user_id"}
        payload = {k: v for k, v in data.items() if k in values}
        for key in ("read", "minute", "second", "fileList", "person"):
            payload.pop(key, None)
        if not province:
            payload["dinner_reservation"] = data.get("dinner_reservation") or []
        else:
            payload.pop("dinner_reservation", None)
        report = Report.objects.create(user_id=user.id, **payload)
        attach_report_people(report.id, stored)
    write_log(user, 2, "创建节目报名表 " + str(report.name))
    return response(success("创建成功", report_dict(report)))


def update_report(request, on_behalf=False):
    user = request.auth
    data = body(request)
    report = Report.objects.filter(pk=data.get("id")).first()
    if not report:
        return response(failure("报名表不存在！"))
    if not on_behalf and report.user_id != user.id:
        return response(failure("不具备该报表修改信息权限！"))
    people = data.get("person", [])
    with transaction.atomic():
        # Admin/committee edits stay attributed to the owning school so the
        # report's ownership and its people records never change hands.
        ok, stored = store_people(report.user_id if on_behalf else user, people)
        if not ok:
            transaction.set_rollback(True)
            return response(failure(stored))
        fields = {f.name for f in Report._meta.fields}
        fields -= {"id", "created_at", "updated_at", "deleted_at", "user_id"}
        for key, value in data.items():
            if key in fields and key not in {"status", "dinner_reservation"}:
                setattr(report, key, value)
        if "dinner_reservation" in fields:
            report.dinner_reservation = data.get("dinner_reservation") or []
        if not on_behalf:
            report.user_id = user.id
        report.status = 0
        report.save()
        attach_report_people(report.id, stored)
    write_log(user, 1, "修改节目报名表 " + str(data.get("name", report.name)))
    return response(success("修改成功！", None))


def report_detail(request, expected, id):
    err = role_error(request, expected)
    report = Report.objects.filter(pk=id).first()
    if err: return err
    if not report: return response(success("获取成功！", None))
    return response(success("获取成功！", report_dict(report)))


def recommend_dict(item):
    value = model_dict(item)
    value["user"] = user_dict(User.objects.filter(pk=item.user_id).first())
    return value


def live_page(request, current_user=None):
    qs = LiveReport.objects.all().order_by("id")
    if current_user:
        qs = qs.filter(user_id=current_user.id)
    keyword = request.GET.get("keyword")
    if keyword:
        qs = qs.filter(name__icontains=keyword)
    if request.GET.get("status") not in (None, ""):
        qs = qs.filter(status=request.GET["status"])
    if request.GET.get("group") not in (None, ""):
        qs = qs.filter(group=request.GET["group"])
    return response(list_page(qs, request, live_report_dict))


def stats_for_group(user_id, group):
    qs = Report.objects.filter(user_id=user_id, group=str(group))
    return [qs.count(), qs.filter(status=-1).count(), qs.filter(status=0).count(), qs.filter(status=1).count()]


def xlsx_response(rows, filename):
    try:
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter
        workbook = Workbook()
        sheet = workbook.active
        for row in rows:
            sheet.append(list(row))
        for column in sheet.columns:
            sheet.column_dimensions[get_column_letter(column[0].column)].width = min(
                50, max(12, max(len(str(cell.value or "")) for cell in column) + 2))
        output = io.BytesIO()
        workbook.save(output)
        content = output.getvalue()
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    except ImportError:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerows(rows)
        content, content_type = output.getvalue().encode("utf-8-sig"), "text/csv"
    result = HttpResponse(content, content_type=content_type)
    result["Content-Disposition"] = f'attachment; filename="{filename}"'
    return result


def pdf_response(rows, filename):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        output = io.BytesIO()
        canvas_obj = canvas.Canvas(output, pagesize=A4)
        font = _cjk_pdf_font()
        canvas_obj.setFont(font, 9)
        y = 810
        for row in rows:
            canvas_obj.drawString(36, y, " | ".join(str(x or "") for x in row)[:180])
            y -= 16
            if y < 40:
                canvas_obj.showPage(); y = 810
                canvas_obj.setFont(font, 9)  # showPage resets graphics state
        canvas_obj.save()
        content = output.getvalue()
    except ImportError:
        content = "\n".join(",".join(str(x or "") for x in row) for row in rows).encode()
    result = HttpResponse(content, content_type="application/pdf")
    result["Content-Disposition"] = f'attachment; filename="{filename}"'
    return result


@api.post("/login")
def login(request):
    data = body(request)
    user = User.objects.filter(username=data.get("username", "")).first()
    if not user:
        return response(failure("用户不存在"))
    if not verify_user_password(user, data.get("password", "")):
        return response(failure("账号或密码错误"))
    from apps.core.models import PersonalAccessToken
    token, plain = PersonalAccessToken.issue(user)
    write_log(user, 4, "用户登录")
    return response(success("登录成功", {"token": plain, "user": user_dict(user)}))


@api.post("/logout", auth=auth)
def logout(request):
    plain = request.headers.get("Authorization", "").split(" ", 1)[-1]
    from apps.core.models import PersonalAccessToken
    if plain and "|" in plain:
        import hashlib
        token_id, secret = plain.split("|", 1)
        if token_id.isdigit():
            PersonalAccessToken.objects.filter(
                pk=int(token_id), token=hashlib.sha256(secret.encode()).hexdigest()
            ).delete()
    write_log(request.auth, 5, "用户退出")
    return response(success("退出成功"))


@api.get("/user", auth=auth)
def user_info(request):
    # 读取当前登录用户：只认 request.auth，不接受任何 query/body 里的用户 id，
    # 复用 user_dict 保证与登录接口返回结构一致（且永不回传 password）。
    user = User.objects.filter(pk=request.auth.id).first()
    if not user:
        return response(failure("用户不存在"))
    return response(success("获取成功", user_dict(user)))


@api.put("/user", auth=auth)
def user_update(request):
    data = body(request)
    requested_id = data.get("id", request.auth.id)
    if str(requested_id) != str(request.auth.id):
        return response(failure("无该用户修改权限！"))
    user = User.objects.filter(pk=request.auth.id).first()
    if not user:
        return response(failure("用户不存在"))
    for key in ("username", "nickname", "description", "tel", "leader"):
        if key in data:
            setattr(user, key, data[key])
    if data.get("password"):
        user.set_password(data["password"])
    user.save()
    write_log(request.auth, 1, "修改用户 " + user.username)
    return response(success())


@api.get("/qiniu/token", auth=auth)
def qiniu_token(request):
    if not (settings.QINIU_ACCESS_KEY and settings.QINIU_SECRET_KEY and settings.QINIU_BUCKET):
        return response(failure("七牛服务未配置"))
    try:
        from qiniu import Auth
        token = Auth(settings.QINIU_ACCESS_KEY, settings.QINIU_SECRET_KEY).upload_token(settings.QINIU_BUCKET)
        return response({"code": 0, "uptoken": token})
    except Exception:
        return response(failure("获取上传凭证失败"))


@api.post("/file/create", auth=auth)
def file_create(request):
    data = body(request); fields = {f.name for f in Files._meta.fields} - {"id", "created_at", "updated_at"}
    obj = Files.objects.create(user_id=request.auth.id, **{k: v for k, v in data.items() if k in fields and k != "user_id"})
    return response(success("", model_dict(obj)))


@api.get("/file/list", auth=auth)
def file_list(request):
    ids = request_ids(request)
    qs = Files.objects.filter(id__in=ids)
    return response(success("", [model_dict(x) for x in qs]))


@api.get("/scan/list", auth=auth)
def scan_list(request):
    types = [0, 1, 4]
    qs = User.objects.filter(type__in=types).order_by("id")
    if request.GET.get("keyword"):
        qs = qs.filter(nickname__icontains=request.GET["keyword"])
    scan_type = request.GET.get("type")
    if scan_type not in (None, ""):
        ids = ScanFiles.objects.filter(type=scan_type).values_list("user_id", flat=True)
        qs = qs.filter(id__in=ids)
    def item(u):
        d = user_dict(u); d["scanfile"] = [model_dict(x) for x in ScanFiles.objects.filter(user_id=u.id)]
        return d
    return response(list_page(qs, request, item))


@api.post("/scan/cau", auth=auth)
def scan_create_update(request):
    data = body(request); obj, created = ScanFiles.objects.get_or_create(user_id=request.auth.id, type=data.get("type"))
    for key in ("files", "status", "remark"):
        if key in data: setattr(obj, key, data[key])
    obj.save()
    return response(success("新建成功!" if created else "修改成功!", model_dict(obj)))


@api.get("/scan/files", auth=auth)
def scan_files(request):
    if request.auth.type in (2, 3):
        uid = request.GET.get("user_id")
    else:
        uid = request.auth.id
    if not uid:
        return response(success("没有扫描文件信息！", None))
    obj = ScanFiles.objects.filter(user_id=uid, type=request.GET.get("type", 0)).first()
    if obj:
        payload = model_dict(obj)
        return response(success("获取成功！" if obj.files else "没有扫描文件上传！", payload))
    return response(success("没有扫描文件信息！", None))


@api.get("/export/report", auth=auth)
def export_report(request):
    """按组委会《附件2》式样导出报名信息表，每张报名表一页。"""
    reports = list(Report.objects.filter(user_id=request.auth.id, status__gte=0).order_by("id"))
    write_log(request.auth, 6, "导出报名信息表")
    return registration_form_response(reports, "报名信息表.pdf")


@api.get("/export/person", auth=auth)
def export_person(request):
    rows = [["序号", "姓名", "性别", "年龄", "学校名称", "身份", "角色", "备注"]]
    sequence = 0
    for report in Report.objects.filter(user_id=request.auth.id, status__gte=0).order_by("id"):
        for link in ReportPerson.objects.filter(report_id=report.id):
            p = Person.objects.filter(pk=link.person_id).first()
            if p and link.position != 4:
                sequence += 1
                rows.append([sequence, p.name, p.gender or "", p.age or "", p.school or "", link.type, link.position, p.remark or ""])
    return pdf_response(rows, "参演人员信息表.pdf")


@api.get("/export/data", auth=auth)
def export_data(request):
    qs = Report.objects.all().order_by("id")
    if request.GET.get("group"):
        qs = qs.filter(group=request.GET["group"])
    write_log(request.auth, 6, "导出报送数据")
    return reports_export_response(qs, "数据导出.xlsx")


@api.get("/live/list", auth=auth)
def live_list(request):
    return live_page(request, request.auth)


@api.get("/live/{id}", auth=auth)
def live_get(request, id: int):
    obj = LiveReport.objects.filter(pk=id).first()
    if not obj: return response(failure("未找到相关信息！"))
    if obj.user_id != request.auth.id: return response(failure("不具备该报表查看权限！"))
    return response(success("获取成功！", live_report_dict(obj)))


@api.put("/live/", auth=auth)
def live_update(request):
    data = body(request); obj = LiveReport.objects.filter(pk=data.get("id")).first()
    if not obj or obj.user_id != request.auth.id: return response(failure("不具备该报表修改信息权限！"))
    with transaction.atomic():
        fields = {f.name for f in LiveReport._meta.fields} - {"id", "created_at", "updated_at", "user_id"}
        for k, v in data.items():
            if k in fields and k not in {"leader", "crew"}: setattr(obj, k, v)
        obj.status = 0; obj.save()
        Leader.objects.filter(live_report_id=obj.id).delete(); Crew.objects.filter(live_report_id=obj.id).delete()
        for item in data.get("leader", []): Leader.objects.create(live_report_id=obj.id, **{k: v for k, v in item.items() if k in {f.name for f in Leader._meta.fields} - {"id", "live_report_id", "created_at", "updated_at"}})
        for item in data.get("crew", []): Crew.objects.create(live_report_id=obj.id, **{k: v for k, v in item.items() if k in {f.name for f in Crew._meta.fields} - {"id", "live_report_id", "created_at", "updated_at"}})
    write_log(request.auth, 1, "更新现场报名表 " + str(obj.name))
    return response(success("操作成功！", None))


def stats_admin(request):
    result = []
    for group, name in (("小学组", "小学组报名情况"), ("中学组", "中学组报名情况"), ("大学组", "大学组报名情况")):
        qs = Report.objects.filter(group=group)
        result.append({"data": [qs.count(), qs.filter(status=-1).count(), qs.filter(status=0).count(), qs.filter(status=1).count()], "name": name})
    return result


@api.get("/admin/index/total", auth=auth)
def admin_total(request):
    err = role_error(request, 3)
    return err or response(success("获取成功！", stats_admin(request)))


@api.get("/admin/report/list", auth=auth)
def admin_report_list(request):
    err = role_error(request, 3)
    return err or report_page(request)


@api.put("/admin/report/check", auth=auth)
def admin_report_check(request):
    err = role_error(request, 3)
    if err: return err
    data = body(request); ids = data.get("id", []) if isinstance(data.get("id"), list) else [data.get("id")]
    ids = [x for x in ids if x is not None]
    changed = Report.objects.filter(id__in=ids).update(status=data.get("status"), remark=data.get("remark"))
    return response(success("审核成功！", changed))


# The PUT route must be registered before GET "/admin/report/{id}": the
# {id} pattern also matches the literal "update", and Ninja picks the first
# URL match, so a later registration would end up 405 on PUT.
@api.put("/admin/report/update", auth=auth)
def admin_report_update(request):
    err = role_error(request, 3)
    return err or update_report(request, on_behalf=True)


@api.get("/admin/report/{id}", auth=auth)
def admin_report_get(request, id: int):
    return report_detail(request, 3, id)


@api.get("/admin/recommend/list", auth=auth)
def admin_recommend_list(request):
    err = role_error(request, 3)
    return err or response(list_page(Recommend.objects.all().order_by("-created_at"), request, recommend_dict))


def user_list(request, committee=False):
    qs = User.objects.all().order_by("id"); keyword = request.GET.get("keyword")
    if committee:
        qs = qs.filter(type__in=(0, 4))
    if keyword: qs = qs.filter(Q(username__icontains=keyword) | Q(tel__icontains=keyword) | Q(nickname__icontains=keyword))
    return response(list_page(qs, request, user_dict))


def user_update_admin(request):
    data = body(request); user = User.all_objects.filter(pk=data.get("id")).first()
    if not user: return response(failure("用户不存在"))
    for k in ("username", "nickname", "description", "tel", "leader", "type", "parent_id"):
        if k in data: setattr(user, k, data[k])
    if data.get("password"): user.set_password(data["password"])
    user.save(); write_log(request.auth, 1, "修改用户 " + user.username)
    return response(success())


def user_create_admin(request, committee=False):
    data = body(request); values = {k: data.get(k) for k in ("username", "nickname", "description", "tel", "leader", "type") if k in data}
    values["parent_id"] = 0
    if committee:
        values["type"] = 0
    user = User(**values)
    user.set_password(data.get("password", "")); user.save()
    write_log(request.auth, 2, "创建用户用户 " + user.username)
    return response(success("添加成功"))


def user_delete_admin(request):
    ids = request_ids(request, body(request)); User.objects.filter(id__in=ids).exclude(id=1).update(deleted_at=timezone.now())
    return response(success())


def user_restore_admin(request):
    ids = request_ids(request, body(request)); User.all_objects.filter(id__in=ids).update(deleted_at=None)
    return response(success())


def user_export_admin(request):
    qs = User.objects.all() if request.auth.type == 2 else User.objects.filter(type=0)
    rows = [["账号", "名称", "密码", "修改人姓名", "修改人联系方式", "备注"]]
    rows += [[x.username, x.nickname, "初始密码为scdyz@2023，请登陆系统后修改密码，密码找回请联系省级行政部门。", x.leader, x.tel, x.description] for x in qs]
    return xlsx_response(rows, request.auth.username + ".xlsx")


def register_user_routes(prefix, expected):
    tag = prefix.strip("/").replace("/", "_")
    committee = expected == 2
    @api.get(prefix + "/user/list", auth=auth, operation_id=tag + "_user_list")
    def _list(request):
        err = role_error(request, expected); return err or user_list(request, committee)
    @api.put(prefix + "/user/", auth=auth, operation_id=tag + "_user_update")
    def _update(request):
        err = role_error(request, expected); return err or user_update_admin(request)
    @api.put(prefix + "/user/restore", auth=auth, operation_id=tag + "_user_restore")
    def _restore(request):
        err = role_error(request, expected); return err or user_restore_admin(request)
    @api.post(prefix + "/user/", auth=auth, operation_id=tag + "_user_create")
    def _create(request):
        err = role_error(request, expected); return err or user_create_admin(request, committee)
    @api.get(prefix + "/user/export", auth=auth, operation_id=tag + "_user_export")
    def _export(request):
        err = role_error(request, expected); return err or user_export_admin(request)
    @api.delete(prefix + "/user/", auth=auth, operation_id=tag + "_user_delete")
    def _delete(request):
        err = role_error(request, expected); return err or user_delete_admin(request)
    @api.get(prefix + "/user/{id}", auth=auth, operation_id=tag + "_user_info")
    def _info(request, id: int):
        err = role_error(request, expected)
        user = User.objects.filter(pk=id).first()
        return err or response(success("获取成功", user_dict(user)))


register_user_routes("/admin", 3)
register_user_routes("/committee", 2)


@api.get("/admin/log/list", auth=auth)
def admin_log_list(request):
    err = role_error(request, 3)
    if err: return err
    qs = Logs.objects.all().order_by("-created_at")
    if request.GET.get("keyword"): qs = qs.filter(content__icontains=request.GET["keyword"])
    if request.GET.get("type") not in (None, ""): qs = qs.filter(type=request.GET["type"])
    if request.GET.get("user_id") not in (None, ""): qs = qs.filter(user_id=request.GET["user_id"])
    def item(x):
        d = model_dict(x); d["user"] = user_dict(User.objects.filter(pk=x.user_id).first()); return d
    return response(list_page(qs, request, item))


@api.get("/admin/person/list", auth=auth)
def admin_person_list(request):
    err = role_error(request, 3)
    if err: return err
    qs = Person.objects.all().order_by("id"); k = request.GET.get("keyword")
    if k: qs = qs.filter(Q(name__icontains=k) | Q(card__icontains=k))
    return response(list_page(qs, request, model_dict))


@api.put("/admin/person", auth=auth)
def admin_person_update(request):
    err = role_error(request, 3)
    if err: return err
    data = body(request); obj = Person.objects.filter(pk=data.get("id")).first()
    if not obj: return response(failure("人员不存在"))
    if "head" in data and not valid_person_head(data["head"]):
        return response(failure("头像地址必须为空，且只能使用已配置 OSS/CDN 域名的 http/https 地址"))
    for k, v in data.items():
        if k in {f.name for f in Person._meta.fields} and k not in {"id", "created_at", "updated_at"}: setattr(obj, k, v)
    obj.save(); return response(success())


@api.get("/admin/chouqian/{type}", auth=auth)
def draw_list(request, type: int):
    err = role_error(request, 3)
    return err or response(success("获取成功！", [model_dict(x) for x in Draw.objects.filter(type=type).order_by("index")]))


@api.put("/admin/chouqian/update", auth=auth)
def draw_update(request):
    err = role_error(request, 3)
    if err: return err
    with transaction.atomic():
        for item in body(request).get("data", []): Draw.objects.filter(pk=item.get("id")).update(index=item.get("order_index"))
    return response(success("操作成功！", None))


@api.get("/admin/chouqian/export/{type}", auth=auth)
def draw_export(request, type: int):
    err = role_error(request, 3)
    if err:
        return err
    write_log(request.auth, 6, f"导出抽签类别 {type}")
    return draw_response(Draw.objects.filter(type=type).order_by("index"), type)


@api.get("/admin/chouqian/exportall", auth=auth)
def draw_export_all(request):
    err = role_error(request, 3)
    if err:
        return err
    grouped = {
        draw_type: Draw.objects.filter(type=draw_type).order_by("index")
        for draw_type in (1, 2, 3, 4)
    }
    write_log(request.auth, 6, "导出所有抽签类别")
    return draw_all_response(grouped)


@api.get("/admin/export/data1", auth=auth)
def admin_export_data1(request):
    err = role_error(request, 3)
    if err:
        return err
    write_log(request.auth, 6, "管理员导出数据1")
    return admin_data1_response(Report.objects.all().order_by("id"))


@api.get("/admin/export/data2", auth=auth)
def admin_export_data2(request):
    err = role_error(request, 3)
    if err:
        return err
    write_log(request.auth, 6, "管理员导出数据2")
    return admin_data2_response(Report.objects.all().order_by("id"))


@api.get("/committee/index/total", auth=auth)
def committee_total(request):
    err = role_error(request, 2); return err or response(success("获取成功！", stats_admin(request)))


@api.get("/committee/report/list", auth=auth)
def committee_report_list(request):
    err = role_error(request, 2); return err or report_page(request, descending=True)


@api.put("/committee/report/check", auth=auth)
def committee_report_check(request):
    err = role_error(request, 2)
    if err: return err
    data = body(request); ids = data.get("id", []) if isinstance(data.get("id"), list) else [data.get("id")]
    changed = Report.objects.filter(id__in=[x for x in ids if x is not None]).update(status=data.get("status"), remark=data.get("remark"))
    return response(success("审核成功！", changed))


# Same ordering constraint as the admin routes: PUT before GET "/{id}".
@api.put("/committee/report/update", auth=auth)
def committee_report_update(request):
    err = role_error(request, 2)
    return err or update_report(request, on_behalf=True)


@api.get("/committee/report/{id}", auth=auth)
def committee_report_get(request, id: int):
    return report_detail(request, 2, id)


@api.get("/committee/recommend/list", auth=auth)
def committee_recommend_list(request):
    err = role_error(request, 2); return err or response(list_page(Recommend.objects.all().order_by("-created_at"), request, recommend_dict))


@api.get("/committee/online/list", auth=auth)
def committee_online_list(request):
    err = role_error(request, 2); return err or live_page(request)


def scoped_total(request, expected, kind):
    err = role_error(request, expected)
    if err: return err
    uid = request.auth.id
    if kind == "city":
        groups = [("小学组", "小学组"), ("中学组", "中学组")]
        data = []
        for group, name in groups:
            qs = Report.objects.filter(user_id=uid, group=group)
            data.append({"name": name, "total": qs.count(), "data1": qs.filter(status=-1).count(), "data2": qs.filter(status=0).count(), "data3": qs.filter(status=1).count()})
        return response(success("获取成功！", {"success": {"elementary": 0, "teacher": 0}, "data": data}))
    if kind == "school":
        qs = Report.objects.filter(user_id=uid, group="大学组")
        data = [{"name": "大学组", "total": qs.count(), "data1": qs.filter(status=-1).count(), "data2": qs.filter(status=0).count(), "data3": qs.filter(status=1).count()}]
        return response(success("获取成功！", {"success": {"colleges": 0, "teacher": 0, "colleges1": 0}, "data": data}))
    groups = [(0, "中小学组节目统计"), (1, "大学组节目统计"), (2, "中小学教师组节目统计"), (3, "高校教师组节目统计")]
    data = []
    for group, name in groups:
        qs = Report.objects.filter(user_id=uid, group=str(group)); data.append({"name": name, "total": qs.count(), "data1": qs.filter(status=-1).count(), "data2": qs.filter(status=0).count(), "data3": qs.filter(status=1).count()})
    counts = {str(g): Report.objects.filter(user_id=uid, group=str(g)).count() for g, _ in groups}
    return response(success("获取成功！", {"success": {"elementary": counts["0"], "colleges": counts["1"], "teacher": counts["2"], "teacher1": counts["3"]}, "limit": {"elementary": 1, "colleges": 1, "teacher": 1, "teacher1": 1}, "data": data}))


@api.get("/city/index/total", auth=auth)
def city_total(request): return scoped_total(request, 1, "city")


@api.get("/city/index/percent", auth=auth)
def city_percent(request):
    err = role_error(request, 1)
    if err: return err
    uid = request.auth.id; elementary = Report.objects.filter(user_id=uid, group="0").count(); middle = Report.objects.filter(user_id=uid, group="0", group_type=1).count() if hasattr(Report, "group_type") else 0
    ratio = (middle / elementary) if elementary else 0
    return response(success("获取成功！", {"data": [{"require": "中学组数量不低于中小学组报送总数40%", "pass": 1, "data": ["中学组数量所在比:" + str(round(ratio * 100, 2)) + "%"]}, {"require": "中小学组同一学校只能报送1个", "pass": 1, "data": []}, {"require": "中小学教师组同一个县（区）只能报送1个", "pass": 1, "data": []}]}))


@api.get("/school/index/total", auth=auth)
def school_total(request): return scoped_total(request, 0, "school")


@api.get("/school/index/percent", auth=auth)
def school_percent(request):
    err = role_error(request, 0)
    return err or response(success("获取成功！", {"data": []}))


@api.get("/province/index/total", auth=auth)
def province_total(request): return scoped_total(request, 4, "province")


@api.get("/province/index/percent", auth=auth)
def province_percent(request):
    err = role_error(request, 4)
    return err or response(success("获取成功！", {"data": []}))


def _draft_error_response(error):
    field = getattr(error, "field", None)
    data = {"code": getattr(error, "code", "INVALID_DRAFT_PAYLOAD"),
            "errors": [{"field": field, "message": str(error)}] if field else []}
    return response({"code": data["code"], "msg": str(error), "data": data},
                    getattr(error, "status", 400))


def _draft_payload_body(request):
    data = body(request)
    return data.get("payload", data)


def register_draft_routes(prefix, expected, scope):
    tag = prefix.strip("/").replace("/", "_")

    @api.post(prefix + "/report/drafts", auth=auth, operation_id=tag + "_draft_create")
    def _draft_create(request):
        err = role_error(request, expected)
        if err:
            return err
        try:
            draft = create_or_get_draft(request.auth, scope, _draft_payload_body(request))
            return response(success("暂存成功", draft_summary(draft)))
        except DraftError as exc:
            return _draft_error_response(exc)

    @api.put(prefix + "/report/drafts/{draft_id}", auth=auth, operation_id=tag + "_draft_update")
    def _draft_update(request, draft_id: int):
        err = role_error(request, expected)
        if err:
            return err
        data = body(request)
        try:
            version = data.get("version")
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise DraftError("version 必须是正整数")
            draft = update_draft(request.auth, scope, draft_id, version, data.get("payload"))
            return response(success("暂存成功", draft_summary(draft)))
        except DraftError as exc:
            return _draft_error_response(exc)

    @api.get(prefix + "/report/drafts", auth=auth, operation_id=tag + "_draft_list")
    def _draft_list(request):
        err = role_error(request, expected)
        if err:
            return err
        drafts = ReportDraft.objects.filter(
            user_id=request.auth.id, scope=scope,
            state=ReportDraft.STATE_EDITING).order_by("-updated_at")
        return response(success("获取成功", {
            "data": [draft_summary(item) for item in drafts], "count": drafts.count()
        }))

    @api.get(prefix + "/report/drafts/{draft_id}", auth=auth, operation_id=tag + "_draft_get")
    def _draft_get(request, draft_id: int):
        err = role_error(request, expected)
        if err:
            return err
        draft = ReportDraft.objects.filter(id=draft_id, user_id=request.auth.id, scope=scope).first()
        if not draft:
            return _draft_error_response(DraftNotFound("草稿不存在"))
        try:
            data = draft_summary(draft)
            data["payload"] = decode_payload(draft.payload)
            return response(success("获取成功", data))
        except DraftError as exc:
            return _draft_error_response(exc)

    @api.post(prefix + "/report/drafts/{draft_id}/submit", auth=auth, operation_id=tag + "_draft_submit")
    def _draft_submit(request, draft_id: int):
        err = role_error(request, expected)
        if err:
            return err
        data = body(request)
        try:
            version = data.get("version")
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise DraftError("version 必须是正整数")
            with transaction.atomic():
                draft = ReportDraft.objects.select_for_update().filter(
                    id=draft_id, user_id=request.auth.id, scope=scope).first()
                if not draft:
                    raise DraftNotFound("草稿不存在")
                if draft.state == ReportDraft.STATE_SUBMITTED:
                    if draft.report_id is None:
                        raise DraftIntegrityError("已提交草稿缺少 report_id")
                    return response(success("提交成功", {
                        **draft_summary(draft), "report_id": str(draft.report_id)
                    }))
                if draft.version != version:
                    raise DraftConflict("草稿已在其他页面更新", {"server_version": draft.version})
                submission = parse_submission_payload(decode_payload(draft.payload), request.auth.id, scope)
                if draft.report_id is None:
                    report = create_report_from_submission(user=request.auth, submission=submission,
                                                           scope=scope)
                else:
                    report = Report.objects.select_for_update().filter(
                        pk=draft.report_id, user_id=request.auth.id).first()
                    if not report:
                        raise DraftNotFound("关联报名不存在")
                    report = update_rejected_report_from_submission(
                        user=request.auth, report=report, scope=scope, submission=submission)
                draft.report_id = report.id
                draft.state = ReportDraft.STATE_SUBMITTED
                draft.submitted_at = timezone.now()
                draft.updated_at = timezone.now()
                draft.save(update_fields=["report_id", "state", "submitted_at", "updated_at"])
                return response(success("提交成功", {
                    **draft_summary(draft), "report_id": str(report.id),
                    "report_status": report.status,
                }))
        except DraftError as exc:
            return _draft_error_response(exc)
        except (ValueError, ValidationError) as exc:
            return _draft_error_response(SubmissionError(str(exc)))

    @api.post(prefix + "/reports/{report_id}/edit-draft", auth=auth,
              operation_id=tag + "_report_edit_draft")
    def _edit_draft(request, report_id: int):
        err = role_error(request, expected)
        if err:
            return err
        try:
            with transaction.atomic():
                report = Report.objects.select_for_update().filter(
                    pk=report_id, user_id=request.auth.id).first()
                if not report:
                    raise DraftNotFound("报名不存在")
                if report.status != -1:
                    raise ReportNotRejected("当前报名不是驳回状态")
                draft = ReportDraft.objects.select_for_update().filter(
                    report_id=report.id, user_id=request.auth.id, scope=scope).first()
                payload = encode_payload(build_payload_from_report(report))
                if draft is None:
                    draft = ReportDraft.objects.create(
                        user_id=request.auth.id, scope=scope, report_id=report.id,
                        payload=payload, state=ReportDraft.STATE_EDITING)
                else:
                    draft.payload = payload
                    draft.state = ReportDraft.STATE_EDITING
                    draft.version = F("version") + 1
                    draft.updated_at = timezone.now()
                    draft.save(update_fields=["payload", "state", "version", "updated_at"])
                    draft.refresh_from_db()
                data = draft_summary(draft)
                data["payload"] = decode_payload(draft.payload)
                return response(success("进入编辑成功", data))
        except DraftError as exc:
            return _draft_error_response(exc)


def register_scope_routes(prefix, expected, province=False):
    tag = prefix.strip("/").replace("/", "_")
    @api.get(prefix + "/report/list", auth=auth, operation_id=tag + "_report_list")
    def _list(request):
        err = role_error(request, expected); return err or report_page(request, request.auth)
    @api.post(prefix + "/report/create", auth=auth, operation_id=tag + "_report_create")
    def _create(request):
        err = role_error(request, expected); return err or create_report(request, province)
    @api.put(prefix + "/report/update", auth=auth, operation_id=tag + "_report_update")
    def _update(request):
        err = role_error(request, expected); return err or update_report(request)
    @api.delete(prefix + "/report/delete/{id}", auth=auth, operation_id=tag + "_report_delete")
    def _delete(request, id: int):
        err = role_error(request, expected)
        if err: return err
        report = Report.objects.filter(pk=id).first()
        if not report: return response(failure("未找到相关信息！", None))
        if report.user_id != request.auth.id:
            return response(failure("不具备该报表删除权限！", None))
        report.delete(); write_log(request.auth, 3, "删除节目报名表 " + str(report.name)); return response(success("删除成功!", None))
    @api.get(prefix + "/report/{id}", auth=auth, operation_id=tag + "_report_get")
    def _get(request, id: int):
        err = role_error(request, expected)
        report = Report.objects.filter(pk=id).first()
        if err: return err
        if not report: return response(success("获取成功！", None))
        if report.user_id != request.auth.id:
            return response(failure("不具备该报表查看权限！", None))
        result = report_dict(report)
        if province:
            name = str(report.name or "")
            parts = name.split("+"); result["name1"] = parts[0]
            if len(parts) > 1: result["name2"] = parts[1]
            origin = getattr(report, "origin", None); territory = getattr(report, "territory", None)
            if origin is not None: result.update(_map_pair("origin", origin))
            if territory is not None: result.update(_map_pair("territory", territory))
        return response(success("获取成功！", result))
    @api.get(prefix + "/recommend/list", auth=auth, operation_id=tag + "_recommend_list")
    def _recommend_list(request):
        err = role_error(request, expected)
        if err: return err
        return response(list_page(Recommend.objects.filter(user_id=request.auth.id).order_by("-created_at"), request, recommend_dict))
    @api.post(prefix + "/recommend/cau", auth=auth, operation_id=tag + "_recommend_create_update")
    def _recommend_cau(request):
        err = role_error(request, expected)
        if err: return err
        data = body(request); obj, created = Recommend.objects.get_or_create(user_id=request.auth.id)
        for k, v in data.items():
            if k in {"file", "status", "remark"}: setattr(obj, k, v)
        obj.save(); return response(success("操作成功！", None))


def _map_pair(prefix, value):
    try: value = int(value)
    except (TypeError, ValueError): return {}
    mapping = {0: (0, None), 1: (1, None), 2: (0, 2), 3: (1, 2), 4: (0, 4), 5: (1, 4)}
    first, second = mapping.get(value, (None, None)); result = {}
    if first is not None: result[prefix + "1"] = first
    if second is not None: result[prefix + "2"] = second
    return result


register_draft_routes("/city", 1, ReportDraft.SCOPE_CITY)
register_draft_routes("/school", 0, ReportDraft.SCOPE_SCHOOL)
register_scope_routes("/city", 1)
register_scope_routes("/school", 0)
register_scope_routes("/province", 4, True)


@api.get("/ticket/list")
def ticket_list(request):
    qs = Ticket.objects.filter(type_name=request.GET.get("type_name"))
    if request.GET.get("time"): qs = qs.filter(time=request.GET["time"])
    result = []
    for ticket in qs:
        d = model_dict(ticket); d["subscribe"] = [model_dict(x) for x in TicketSubscribe.objects.filter(ticket_id=ticket.id)]; result.append(d)
    return response(success("获取成功！", result))


@api.get("/ticket/message/{code}")
def ticket_message(request, code: str):
    item = TicketSubscribe.objects.filter(code=code).first()
    if not item: return response(failure("未找到相关信息！"))
    d = model_dict(item); d["ticket"] = model_dict(Ticket.objects.filter(pk=item.ticket_id).first()); return response(success("获取成功！", d))


@api.get("/ticket/my")
def my_ticket(request):
    qs = TicketSubscribe.objects.filter(name=request.GET.get("name"), card=request.GET.get("card")).order_by("ticket_id")
    result = []
    for item in qs:
        d = model_dict(item); d["ticket"] = model_dict(Ticket.objects.filter(pk=item.ticket_id).first()); result.append(d)
    return response(success("获取成功！", result))


@api.post("/ticket/make")
def make_ticket(request):
    data = body(request)
    with transaction.atomic():
        # This is the original fixed closing time; deployments may disable this
        # check only through an explicit business change review.
        try:
            if timezone.now() < timezone.make_aware(datetime(2024, 11, 14, 20, 0, 0)):
                return response(failure("未到预约时间！"))
        except ValueError:
            pass
        if TicketSubscribe.objects.select_for_update().filter(ticket_id=data.get("ticket_id"), card=data.get("card")).exists():
            return response(failure("已预约成功该场观展！"))
        ticket = Ticket.objects.select_for_update().filter(pk=data.get("ticket_id")).first()
        if not ticket: return response(failure("票不存在！"))
        if TicketSubscribe.objects.filter(ticket_id=ticket.id).count() >= ticket.number:
            return response(failure("已预约满！"))
        obj = TicketSubscribe.objects.create(ticket_id=ticket.id, name=data.get("name", ""), card=data.get("card", ""), phone=data.get("phone", ""), ip=request.META.get("REMOTE_ADDR", ""), code=new_code())
    return response(success("预约成功！", model_dict(obj)))


# --- 阿里云 OSS 直传（方案 B：STS 临时凭证）---
# 前端拿到响应后用 ali-oss 直传 bucket，服务器只签发短期凭证，不经手文件流。
OSS_BIZ_RULES = {
    # biz: (中文名, 大小上限字节, 允许的 content type)
    "video": ("视频", 700 * 1024 * 1024, {"video/mp4", "video/quicktime"}),
    "image": ("图片", 1 * 1024 * 1024, {"image/jpeg", "image/png"}),
    "photo": ("照片", 20 * 1024 * 1024, {"image/jpeg", "image/tiff"}),
    "spectrum": ("曲谱", 20 * 1024 * 1024, {"application/pdf"}),
    "doc": ("文件", 20 * 1024 * 1024, {"application/pdf"}),
}
OSS_STS_ACTIONS = ["oss:PutObject", "oss:AbortMultipartUpload",
                   "oss:ListParts", "oss:ListMultipartUploads"]


def oss_configured():
    return bool(settings.ALIYUN_OSS_ACCESS_KEY_ID
                and settings.ALIYUN_OSS_ACCESS_KEY_SECRET
                and settings.ALIYUN_OSS_BUCKET
                and settings.ALIYUN_OSS_STS_ROLE_ARN)


def _assume_oss_role(session_policy):
    """用长期 RAM 密钥换取一次上传用的 STS 临时凭证（AssumeRole）。

    SDK 懒加载，未安装时只有在真正调用该接口才会报错（与 qiniu_token 一致）。
    """
    from aliyunsdkcore.client import AcsClient
    from aliyunsdksts.request.v20150401 import AssumeRoleRequest
    # STS 服务端点按 region id（cn-chengdu）拼，去掉 SDK 风格的 oss- 前缀
    region_id = settings.ALIYUN_OSS_REGION
    if region_id.startswith("oss-"):
        region_id = region_id[len("oss-"):]
    client = AcsClient(settings.ALIYUN_OSS_ACCESS_KEY_ID,
                       settings.ALIYUN_OSS_ACCESS_KEY_SECRET,
                       region_id)
    req = AssumeRoleRequest.AssumeRoleRequest()
    req.set_accept_format("json")
    req.set_RoleArn(settings.ALIYUN_OSS_STS_ROLE_ARN)
    req.set_RoleSessionName("ylb-upload-" + uuid.uuid4().hex[:12])
    req.set_DurationSeconds(settings.ALIYUN_OSS_STS_EXPIRE)
    req.set_Policy(json.dumps(session_policy))
    return json.loads(client.do_action_with_exception(req).decode("utf-8"))


def oss_public_host():
    """对外访问域名：优先 ALIYUN_OSS_HOST，否则按 bucket+endpoint 推导（容忍带协议的 endpoint）。"""
    endpoint = settings.ALIYUN_OSS_ENDPOINT
    for scheme in ("https://", "http://"):
        if endpoint.startswith(scheme):
            endpoint = endpoint[len(scheme):]
    return settings.ALIYUN_OSS_HOST or f"https://{settings.ALIYUN_OSS_BUCKET}.{endpoint}"


@api.post("/oss/token", auth=auth)
def oss_token(request):
    data = body(request)
    if not oss_configured():
        return response(failure("阿里云OSS服务未配置"))
    biz = data.get("biz", "")
    if biz not in OSS_BIZ_RULES:
        return response(failure("未知的业务类型"))
    biz_name, max_bytes, allowed_types = OSS_BIZ_RULES[biz]
    try:
        file_size = int(data.get("fileSize") or 0)
    except (TypeError, ValueError):
        return response(failure("fileSize不合法"))
    if file_size <= 0 or file_size > max_bytes:
        return response(failure(f"{biz_name}大小不能超过{max_bytes // (1024 * 1024)}MB"))
    content_type = str(data.get("contentType") or "")
    if content_type not in allowed_types:
        return response(failure("不支持的文件类型"))
    # Key 由后端生成：{biz}/{YYYYMMDD}/{uuid4}{ext}，全局唯一避免互相覆盖。
    filename = str(data.get("filename") or "")
    dot = filename.rfind(".")
    ext = ""
    if dot >= 0:
        candidate = filename[dot:].lower()
        if 1 <= len(candidate) <= 9 and candidate[1:].isalnum():
            ext = candidate
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"{biz}/{today}/"
    key = f"{prefix}{uuid.uuid4().hex}{ext}"
    # STS 会话策略收敛到本次上传的目录前缀，只给上传相关动作。
    session_policy = {"Version": "1", "Statement": [{
        "Effect": "Allow", "Action": OSS_STS_ACTIONS,
        "Resource": [f"acs:oss:*:*:{settings.ALIYUN_OSS_BUCKET}/{prefix}*"],
    }]}
    try:
        creds = _assume_oss_role(session_policy)["Credentials"]
    except Exception:
        write_log(request.auth, 5, "发放OSS上传凭证失败")
        return response(failure("获取上传凭证失败，请稍后重试"))
    host = oss_public_host()
    return response(success("", {
        "accessKeyId": creds["AccessKeyId"],
        "accessKeySecret": creds["AccessKeySecret"],
        "securityToken": creds["SecurityToken"],
        "expiration": creds["Expiration"],
        "region": settings.ALIYUN_OSS_REGION,
        "bucket": settings.ALIYUN_OSS_BUCKET,
        "endpoint": settings.ALIYUN_OSS_ENDPOINT,
        "host": host,
        "key": key,
    }))


# --- 小文件后端代理上传：前端 multipart 交文件，服务器校验后直传 OSS ---
# 大文件/视频不走这里：700MB 级别必须由前端持 STS 凭证直传（POST /api/oss/token）。
OSS_EXT_RULES = {
    # 代理上传按扩展名校验：浏览器对 tiff 等类型常给出空 MIME，不可靠
    "image": {".jpg", ".jpeg", ".png"},
    "photo": {".jpg", ".jpeg", ".tif", ".tiff"},
    "spectrum": {".pdf"},
    "doc": {".pdf"},
}


def oss_proxy_ready():
    return bool(settings.ALIYUN_OSS_ACCESS_KEY_ID
                and settings.ALIYUN_OSS_ACCESS_KEY_SECRET
                and settings.ALIYUN_OSS_BUCKET)


def _oss_bucket():
    from oss2 import Auth, Bucket
    auth = Auth(settings.ALIYUN_OSS_ACCESS_KEY_ID, settings.ALIYUN_OSS_ACCESS_KEY_SECRET)
    return Bucket(auth, settings.ALIYUN_OSS_ENDPOINT, settings.ALIYUN_OSS_BUCKET)


@api.post("/oss/upload", auth=auth)
def oss_upload(request):
    if not oss_proxy_ready():
        return response(failure("阿里云OSS服务未配置"))
    biz = request.POST.get("biz", "")
    if biz not in OSS_BIZ_RULES:
        return response(failure("未知的业务类型"))
    if biz == "video":
        return response(failure("视频请使用前端直传：POST /api/oss/token"))
    f = request.FILES.get("file")
    if not f:
        return response(failure("缺少文件"))
    biz_name, max_bytes, allowed_types = OSS_BIZ_RULES[biz]
    if f.size > max_bytes:
        return response(failure(f"{biz_name}大小不能超过{max_bytes // (1024 * 1024)}MB"))
    dot = f.name.rfind(".")
    ext = f.name[dot:].lower() if dot >= 0 else ""
    if ext not in OSS_EXT_RULES[biz]:
        return response(failure("不支持的文件类型"))
    # key 生成规则与 /api/oss/token 一致：{biz}/{YYYYMMDD}/{uuid4}{ext}
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"{biz}/{today}/"
    key = f"{prefix}{uuid.uuid4().hex}{ext}"
    headers = {"Content-Type": f.content_type} if f.content_type in allowed_types else None
    try:
        bucket = _oss_bucket()
        if headers:
            bucket.put_object(key, f, headers=headers)
        else:
            bucket.put_object(key, f)
    except Exception:
        write_log(request.auth, 5, "OSS代理上传失败")
        return response(failure("上传失败，请稍后重试"))
    return response(success("", {
        "url": f"{oss_public_host()}/{key}",
        "key": key,
        "filename": f.name,
        "size": f.size,
    }))


# --- Deployment health probe -------------------------------------------------
#
# Consumed by the automated deployment pipeline (see
# .github/workflows/deploy.yml). The route is unauthenticated, so its body must
# stay a fixed string and never echo configuration back to the caller.

logger = logging.getLogger(__name__)


def _health(request):
    """Answer 200 only when the app serves *and* the configured database answers.

    A bare liveness probe would also return 200 when ``DB_*`` in the server
    ``.env`` has drifted, which is precisely the failure a deployment health
    check exists to catch. Details of a failed probe go to the server log only:
    the response body is a constant, so a driver error quoting the database
    user or host cannot reach a public caller or a CI log.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        logger.exception("health check: database probe failed")
        raise HttpError(503, "database unavailable")
    return {"status": "ok"}


# Registered under both spellings: the pipeline calls ``/api/health`` to match
# the trailing-slash-free convention of the other routes here, while
# ``/api/health/`` keeps a manual ``curl`` from silently 404ing.
api.get("/health", operation_id="health")(_health)
api.get("/health/", operation_id="health_with_trailing_slash")(_health)
