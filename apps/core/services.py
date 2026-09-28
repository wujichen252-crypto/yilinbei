import json
import re
import secrets
from datetime import datetime
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (Files, Leader, Logs, Person, PersonalAccessToken,
                     ReportPerson, User, normalize_card)


def success(msg="操作成功!", data=None):
    return {"code": 0, "msg": msg, "data": data}


def failure(msg="操作失败!", data=None):
    return {"code": 1, "msg": msg, "data": data}


def page_response(items, count, msg="", code=0):
    return {"data": items, "count": count, "code": code, "msg": msg}


class BodyError(Exception):
    """请求体是合法 JSON 但不是对象（如 "abc"、[1,2]、3）。views 层负责回 400。"""


def parse_body(request):
    if request.body:
        try:
            value = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return request.POST.dict()
        if not isinstance(value, dict):
            raise BodyError("请求体必须是 JSON 对象")
        return value
    return request.POST.dict()


def model_dict(obj, fields=None):
    if obj is None:
        return None
    names = fields or [f.name for f in obj._meta.fields]
    result = {}
    for name in names:
        value = getattr(obj, name, None)
        if hasattr(value, "isoformat"):
            value = value.isoformat(sep=" ")
        result[name] = value
    return result


def user_dict(user):
    if not user:
        return None
    return {"id": user.id, "username": user.username, "nickname": user.nickname,
            "description": user.description or "", "tel": user.tel or "",
            "leader": user.leader or "", "type": user.type,
            "can_report_twice": bool(getattr(user, "can_report_twice", False)),
            "parent_id": user.parent_id}


def subordinate_school_ids(city_user):
    """归属于该市州账号的中小学账号（type=5）id 列表。

    市州端查看下级学校报名情况的统一数据范围来源：parent_id 指向该市州账号。
    """
    return list(
        User.objects.filter(
            parent_id=city_user.id, type=User.TYPE_PRIMARY_SECONDARY
        ).values_list("id", flat=True)
    )


def list_page(queryset, request, serializer=model_dict):
    try:
        limit = max(1, int(request.GET.get("limit", 10)))
        number = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        limit, number = 10, 1
    count = queryset.count()
    offset = limit * (number - 1)
    # Laravel's paginate/skip+take contract returns an empty data array for an
    # out-of-range page. Django Paginator.get_page() instead clamps to the last
    # page, which breaks the existing frontend behavior.
    items = queryset[offset:offset + limit]
    return page_response([serializer(item) for item in items], count)


def write_log(user, action_type, content):
    Logs.objects.create(user_id=user.id if user else 1, type=action_type, content=str(content)[:5000])


def _configured_person_head_hosts():
    """Return normalized exact hosts and explicitly delegated subdomains."""
    configured = list(getattr(settings, "PERSON_HEAD_ALLOWED_DOMAINS", []))
    configured += list(getattr(settings, "PERSON_HEAD_CDN_DOMAINS", []))
    configured += [getattr(settings, "QINIU_DOMAIN", ""), getattr(settings, "ALIYUN_OSS_HOST", "")]
    endpoint = getattr(settings, "ALIYUN_OSS_ENDPOINT", "")
    bucket = getattr(settings, "ALIYUN_OSS_BUCKET", "")
    if endpoint and bucket:
        configured.append(f"{bucket}.{endpoint}")

    exact = set()
    subdomains = set()
    for value in configured:
        raw = str(value or "").strip().rstrip("/")
        if not raw:
            continue
        parsed = urlsplit(raw if "://" in raw else f"//{raw}")
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            continue
        if raw.startswith("."):
            subdomains.add(host.lstrip("."))
        else:
            exact.add(host)
    return exact, subdomains


def valid_person_head(value):
    """Allow no avatar, or an HTTP(S) URL hosted by configured OSS/CDN hosts."""
    if value in (None, ""):
        return True
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not value:
        return True
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return False
    exact, subdomains = _configured_person_head_hosts()
    host = parsed.hostname.lower().rstrip(".")
    return host in exact or any(host == domain or host.endswith("." + domain)
                                for domain in subdomains)


def _person_head_error(value):
    return None if valid_person_head(value) else "头像地址必须为空，且只能使用已配置 OSS/CDN 域名的 http/https 地址"


# Person 上不参与「原样批量拷贝」的字段：
#   · id / created_at / updated_at —— 主键与时间戳；
#   · name —— 走下面显式赋值，用的是已 strip 且非空的校验值，而不是 payload 原值；
#   · user_id —— 归属单位，**只能由服务端决定**。放进来会让
#     `POST /report {"person":[{"person_id": <他校人员>, "user_id": 999}]}`
#     把别人的 Person 直接过户到自己名下，从而合法复用 —— 这是越权，不是便利。
#
# 注意 name 在这里**不等于**「客户端不可改」：新建和复用都会应用客户端提交的姓名，
# 只是必须先过校验/清洗。真正不可改的只有 user_id。
_PERSON_CLIENT_READONLY = {"id", "name", "user_id", "created_at", "updated_at"}


def _person_reference(item):
    """取出客户端显式声明的 Person 主键。返回 (True, pid_or_None) 或 (False, 错误文案)。

    同时接受 `person_id` 与历史键名 `id`：前端 draftPayload.js 一直发的就是 `id`
    （后端 PERSON_FIELDS 白名单里也只有 `id`），而新接口约定叫 person_id。
    两个都认是为了不制造半迁移状态。

    **本批内不做任何「同 card 复用同一行」的合并** —— 同一个后 6 位在这批里出现两次，
    只要都没带 person_id，就必须老老实实建两条 Person（第 7、8 条冻结规则）。
    """
    raw = item.get("person_id", item.get("id"))
    if raw in (None, ""):
        return True, None
    if isinstance(raw, bool):
        # bool 是 int 的子类，True 会被当成 1 —— 那会指到 ID=1 的人身上
        return False, "person_id 必须是正整数"
    if isinstance(raw, int):
        pid = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        pid = int(raw.strip())
    else:
        return False, "person_id 必须是正整数"
    if pid <= 0:
        return False, "person_id 必须是正整数"
    return True, pid


def _person_profile(item):
    """Person 上可写档案字段（排除身份、归属、主键、时间戳）。"""
    allowed = {f.name for f in Person._meta.fields} - _PERSON_CLIENT_READONLY
    return {k: v for k, v in item.items() if k in allowed}


def _signature_order(value):
    """署名排序的宽松规整（直传 create/update_report 路径）：正整数或 None。

    bool/非数字/≤0 一律归 None，不在这里新增报错面；草稿提交路径在
    report_drafts._person 里有严格校验（非正整数直接 400）。
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value)
    if isinstance(value, int) and value > 0:
        return value
    return None


def _display_order(value):
    """表内行下标的宽松规整（直传 create/update_report 路径）：非负整数或 None。

    bool/非数字/负数一律归 None，不在这里新增报错面；草稿提交路径在
    report_drafts._person 里有严格校验（负数直接 400）。
    与 _signature_order 的唯一区别：0 是合法值（下标从 0 起）。
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value)
    if isinstance(value, int) and value >= 0:
        return value
    return None


# 官方用餐时段数（apps/api/registration_form.MEALS 的长度）；core 不反向依赖 api，常量本地存
_DINNER_SLOTS = 6


def _dinner_counts(value):
    """用餐预约人数的宽松规整（直传 create/update_report 路径）。

    与 dinner_reservation 并行、按下标对齐 6 个官方时段，counts[i]>0 表示第 i
    时段订 N 人。list 以外的输入整体视为没填（None），不在这里新增报错面；
    草稿提交路径在 report_drafts 里有严格校验（非数组/负数直接 400）。
    bool/负数/非数字归 None，数字字符串收下，不足 6 位补 None 对齐。
    """
    if not isinstance(value, list):
        return None
    result = [None] * _DINNER_SLOTS
    for index, item in enumerate(value[:_DINNER_SLOTS]):
        if isinstance(item, bool) or item is None:
            continue
        if isinstance(item, str) and item.strip().isdigit():
            item = int(item)
        if isinstance(item, int) and item >= 0:
            result[index] = item
    return result


# 【合并删除说明】master 侧此处曾有 _person_item_id / _CARD18_RE / _normalize_card：
#   · _normalize_card 把 18 位「自动截为后六位」——合并时**不保留**（见 models.Person.card
#     的合并说明：静默截断正是本轮要根除的错配来源）。写入路径统一走 models.normalize_card，
#     非法值一律在下面 store_people 的第一步整批拒绝。
#   · _person_item_id 已被 _person_reference 取代：后者同样同时认 person_id / id 两个键名，
#     但把「非正整数」「无效值」变成明确报错，而前者返回 None —— 那等于把越权/失效的 id
#     静默当成「没传」而新建一条 Person，正是冻结规则第 5、6 条禁止的行为。


@transaction.atomic
def store_people(user, people):
    """落地一批人员，返回 (ok, [{"person_id", "position", "type"}, ...] 或 错误文案)。

    【身份基准是 Person.id，不是 card】
    card 现在只是「身份证后 6 位」，10^6 种取值必然碰撞，不唯一、也不能唯一。
    所以这里**没有任何按 card 查库/合并的逻辑**：
      · 客户端带了 person_id → 复用那一行（前提是它属于当前单位），
        字段按本次提交更新；只有 user_id（归属单位）始终由服务端保留；
      · 没带 person_id → 一律新建一行。哪怕同一个人（同名同后 6 位）在同一批里
        交了两遍，也是两行 —— 这是刻意为之，不是遗漏。

    【person_id 是唯一显式复用机制，且必须显式失败】
    不存在的 id、不属于当前单位的 id，都返回明确错误；绝不静默新建，
    也绝不把越权的 id 当没传处理 —— 后者会让越权者在毫无察觉的情况下拿到一条
    看似成功、实则新造的人员记录。

    user 参数是**生效用户**：update_report 在 on_behalf（组委会代报）时传的是
    report.user_id，所以这里算出来的 effective_user_id 天然就是被代报单位的 id。
    """
    people = people or []
    effective_user_id = getattr(user, "id", user)

    # ---- 第一步：全部校验完再写第一行（保持原有的全有或全无语义）----
    prepared = []
    for item in people:
        if not isinstance(item, dict):
            return False, "人员数据格式不正确"
        name = str(item.get("name") or "").strip()
        if not name:
            return False, "姓名不能为空"
        ok, card = normalize_card(item.get("card"))
        if not ok:
            # card 校验文案里已含格式说明，拼上姓名便于用户在长表格里定位
            return False, "%s：%s" % (name, card)
        ok, person_id = _person_reference(item)
        if not ok:
            return False, "%s：%s" % (name, person_id)
        head_error = _person_head_error(item.get("head"))
        if head_error:
            return False, head_error
        prepared.append((item, name, card, person_id))

    # ---- 第二步：显式 person_id 的存在性 + 归属校验（一次性查库，避免 N+1）----
    referenced = {}
    wanted = {pid for (_, _, _, pid) in prepared if pid is not None}
    if wanted:
        referenced = {p.id: p for p in Person.objects.filter(id__in=wanted)}
    for (_, name, _, person_id) in prepared:
        if person_id is None:
            continue
        person = referenced.get(person_id)
        if person is None:
            return False, "人员不存在（person_id=%s）" % person_id
        if person.user_id != effective_user_id:
            return False, "人员不属于当前单位"

    # ---- 第三步：写入 ----
    result = []
    for (item, name, card, person_id) in prepared:
        profile = _person_profile(item)
        profile.pop("card", None)  # 统一走下面归一后的 card，避免未校验值覆盖
        if person_id is None:
            person = Person.objects.create(name=name, card=card,
                                           user_id=effective_user_id, **profile)
        else:
            person = referenced[person_id]
            # 复用：档案字段按本次提交更新。name 单独赋值 —— 用上面校验过的
            # 那份（已 strip），而不是原样拷贝 payload 里的值。
            #
            # 【name 必须跟着改，不能"保持既有值"】身份基准是 person_id；用户既然
            # 显式点名了这一行，就是"我知道我在改谁"。姓名可能是更正错别字。
            # 若这里不动 name，前端（draftPayload.js 对已有行发 id）改完姓名提交会
            # **报成功但姓名没变** —— 静默丢弃用户显式提交的字段，比报错更坏。
            # 同理也不能因为 name 与库里不一致就拒绝复用（那等于又拿 name 当身份）。
            person.name = name
            for key, value in profile.items():
                setattr(person, key, value)
            person.card = card
            person.save()
        # 【master 新功能】署名顺序与表内行下标写的是**关联行**（ReportPerson），
        # 不是 Person 的字段。直传路径在这里做宽松规整（"3"→3、0/布尔→None、
        # 负数→None），不新增报错面；草稿提交路径由 report_drafts._person 严格 400。
        result.append({"person_id": person.id,
                       "position": item.get("position", 0),
                       "type": item.get("type", 0),
                       "signature_order": _signature_order(item.get("signature_order")),
                       "display_order": _display_order(item.get("display_order"))})
    return True, result


def attach_report_people(report_id, result):
    ReportPerson.all_objects.filter(report_id=report_id).update(deleted_at=timezone.now())
    links = []
    for item in result:
        values = dict(item)
        # Accept the legacy service's `id` key while always storing it in the
        # report_person.person_id column, never as the link's primary key.
        if "person_id" not in values and "id" in values:
            values["person_id"] = values.pop("id")
        links.append(ReportPerson(report_id=report_id, **values))
    ReportPerson.objects.bulk_create(links)
    # 只解除本报名与人员的关联，**不删除 Person**。
    #
    # 「从某张报名里移除某人」≠「删除这个人」：Person 的身份基准是 Person.id，
    # 且 person_id 是客户端可长期持有、跨报名复用的显式引用（见 store_people）。
    # 原先此处会在「该人员不再被任何报名引用」时物理删除 Person 行 ——
    # 但全库没有任何 ForeignKey 指向 Person（report_person.person_id 只是普通
    # IntegerField），删除既不会被拦下，也因 Person 无软删除字段而不可逆，
    # 会连带丢掉 phone/school/head/instrument 等档案，并使草稿/回显里持有的
    # person_id 变成悬空引用。该行为已废除：Person 一律保留。


# Violations of the report_person conductor/instructor rule (migration 0007:
# partial unique index + trigger) surface as IntegrityError. Match the known
# trigger/index diagnostics and translate them into API-facing messages; the
# None return tells callers the error is unrelated and must be re-raised.
# GaussDB/PostgreSQL deferred triggers raise at COMMIT, so callers must catch
# outside their transaction.atomic() block.
_RULE_MESSAGE_HINTS = (
    ("指挥只能有 1 人", "每张报名表只能有 1 名指挥"),
    ("指导老师最多 1 人", "指挥是教师时，指导老师最多 1 人"),
    ("指导老师最多 2 人", "指挥是学生时，指导老师最多 2 人"),
    ("只能由教师担任指挥", "中小学组别只能由教师担任指挥"),
)
_RULE_CONSTRAINT_HINTS = (
    "report_person_one_conductor_idx",
    "UNIQUE constraint failed: report_person.report_id",
)


def report_rule_message(exc):
    """Friendly message for a conductor/instructor rule IntegrityError, else None."""
    cause = getattr(exc, "__cause__", None) or exc
    texts = [str(cause), str(exc)]
    diag = getattr(cause, "diag", None)
    for attr in ("message_primary", "constraint_name"):
        value = getattr(diag, attr, None)
        if value:
            texts.append(str(value))
    for marker, message in _RULE_MESSAGE_HINTS:
        if any(marker in text for text in texts):
            return message
    if any(hint in text for hint in _RULE_CONSTRAINT_HINTS for text in texts):
        return "每张报名表只能有 1 名指挥"
    return None


def report_dict(report, include_children=True):
    from .models import Report, Files, Person
    result = model_dict(report)
    result["dinner_reservation"] = report.dinner_reservation or []
    result["user"] = user_dict(User.objects.filter(pk=report.user_id).first())
    if include_children:
        links = ReportPerson.objects.filter(report_id=report.id)
        result["person"] = []
        for link in links:
            item = model_dict(link)
            item["person_info"] = model_dict(Person.objects.filter(pk=link.person_id).first())
            result["person"].append(item)
        # Laravel's eager-loaded relation names (`file` and `spectrum`) take
        # precedence over the raw foreign-key scalar in JSON serialization.
        result["file"] = model_dict(Files.objects.filter(pk=report.file).first())
        result["spectrum"] = model_dict(Files.objects.filter(pk=report.spectrum).first())
    return result


def live_report_dict(report):
    from .models import Crew
    result = model_dict(report)
    result["user"] = user_dict(User.objects.filter(pk=report.user_id).first())
    result["leader"] = [model_dict(x) for x in Leader.objects.filter(live_report_id=report.id)]
    result["crew"] = [model_dict(x) for x in Crew.objects.filter(live_report_id=report.id)]
    return result


def new_code():
    # Laravel: substr(date("YmdHis"), 2, 12) + six digits + three digits.
    return (
        datetime.now().strftime("%y%m%d%H%M%S")
        + str(secrets.randbelow(900000) + 100000)
        + str(secrets.randbelow(900) + 100)
    )


# --- Laravel-compatible password verification ---------------------------------
#
# Django 3.2's check_password() silently returns False for Laravel's raw bcrypt
# hashes ("$2y$10$..."): identify_hasher() parses the empty string before the
# leading '$' as the algorithm name, raises ValueError, and check_password
# swallows it (hashers.py:43-47). Registering a custom PASSWORD_HASHERS entry
# cannot help -- the hash is never routed to any hasher. So legacy hashes are
# verified here instead, and upgraded to PBKDF2 on successful login.
_LARAVEL_BCRYPT_RE = re.compile(r"^\$2[xyab]\$\d{2}\$[./A-Za-z0-9]{53}$")


def is_laravel_bcrypt_hash(encoded):
    """True for Laravel/PHP bcrypt digests: $2y$/$2x$/$2a$/$2b$ + cost + 53 chars, 60 total."""
    return (isinstance(encoded, str) and len(encoded) == 60
            and _LARAVEL_BCRYPT_RE.match(encoded) is not None)


def verify_user_password(user, raw_password):
    """check_password() replacement supporting Laravel bcrypt hashes.

    Verifies legacy $2y$ digests via the bcrypt package (transparently
    re-hashing the row to the preferred PBKDF2 hasher on success) and defers
    everything else to django.contrib.auth.hashers.check_password.
    """
    if raw_password is None:
        return False
    encoded = user.password or ""
    if not is_laravel_bcrypt_hash(encoded):
        from django.contrib.auth.hashers import check_password
        return check_password(raw_password, encoded)
    if not isinstance(raw_password, str):
        return False  # keep the pre-fix contract: non-string input fails silently
    try:
        import bcrypt
    except ImportError:
        return False  # missing package must not 500; behaves like before the fix
    if encoded.startswith("$2y$"):
        # $2y is byte-for-byte equivalent to $2b (OpenBSD canonical form).
        # $2x/$2a/$2b are passed through untouched; ValueError below absorbs
        # any input bcrypt rejects (e.g. passwords over 72 bytes).
        encoded = "$2b$" + encoded[4:]
    try:
        ok = bcrypt.checkpw(raw_password.encode("utf-8"), encoded.encode("ascii"))
    except ValueError:
        ok = False
    if ok:
        from django.contrib.auth.hashers import make_password
        user.password = make_password(raw_password)
        # update_fields keeps updated_at untouched: a hash reformat is not a
        # content change. (set_password() does not save; explicit save required.)
        user.save(update_fields=["password"])
    return ok
