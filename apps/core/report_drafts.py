import json
from dataclasses import dataclass
from typing import Optional, Tuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Files, Person, Report, ReportDraft, ReportPerson, User
from .services import attach_report_people, store_people

# 每所学校/单位可持有的（未删除）正式报名数上限；按 scope 分档：
#   0 = 校级、1 = 市级、4 = 省级（历史 create_report 里的 "8" 沿用）。
# 规则口径：驳回态（status=-1）与待审核态（status=0）同样占额度，
# 只有软删除后才腾出名额，与 Report.objects（SoftDeleteManager）一致。
REPORT_QUOTA_BY_SCOPE = {0: 1, 1: 1, 4: 8}
DEFAULT_REPORT_QUOTA = 1

REPORT_FIELDS = {
    "choir_name", "name", "name1", "school_name", "desc", "group",
    "establishment", "establishment_name", "contact_name", "contact_phone",
    "contact_way", "time_length", "spectrum", "file", "dinner_reservation",
    "remark", "person",
}
SERVER_FIELDS = {"user_id", "scope", "status", "report_id", "draft_id", "version",
                 "created_at", "updated_at", "submitted_at"}
PERSON_FIELDS = {"id", "name", "card", "age", "school", "phone", "gender",
                 "major", "head", "instrument", "other", "remark", "position", "type"}

# 单字段上限，与 Report 各列 max_length 对齐；未列出的字段沿用 255（大部分列宽）
_FIELD_LIMITS = {"desc": 1000}


class DraftError(Exception):
    code = "INVALID_DRAFT_PAYLOAD"
    status = 400

    def __init__(self, message, data=None):
        super().__init__(message)
        self.message = message
        self.data = data


class DraftNotFound(DraftError):
    code = "DRAFT_NOT_FOUND"
    status = 404


class DraftConflict(DraftError):
    code = "DRAFT_VERSION_CONFLICT"
    status = 409


class ReportNotRejected(DraftError):
    code = "REPORT_NOT_REJECTED"
    status = 409


class InvalidSubmission(DraftError):
    code = "SUBMISSION_VALIDATION_FAILED"


class SubmissionError(InvalidSubmission):
    pass


class DraftIntegrityError(DraftError):
    code = "DRAFT_DATA_INTEGRITY_ERROR"
    status = 409


class ReportQuotaExceeded(DraftError):
    code = "REPORT_QUOTA_EXCEEDED"
    status = 409


def lock_user_slot(user_id):
    """在事务内串行化同一用户的正式报名创建。SQLite 下退化为普通 SELECT。

    必须在 assert_report_quota 之前调用。使用 all_objects 以便即使账号
    被软删也能锁住那一行（正常业务不会走到，但避免 select_for_update() 空手）。
    """
    return User.all_objects.select_for_update().filter(pk=user_id).first()


def assert_report_quota(user, scope, group=None):
    """必须在 lock_user_slot 之后、同一事务内调用。

    【配额的单位是「组别」，不是「账号」】
    口径依据（不是推测，是仓库里已有的书面口径）：
    `src/components/common/HaveToRead.vue` §二段 2 的【2026-09-23 口径变更】写得很明确 ——
    红头文件原文是「每所学校限报一支队伍，且只能参加一个组别」，组委会后来**放宽**为
    「每所学校**每个组别**限报一支队伍，小学组、中学组可各报一支（最多两支），
      大学组限报一支」。同一段还要求本函数与该节**必须同步**。

    原先这里只按 user_id 计数、完全不看 group，于是同一所学校报完中学组就再也报不了
    小学组 —— 页面承诺「可各报一支」，系统却回
    `REPORT_QUOTA_EXCEEDED 每所学校限报一支队伍，您已有报名记录`，两边对不上。

    group 取不到时退回账号级计数（宁严不松，不会凭空多放行一支）。
    """
    limit = REPORT_QUOTA_BY_SCOPE.get(scope, DEFAULT_REPORT_QUOTA)
    queryset = Report.objects.filter(user_id=user.id)
    if group:
        queryset = queryset.filter(group=group)
    current = queryset.count()
    if current >= limit:
        if limit != 1:
            message = "目前您的单位已超报送限制,无法再继续进行报送!"
        elif group:
            # 点明是哪个组别满了 —— 只说「限报一支」正是用户被误导的原因
            message = "%s每所学校限报一支队伍，您已有报名记录" % group
        else:
            message = "每所学校限报一支队伍，您已有报名记录"
        raise ReportQuotaExceeded(message)


@dataclass(frozen=True)
class Submission:
    report: dict
    people: Tuple[dict, ...]


def _constant(value):
    raise ValueError("非法 JSON 数值：%s" % value)


def encode_payload(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":"))


def decode_payload(text):
    try:
        value = json.loads(text, parse_constant=_constant)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DraftError("payload 不是合法 JSON") from exc
    if not isinstance(value, dict):
        raise DraftError("payload 必须是 JSON 对象")
    return value


def _string(value, field, required=False, maximum=255):
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise DraftError("%s 必须是字符串" % field)
    value = value.strip()
    if required and not value:
        raise DraftError("%s 不能为空" % field)
    if len(value) > maximum:
        raise DraftError("%s 长度不能超过 %s" % (field, maximum))
    return value


def _integer(value, field, required=False, minimum=None):
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise DraftError("%s 必须是整数" % field)
    if minimum is not None and value < minimum:
        raise DraftError("%s 不能小于 %s" % (field, minimum))
    return value


def _entity_id(value, field):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not value.isdigit() or int(value) <= 0:
        raise DraftError("%s 必须是正整数 ID 字符串" % field)
    return int(value)


def _person(item, index, complete=False):
    if not isinstance(item, dict):
        raise DraftError("person[%s] 必须是对象" % index)
    unknown = set(item) - PERSON_FIELDS
    if unknown:
        raise DraftError("person[%s] 存在不允许字段" % index, {"fields": sorted(unknown)})
    result = {}
    result["id"] = str(item["id"]) if item.get("id") is not None else None
    result["name"] = _string(item.get("name"), "person[%s].name" % index, complete)
    result["card"] = _string(item.get("card"), "person[%s].card" % index, complete)
    for field in ("school", "phone", "gender", "major", "head", "instrument", "other", "remark"):
        result[field] = _string(item.get(field), "person[%s].%s" % (index, field))
    result["age"] = _integer(item.get("age"), "person[%s].age" % index)
    result["position"] = _integer(item.get("position"), "person[%s].position" % index, complete, 0)
    result["type"] = _integer(item.get("type"), "person[%s].type" % index, complete, 0)
    return result


def normalize_draft_payload(raw):
    if not isinstance(raw, dict):
        raise DraftError("payload 必须是 JSON 对象")
    unknown = set(raw) - REPORT_FIELDS - SERVER_FIELDS
    if unknown:
        raise DraftError("存在不允许字段", {"fields": sorted(unknown)})
    result = {}
    for field in REPORT_FIELDS - {"person"}:
        value = raw.get(field)
        if field in {"spectrum", "file"}:
            if value in (None, ""):
                result[field] = None
            else:
                _entity_id(value, field)
                result[field] = value
        elif field == "time_length":
            result[field] = _integer(value, field)
        elif field == "dinner_reservation":
            if value is not None and not isinstance(value, (list, dict)):
                raise DraftError("dinner_reservation 必须是数组或对象")
            result[field] = value
        else:
            result[field] = _string(value, field, maximum=_FIELD_LIMITS.get(field, 255))
    people = raw.get("person", [])
    if not isinstance(people, list):
        raise DraftError("person 必须是数组")
    result["person"] = [_person(item, i) for i, item in enumerate(people)]
    return result


def parse_submission_payload(raw, user_id, scope=None):
    value = normalize_draft_payload(raw)
    required = ("choir_name", "name", "group", "establishment", "contact_name", "contact_phone")
    for field in required:
        value[field] = _string(value.get(field), field, True,
                               maximum=_FIELD_LIMITS.get(field, 255))
    if value.get("time_length") is None:
        raise InvalidSubmission("time_length 不能为空")
    if value["time_length"] <= 0:
        raise InvalidSubmission("time_length 必须大于 0")
    people = tuple(_person(item, i, True) for i, item in enumerate(value["person"]))
    for field in ("file", "spectrum"):
        ident = _entity_id(value.get(field), field)
        if ident is not None and not Files.objects.filter(pk=ident, user_id=user_id).exists():
            raise InvalidSubmission("%s 文件不存在或不属于当前用户" % field)
        value[field] = ident
    value.pop("person", None)
    return Submission(value, people)


def payload_from_report(report):
    result = {field: getattr(report, field) for field in REPORT_FIELDS if field != "person"}
    result["spectrum"] = str(report.spectrum) if report.spectrum is not None else None
    result["file"] = str(report.file) if report.file is not None else None
    result["dinner_reservation"] = report.dinner_reservation or []
    result["person"] = []
    for link in ReportPerson.objects.filter(report_id=report.id):
        person = Person.objects.filter(pk=link.person_id).first()
        if not person:
            continue
        result["person"].append({
            "id": str(person.id), "name": person.name, "card": person.card,
            "age": person.age, "school": person.school, "phone": person.phone,
            "gender": person.gender, "major": person.major, "head": person.head,
            "instrument": person.instrument, "other": person.other, "remark": person.remark,
            "position": link.position, "type": link.type,
        })
    return result


def create_report_from_submission(user, submission, scope=None):
    """从提交草稿创建正式 Report。

    在同一事务内先锁 user 行、再校验配额，保证并发下不会突破每校/每省限报。
    调用方（_draft_submit）已包在 transaction.atomic() 中；这里的 lock 复用
    外层事务，SQLite 下 select_for_update() 是空操作，属于验证盲区（见 P2-5）。
    """
    lock_user_slot(user.id)
    # 按组别计配额（每校每个组别一支），见 assert_report_quota 的说明
    assert_report_quota(user, scope, submission.report.get("group"))
    ok, stored = store_people(user, list(submission.people))
    if not ok:
        raise InvalidSubmission(stored)
    report = Report.objects.create(user_id=user.id, **submission.report)
    attach_report_people(report.id, stored)
    return report


def update_rejected_report_from_submission(user, report, submission, scope=None):
    if report.user_id != user.id or report.status != -1:
        raise ReportNotRejected("当前报名状态不允许重新提交")
    ok, stored = store_people(user, list(submission.people))
    if not ok:
        raise InvalidSubmission(stored)
    for field, value in submission.report.items():
        setattr(report, field, value)
    report.status = 0
    report.save()
    attach_report_people(report.id, stored)
    return report


def create_or_get_draft(user, scope, raw):
    """新增报名的暂存：只认 report_id IS NULL 的编辑中草稿。

    绑定在具体 Report 上的 edit-draft 草稿不会被这里复用/覆盖——那两条业务线
    共用一张表但表意不同，混用会静默改写被驳回的正式报名（历史 P0）。
    """
    normalized = normalize_draft_payload(raw)
    payload = encode_payload(normalized)
    from django.db import IntegrityError
    try:
        with transaction.atomic():
            draft = ReportDraft.objects.select_for_update().filter(
                user_id=user.id, scope=scope, state=ReportDraft.STATE_EDITING,
                report_id__isnull=True,
            ).order_by("id").first()
            if draft is None:
                return ReportDraft.objects.create(user_id=user.id, scope=scope, payload=payload)
            draft.payload = payload
            draft.version += 1
            draft.updated_at = timezone.now()
            draft.save(update_fields=["payload", "version", "updated_at"])
            return draft
    except IntegrityError:
        # 并发下另一请求先插入了同一 (user,scope,new) 槽位——让前端重新拉草稿
        raise DraftConflict("草稿已在其他页面创建，请刷新")


def update_draft(user, scope, draft_id, version, raw):
    normalized = normalize_draft_payload(raw)
    payload = encode_payload(normalized)
    updated = ReportDraft.objects.filter(
        id=draft_id, user_id=user.id, scope=scope,
        state=ReportDraft.STATE_EDITING, version=version,
    ).update(payload=payload, version=F("version") + 1, updated_at=timezone.now())
    if updated != 1:
        current = ReportDraft.objects.filter(id=draft_id, user_id=user.id, scope=scope).first()
        if current is None:
            raise DraftNotFound("草稿不存在")
        if current.state != ReportDraft.STATE_EDITING:
            raise DraftConflict("草稿已提交，不能更新")
        raise DraftConflict("草稿已在其他页面更新", {"server_version": current.version})
    return ReportDraft.objects.get(id=draft_id)


def build_payload_from_report(report):
    return payload_from_report(report)


def draft_data(draft, include_payload=True):
    data = {
        "draft_id": str(draft.id),
        "report_id": str(draft.report_id) if draft.report_id is not None else None,
        "version": draft.version,
        "state": draft.state,
        "updated_at": draft.updated_at.isoformat(),
    }
    if include_payload:
        data["payload"] = decode_payload(draft.payload)
    return data


def draft_summary(draft):
    return draft_data(draft, include_payload=False)
