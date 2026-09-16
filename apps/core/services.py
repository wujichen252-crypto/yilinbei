import json
import secrets
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from .models import (Files, Leader, Logs, Person, PersonalAccessToken,
                     ReportPerson, User)


def success(msg="操作成功!", data=None):
    return {"code": 0, "msg": msg, "data": data}


def failure(msg="操作失败!", data=None):
    return {"code": 1, "msg": msg, "data": data}


def page_response(items, count, msg="", code=0):
    return {"data": items, "count": count, "code": code, "msg": msg}


def parse_body(request):
    if request.body:
        try:
            return json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            pass
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
            "parent_id": user.parent_id}


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


@transaction.atomic
def store_people(user, people):
    people = people or []
    existing_by_card = {
        person.card: person
        for person in Person.objects.filter(
            card__in=[str(item.get("card", "")).strip() for item in people]
        )
    }
    names_by_card = {}

    # Validate the complete batch before performing the first write. This is
    # important because the Laravel operation is all-or-nothing even when a
    # later member has a conflicting identity number.
    for item in people:
        card = str(item.get("card", "")).strip()
        name = str(item.get("name", "")).strip()
        if not card or not name:
            return False, "身份证和姓名不能为空"
        if card in names_by_card and names_by_card[card] != name:
            return False, f"{card}-{name},该身份证已被使用，请检查您的身份证和姓名是否输入正确"
        names_by_card[card] = name
        existing = existing_by_card.get(card)
        if existing and existing.name != name:
            return False, f"{card}-{name},该身份证已被使用，请检查您的身份证和姓名是否输入正确"

    result = []
    saved_by_card = {}
    for item in people:
        card = str(item.get("card", "")).strip()
        name = str(item.get("name", "")).strip()
        person = saved_by_card.get(card) or existing_by_card.get(card)
        values = dict(item)
        values.pop("position", None)
        values.pop("type", None)
        values.pop("id", None)
        values["user_id"] = user.id
        if person:
            for key in {f.name for f in Person._meta.fields}:
                if key in values:
                    setattr(person, key, values[key])
            person.save()
        else:
            person = Person.objects.create(**{k: v for k, v in values.items() if k in {
                f.name for f in Person._meta.fields if f.name != "id"}})
        saved_by_card[card] = person
        result.append({"person_id": person.id, "position": item.get("position", 0), "type": item.get("type", 0)})
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
