import json
from dataclasses import dataclass
from typing import Optional, Tuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Files, Person, Report, ReportDraft, ReportPerson, User, normalize_card
from .services import attach_report_people, store_people

# 正式报名数上限（未软删；驳回态 status=-1 与待审核态 status=0 同样占额度，
# 只有软删除后才腾出名额，与 Report.objects 的 SoftDeleteManager 一致）。
#
# 【2026-09-27 口径】高校端（scope 0）与中小学端（scope 5）统一回到红头文件原口径
# 「每所学校限报一支队伍，且只能参加一个组别」；上限不再取本表，而由账号级特许
# users.can_report_twice 决定（False=1 支，True=2 支，授予中小学合并办学的学校），
# 见 ACCOUNT_QUOTA_SCOPES 与 assert_report_quota。
#
# 本表只留省级端（scope 4）——上一届西部音乐周的历史渠道，维持固定上限 8 的旧口径，
# 特许字段不参与。scope 1（市州端）已无写入路径（只读），不配额度。
REPORT_QUOTA_BY_SCOPE = {4: 8}
DEFAULT_REPORT_QUOTA = 1

# 走「账号总量 + 组别唯一」口径的渠道：默认每校 1 支；can_report_twice=True 时 2 支，
# 且每个组别仍限 1 支（assert_report_group_unique）。
ACCOUNT_QUOTA_SCOPES = (User.TYPE_SCHOOL, User.TYPE_PRIMARY_SECONDARY)

# 各 scope 允许报送的组别。**未列出的 scope 一律不做组别归属校验**（保持现状）。
#
# 口径依据（组委会 2026-09-23 答复，前端 HaveToRead.vue §二段 2 同步记载）：
#   · 中小学端（scope 5，原市级渠道）只能报小学组、中学组，**不得出现大学组** ——
#     大学组归高校渠道。管乐团与铜管乐团同规则（不再按乐团类型细分）。
#   · 【2026-09-27 修订】默认每校只能报 1 个组别；中小学合并办学的学校经管理员
#     授予 can_report_twice 后可报 2 支 —— 本表限死 2 个组别、账号上限为 2、
#     每组别又限 1 支，三者相乘保证两支必然是一支小学、一支中学，不可能出现「2 支小学组」。
#   · 两支的乐团类型互相独立（小学管乐团 + 中学铜管乐团是允许的）。后端
#     establishment 与 group 之间**零耦合**，本来就是自由的，无需改动。
#
# 为什么只有 scope 5：
#   · scope 1（原市级渠道）降级只读后不再有写入路径，不入表；
#   · scope 0（高校端）组委会明确要求本次**不加**校验，故不入表；
#   · scope 4（省级）是上一届西部音乐周 dist 包留下的，本届红头文件没有省级端，
#     故不入表（不入表 = 不校验，保持现状，不为历史代码写新规则）。
REPORT_ALLOWED_GROUPS = {5: ("小学组", "中学组")}

# 仅用于拼错误文案；查不到时退到「当前渠道」
REPORT_SCOPE_LABELS = {0: "高校端", 4: "省级端", 5: "中小学端"}

REPORT_FIELDS = {
    "choir_name", "name", "name1", "school_name", "desc", "group",
    "establishment", "establishment_name", "contact_name", "contact_phone",
    "contact_way", "time_length", "spectrum", "file", "dinner_reservation",
    "dinner_reservation_counts", "remark", "person",
}
SERVER_FIELDS = {"user_id", "scope", "status", "report_id", "draft_id", "version",
                 "created_at", "updated_at", "submitted_at"}
# `id` 与 `person_id` 是同一个东西：要复用的 Person 主键。
#   · `id` 是历史键名，前端 draftPayload.js 一直在发，payload_from_report() 回吐时也写 `id`；
#   · `person_id` 是新接口约定的名字。
# 两个都收（收进来后统一存在 `id` 上），否则就是半迁移状态：老前端发 `id`、
# 新前端发 `person_id`，只能有一边能用。注意这一条是**精确白名单**，
# 白名单里没有的键会让整个暂存/提交 400（见 draftPayload.js 里记载的那次事故）。
#
# 【合并说明：这里必须是两边的并集】
#   · person_id —— 来自身份证后 6 位改造（复用 Person 的显式引用，身份基准）；
#   · signature_order / display_order —— 来自 master（署名顺序、指导教师表内行下标）。
#   少任何一个都会造成「前端发得出、后端 400 判为不允许字段」，且报错文案
#   （person[N] 存在不允许字段）用户看不懂。因此白名单是 superset：
#   前端少发键是安全的（_person() 会补 None），多发未列出的键才会 400。
PERSON_FIELDS = {"id", "person_id", "name", "card", "age", "school", "phone", "gender",
                 "major", "head", "instrument", "other", "remark", "position", "type",
                 "signature_order", "display_order"}

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


def assert_group_allowed(scope, group):
    """校验该渠道能不能报这个组别。允许表见 REPORT_ALLOWED_GROUPS。

    **未配表的 scope 一律放行** —— 所以将来新增渠道时忘了配表，后果是「不校验」，
    而不是「用户全被拦死」。
    """
    if not group:
        # 组别缺失时不在这里拦：必填校验归 parse_submission_payload 的 required 列表
        return
    allowed = REPORT_ALLOWED_GROUPS.get(scope)
    if allowed is None or group in allowed:
        return
    label = REPORT_SCOPE_LABELS.get(scope, "当前渠道")
    raise InvalidSubmission("%s只能报送%s，不能报送%s" % (label, "或".join(allowed), group))


def assert_report_group_unique(user, group, exclude_report_id=None):
    """同一学校同一组别只能持有一支（未软删）报名；驳回态同样占组别名额。

    被驳回报名重新提交时可能改了组别（update_rejected_report_from_submission），
    此时传 exclude_report_id 排除自身，只防与「另一支」撞组别。
    """
    if not group:
        return
    queryset = Report.objects.filter(user_id=user.id, group=group)
    if exclude_report_id is not None:
        queryset = queryset.exclude(id=exclude_report_id)
    if queryset.exists():
        # 点明是哪个组别满了 —— 只说「限报一支」正是用户被误导的原因
        raise ReportQuotaExceeded("%s每所学校限报一支队伍，您已有报名记录" % group)


def assert_report_quota(user, scope, group=None):
    """必须在 lock_user_slot 之后、同一事务内调用。

    【本函数是「这条报名能不能建」的统一闸口】除了配额，它还顺带校验渠道与组别的归属
    （assert_group_allowed）。放这里是因为全仓只有两个调用点 —— create_report 与
    create_report_from_submission —— 都是新建报名的必经之路，加在这里两个入口自动覆盖，
    将来多一个调用点也不会漏。请注意函数名只说了 quota，组别校验是搭车的。

    【2026-09-27 口径：先卡账号总量，再卡组别唯一】
    红头文件原文「每所学校限报一支队伍，且只能参加一个组别」同时适用于高校端（scope 0）
    与中小学端（scope 5）。因此这两个渠道先做**账号总量**检查：
      · can_report_twice=False（默认）：总量 1，报过任意组别的一支后不能再报；
      · can_report_twice=True（管理员授予中小学合并办学的学校）：总量 2，且每个组别
        仍限 1 支 —— 配合 scope 5 的组别白名单（小学组/中学组），两支必然各占一个组别。
    历史上 2026-09-23~27 曾短暂放开为「每组别 1 支、组别数不限」，已收回；
    `src/components/common/HaveToRead.vue` §二段 2 与本注释必须同步。

    省级端（scope 4）及其他历史渠道维持「固定上限 + 按组别计数」的旧逻辑，
    can_report_twice 不参与（特许只发给学校账号）。
    """
    assert_group_allowed(scope, group)
    queryset = Report.objects.filter(user_id=user.id)

    if scope in ACCOUNT_QUOTA_SCOPES:
        account_limit = 2 if getattr(user, "can_report_twice", False) else 1
        if queryset.count() >= account_limit:
            if account_limit == 1:
                message = "每所学校限报一支队伍，您已有报名记录"
            else:
                message = "您的单位最多可报送两支队伍，无法继续报送"
            raise ReportQuotaExceeded(message)
        # 总量未满：再保证同一组别不出现两支（主要拦特许学校报两支同组别）
        assert_report_group_unique(user, group)
        return

    limit = REPORT_QUOTA_BY_SCOPE.get(scope, DEFAULT_REPORT_QUOTA)
    if group:
        queryset = queryset.filter(group=group)
    current = queryset.count()
    if current >= limit:
        if limit != 1:
            message = "目前您的单位已超报送限制,无法再继续进行报送!"
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
    # 统一存到 `id`：下游 store_people 只需认一个键。
    # 两个都给时以 person_id 为准（新键名优先，避免老键残留值静默覆盖新值）。
    reference = item.get("person_id", item.get("id"))
    result["id"] = str(reference) if reference not in (None, "") else None
    result["name"] = _string(item.get("name"), "person[%s].name" % index, complete)
    card = _string(item.get("card"), "person[%s].card" % index, complete)
    if complete:
        # 提交时卡死后 6 位格式；暂存（complete=False）故意放行半成品，
        # 否则用户填到一半就存不了草稿。归一（x→X）在这里做掉，
        # 保证 payload 里存的和最终落库的完全一致。
        ok, card = normalize_card(card)
        if not ok:
            raise DraftError("person[%s].card %s" % (index, card))
    result["card"] = card
    for field in ("school", "phone", "gender", "major", "head", "instrument", "other", "remark"):
        result[field] = _string(item.get(field), "person[%s].%s" % (index, field))
    result["age"] = _integer(item.get("age"), "person[%s].age" % index)
    result["position"] = _integer(item.get("position"), "person[%s].position" % index, complete, 0)
    result["type"] = _integer(item.get("type"), "person[%s].type" % index, complete, 0)
    # 署名排序：可空；填了必须 ≥1 的整数（0/负数/字符串在前端就该拦住，这里兜底 400）
    result["signature_order"] = _integer(item.get("signature_order"), "person[%s].signature_order" % index, False, 1)
    # 表内行下标：可空；填了必须 ≥0 的整数（下标从 0 起，故下限是 0 不是 1）
    result["display_order"] = _integer(item.get("display_order"), "person[%s].display_order" % index, False, 0)
    return result


# 官方用餐时段数（apps/api/registration_form.MEALS 的长度）；core 不反向依赖 api
_DINNER_SLOTS = 6


def _dinner_counts(value, field):
    """用餐预约人数（草稿路径严格校验）：null 或 ≤6 长度数组，元素 null/非负整数。

    与 dinner_reservation 并行、按下标对齐 6 个官方用餐时段，counts[i]>0 表示
    第 i 时段订 N 人。0 是合法值（明确占位）；负数/非整数/超长直接 400。
    直传 create/update_report 路径的宽松规整在 services._dinner_counts。
    """
    if value is None:
        return None
    if not isinstance(value, list):
        raise DraftError("%s 必须是数组" % field)
    if len(value) > _DINNER_SLOTS:
        raise DraftError("%s 最多 %d 项（对应 6 个用餐时段）" % (field, _DINNER_SLOTS))
    result = []
    for index, item in enumerate(value):
        if item is None:
            result.append(None)
            continue
        if isinstance(item, bool) or not isinstance(item, int):
            raise DraftError("%s[%s] 必须是 null 或整数" % (field, index))
        if item < 0:
            raise DraftError("%s[%s] 不能小于 0" % (field, index))
        result.append(item)
    result += [None] * (_DINNER_SLOTS - len(result))
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
        elif field == "dinner_reservation_counts":
            result[field] = _dinner_counts(value, field)
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
    # 与 dinner_reservation 同款：空列回显 []（前端把 null/[] 都当「未填」即可）
    result["dinner_reservation_counts"] = report.dinner_reservation_counts or []
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
            "signature_order": link.signature_order,
            "display_order": link.display_order,
        })
    return result


def create_report_from_submission(user, submission, scope=None):
    """从提交草稿创建正式 Report。

    在同一事务内先锁 user 行、再校验配额，保证并发下不会突破每校/每省限报。
    调用方（_draft_submit）已包在 transaction.atomic() 中；这里的 lock 复用
    外层事务，SQLite 下 select_for_update() 是空操作，属于验证盲区（见 P2-5）。
    """
    lock_user_slot(user.id)
    # 默认每校 1 支；can_report_twice 特许学校 2 支且每组别 1 支，见 assert_report_quota
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
    if scope in ACCOUNT_QUOTA_SCOPES:
        # 重新提交不新增行数，账号总量不变，无需再查；但用户可能在驳回后改了组别，
        # 必须防止两支（特许学校）被改成同一个组别。
        group = submission.report.get("group")
        assert_group_allowed(scope, group)
        assert_report_group_unique(user, group, exclude_report_id=report.id)
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
