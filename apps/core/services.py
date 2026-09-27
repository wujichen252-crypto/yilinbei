import json
import re
import secrets
from datetime import datetime
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (Files, Leader, Logs, Person, PersonalAccessToken,
                     ReportPerson, User)


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


def _person_item_id(item):
    """提取人员项携带的 Person id（草稿流是字符串、直改流可能是整数）；无效时返回 None。

    优先认 person_id 键：详情接口（report_dict）回显的人员项外层 id 是关联行
    （report_person.id），Person 主键在外层 person_id / 内层 person_info.id 上；
    前端按详情回显原样提交时只认 id 会拿错主键，把整单挡在
    「人员不存在或已被删除」上（驳回后编辑删人“数据库未改”的根因之一）。
    """
    for key in ("person_id", "id"):
        raw = item.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text.isdigit():
            return int(text)
    return None


# 18 位居民身份证（前 17 位数字 + 末位数字或 X/x）。不含校验位验算，与前端口径一致。
_CARD18_RE = re.compile(r"^\d{17}[\dXx]$")


def _normalize_card(value):
    """身份证后六位口径（2026-09-27 起）：18 位全号自动截为后六位，其余 strip 原样。

    迁移 0013 已把历史 18 位归一化成后六位；这里对写入路径做同样处理，
    让旧页面/导入模板传上来的全号在落库前就统一成后六位。
    """
    card = str(value if value is not None else "").strip()
    if _CARD18_RE.match(card):
        return card[-6:]
    return card


@transaction.atomic
def store_people(user, people):
    people = people or []
    # 2026-09-27 口径：身份证只收后六位（18 位自动截断）、允许重复，不做任何
    # 身份查重 —— 带 id 的项按 id 原地更新（编辑流），不带 id 的一律新建；
    # 重提交后不再被引用的旧记录由 attach_report_people 的孤儿清理回收。
    ids = [pid for pid in (_person_item_id(item) for item in people) if pid is not None]
    existing_by_id = {person.id: person for person in Person.objects.filter(id__in=ids)}

    # 校验整批前置：任何一项不合法就整批不写（与 Laravel 全有或全无一致）。
    for item in people:
        card = _normalize_card(item.get("card"))
        name = str(item.get("name", "")).strip()
        if not card or not name:
            return False, "身份证和姓名不能为空"
        head_error = _person_head_error(item.get("head"))
        if head_error:
            return False, head_error
        pid = _person_item_id(item)
        if pid is not None and pid not in existing_by_id:
            return False, "人员不存在或已被删除，请刷新页面后重新提交"

    result = []
    for item in people:
        card = _normalize_card(item.get("card"))
        pid = _person_item_id(item)
        person = existing_by_id.get(pid) if pid is not None else None
        values = dict(item)
        # 18 位全号在此统一截为后六位（与迁移 0013 的历史归一化配套）。
        values["card"] = card
        values.pop("position", None)
        values.pop("type", None)
        values.pop("id", None)
        values.pop("person_id", None)   # 关联行回显键，不是 Person 字段
        if person:
            # 归属（user_id）与创建时间不可被提交数据改写。
            values.pop("user_id", None)
            for key in {f.name for f in Person._meta.fields}:
                if key in values:
                    setattr(person, key, values[key])
            person.save()
        else:
            values["user_id"] = getattr(user, "id", user)
            person = Person.objects.create(**{k: v for k, v in values.items() if k in {
                f.name for f in Person._meta.fields if f.name != "id"}})
        result.append({"person_id": person.id, "position": item.get("position", 0), "type": item.get("type", 0),
                       "signature_order": _signature_order(item.get("signature_order")),
                       "display_order": _display_order(item.get("display_order"))})
    return True, result


def attach_report_people(report_id, result):
    # 重新提交前本报名的活跃关联人员（软删前留存，供下面清理比对）。
    old_ids = set(ReportPerson.objects.filter(report_id=report_id)
                  .values_list("person_id", flat=True))
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
    # 清理"本次被移除、且不再被任何报名（活跃关联）引用"的人员记录：
    # Person 是全局人员库（card 唯一），被其他报名引用的人员必须保留。
    new_ids = {item.get("person_id", item.get("id")) for item in result}
    for pid in old_ids - new_ids:
        if pid is None:
            continue
        if not ReportPerson.objects.filter(person_id=pid).exists():
            Person.objects.filter(pk=pid).delete()


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
