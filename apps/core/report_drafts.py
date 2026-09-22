import json
from dataclasses import dataclass
from typing import Optional, Tuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Files, Person, Report, ReportDraft, ReportPerson
from .services import attach_report_people, store_people

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
    result["position"] = _integer(item.get("position"), "person[%s].position" % index, True, 0)
    result["type"] = _integer(item.get("type"), "person[%s].type" % index, True, 0)
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
            result[field] = _string(value, field)
    people = raw.get("person", [])
    if not isinstance(people, list):
        raise DraftError("person 必须是数组")
    result["person"] = [_person(item, i) for i, item in enumerate(people)]
    return result


def parse_submission_payload(raw, user_id, scope=None):
    value = normalize_draft_payload(raw)
    required = ("choir_name", "name", "group", "establishment", "contact_name", "contact_phone")
    for field in required:
        value[field] = _string(value.get(field), field, True)
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
    normalized = normalize_draft_payload(raw)
    payload = encode_payload(normalized)
    with transaction.atomic():
        draft = ReportDraft.objects.select_for_update().filter(
            user_id=user.id, scope=scope, state=ReportDraft.STATE_EDITING
        ).order_by("id").first()
        if draft is None:
            return ReportDraft.objects.create(user_id=user.id, scope=scope, payload=payload)
        draft.payload = payload
        draft.version += 1
        draft.updated_at = timezone.now()
        draft.save(update_fields=["payload", "version", "updated_at"])
        return draft


def update_draft(user, scope, draft_id, version, raw):
    normalized = normalize_draft_payload(raw)
    payload = encode_payload(normalized)
    updated = ReportDraft.objects.filter(
        id=draft_id, user_id=user.id, scope=scope,
        state=ReportDraft.STATE_EDITING, version=version,
    ).update(payload=payload, version=F("version") + 1, updated_at=timezone.now())
    if updated != 1:
        if not ReportDraft.objects.filter(id=draft_id, user_id=user.id, scope=scope).exists():
            raise DraftNotFound("草稿不存在")
        raise DraftConflict("草稿已在其他页面更新")
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
