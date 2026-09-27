# 临时探针：复现「组委会驳回后编辑」两个问题。跑完即删。
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_config.settings")
os.environ["DB_ENGINE"] = "django.db.backends.sqlite3"
# Windows 上 python.exe 打不开 /tmp（MSYS 只转换 bash 参数，不转换脚本内 env）
import tempfile
os.environ["DB_NAME"] = os.path.join(tempfile.gettempdir(), "probe_reject.sqlite3")
if os.path.exists(os.environ["DB_NAME"]):
    os.remove(os.environ["DB_NAME"])   # 每次全新库
import sys
sys.stdout.reconfigure(encoding="utf-8")
django.setup()

from django.conf import settings
settings.ALLOWED_HOSTS.append("testserver")

from django.core.management import call_command
call_command("migrate", "--run-syncdb", "-v0", interactive=False)

from django.test import Client
from apps.core.models import Person, Report, ReportPerson, User, PersonalAccessToken


def make_user(username, utype):
    return User.objects.create_user(username, "pw", type=utype)


def token_for(user):
    _, plain = PersonalAccessToken.issue(user)
    return {"HTTP_AUTHORIZATION": "Bearer " + plain}


def setup_report(school, group="大学组"):
    """驳回状态报名：张三(队员)+李四(教师指挥)+王五(指导老师)，各一行 person。"""
    report = Report.objects.create(
        user_id=school.id, choir_name="驳回复现团", name="驳回复现节目", group=group,
        establishment="管乐团", contact_name="联", contact_phone="13800000000",
        time_length=120, status=-1, remark="驳回理由：信息有误")
    rows = [("张三", "500101200001011234", 0, 0),
            ("李四", "500101200001022345", 2, 1),
            ("王五", "500101200001023456", 4, 1)]
    pids = []
    for name, card, position, ptype in rows:
        p = Person.objects.create(name=name, card=card, user_id=school.id)
        ReportPerson.objects.create(report_id=report.id, person_id=p.id,
                                    position=position, type=ptype)
        pids.append(p.id)
    return report, pids


def snapshot(tag):
    persons = [(p.id, p.name, p.card) for p in Person.objects.order_by("id")]
    print("  [%s] person 表: %s" % (tag, persons))


def run(tag, fn):
    print("== %s ==" % tag)
    try:
        fn()
    except Exception as exc:
        print("  异常: %r" % exc)
    print()


def main():
    school = make_user("rej-school", 0)
    committee = make_user("rej-committee", 2)
    school_h = token_for(school)
    committee_h = token_for(committee)
    client = Client()

    # ---- 场景 1：学校端 PUT update，person 不带 id，改张三的身份证 ----
    def s1():
        report, _ = setup_report(school)
        payload = {"id": report.id, "choir_name": "驳回复现团", "name": "驳回复现节目",
                   "group": "大学组", "establishment": "管乐团", "contact_name": "联",
                   "contact_phone": "13800000000", "time_length": 120,
                   "person": [
                       {"name": "张三", "card": "500101199909099999", "position": 0, "type": 0},
                       {"name": "李四", "card": "500101200001022345", "position": 2, "type": 1},
                       {"name": "王五", "card": "500101200001023456", "position": 4, "type": 1},
                   ]}
        r = client.put("/api/school/report/update", payload, content_type="application/json", **school_h)
        print("  状态=%s code=%s msg=%s" % (r.status_code, r.json().get("code"), r.json().get("msg")))
        snapshot("S1 改身份证·不带id")
    run("S1 学校 PUT update / 改身份证 / person 不带 id", s1)

    # ---- 场景 2：学校端 PUT update，person 带 Person id，改身份证 ----
    def s2():
        report, pids = setup_report(school)
        cards = ["500101200001011234", "500101200001022345", "500101200001023456"]
        names = ["张三", "李四", "王五"]
        pos_type = [(0, 0), (2, 1), (4, 1)]
        payload = {"id": report.id, "choir_name": "驳回复现团", "name": "驳回复现节目",
                   "group": "大学组", "establishment": "管乐团", "contact_name": "联",
                   "contact_phone": "13800000000", "time_length": 120,
                   "person": [
                       {"id": pids[i], "name": names[i], "card": (cards[i] if i else "500101199909099999"),
                        "position": pos_type[i][0], "type": pos_type[i][1]}
                       for i in range(3)]}
        r = client.put("/api/school/report/update", payload, content_type="application/json", **school_h)
        print("  状态=%s code=%s msg=%s" % (r.status_code, r.json().get("code"), r.json().get("msg")))
        snapshot("S2 改身份证·带Person id")
    run("S2 学校 PUT update / 改身份证 / person 带 Person id", s2)

    # ---- 场景 3：学校端 PUT update，带 id，删掉王五 ----
    def s3():
        report, pids = setup_report(school)
        rows = [("张三", "500101200001011234", 0, 0, pids[0]),
                ("李四", "500101200001022345", 2, 1, pids[1])]
        payload = {"id": report.id, "choir_name": "驳回复现团", "name": "驳回复现节目",
                   "group": "大学组", "establishment": "管乐团", "contact_name": "联",
                   "contact_phone": "13800000000", "time_length": 120,
                   "person": [{"id": pid, "name": n, "card": c, "position": p, "type": t}
                              for n, c, p, t, pid in rows]}
        r = client.put("/api/school/report/update", payload, content_type="application/json", **school_h)
        print("  状态=%s code=%s msg=%s" % (r.status_code, r.json().get("code"), r.json().get("msg")))
        links = list(ReportPerson.all_objects.filter(report_id=report.id)
                     .values_list("person_id", "position", "deleted_at"))
        print("  该报名全部关联行: %s" % links)
        snapshot("S3 删王五·带id")
    run("S3 学校 PUT update / 删人员 / person 带 Person id", s3)

    # ---- 场景 4：组委会 PUT update（代改），person 不带 id，删掉王五 ----
    def s4():
        report, _ = setup_report(school)
        payload = {"id": report.id, "choir_name": "驳回复现团", "name": "驳回复现节目",
                   "group": "大学组", "establishment": "管乐团", "contact_name": "联",
                   "contact_phone": "13800000000", "time_length": 120,
                   "person": [
                       {"name": "张三", "card": "500101200001011234", "position": 0, "type": 0},
                       {"name": "李四", "card": "500101200001022345", "position": 2, "type": 1},
                   ]}
        r = client.put("/api/committee/report/update", payload, content_type="application/json", **committee_h)
        print("  状态=%s code=%s msg=%s" % (r.status_code, r.json().get("code"), r.json().get("msg")))
        links = list(ReportPerson.all_objects.filter(report_id=report.id)
                     .values_list("person_id", "position", "deleted_at"))
        print("  该报名全部关联行: %s" % links)
        snapshot("S4 组委会代改·不带id·删王五")
    run("S4 组委会 PUT update / 删人员 / person 不带 id", s4)

    # ---- 场景 5：学校端 edit-draft 流（驳回重提主流程）。独占学校避开配额误伤 ----
    school5 = make_user("rej-school5", 0)
    school5_h = token_for(school5)

    def s5a():
        # 5a：忠实回传草稿 payload（person 带 id）→ 应原地更新 + 删除生效
        report, _ = setup_report(school5)
        r = client.post("/api/school/reports/%s/edit-draft" % report.id, "{}",
                        content_type="application/json", **school5_h)
        print("  edit-draft 状态=%s" % r.status_code)
        if r.json().get("code") != 0:
            print("  内容: %s" % r.content.decode("utf-8")[:200]); return
        data = r.json()["data"]
        draft_id, version, payload = data["draft_id"], data["version"], data["payload"]
        print("  草稿 person id: %s" % [row.get("id") for row in payload["person"]])
        payload["person"][0]["card"] = "500101199909099999"   # 改张三身份证
        payload["person"] = payload["person"][:2]             # 删王五
        r = client.put("/api/school/report/drafts/%s" % draft_id,
                       {"version": version, "payload": payload},
                       content_type="application/json", **school5_h)
        print("  暂存 状态=%s code=%s" % (r.status_code, r.json().get("code")))
        r = client.post("/api/school/report/drafts/%s/submit" % draft_id,
                        {"version": version + 1}, content_type="application/json", **school5_h)
        print("  提交 状态=%s code=%s msg=%s" % (r.status_code, r.json().get("code"), r.json().get("msg")))
        links = list(ReportPerson.all_objects.filter(report_id=report.id)
                     .values_list("person_id", "position", "deleted_at"))
        print("  该报名全部关联行: %s" % links)
        snapshot("S5a edit-draft·带id·改卡+删人")
    run("S5a 学校 edit-draft 重提 / 忠实回传（带 id）/ 改身份证+删人员", s5a)

    def s5b():
        # 5b：payload person 被剥掉 id（模拟前端不回传 id）→ 全组重建为新人员
        report, _ = setup_report(school5, group="中学组")
        r = client.post("/api/school/reports/%s/edit-draft" % report.id, "{}",
                        content_type="application/json", **school5_h)
        data = r.json()["data"]
        draft_id, version, payload = data["draft_id"], data["version"], data["payload"]
        for row in payload["person"]:
            row.pop("id", None)                      # 模拟前端编辑页丢 id
        payload["person"][0]["card"] = "500101199909099999"   # 改张三身份证
        payload["person"] = payload["person"][:2]             # 删王五
        client.put("/api/school/report/drafts/%s" % draft_id,
                   {"version": version, "payload": payload},
                   content_type="application/json", **school5_h)
        r = client.post("/api/school/report/drafts/%s/submit" % draft_id,
                        {"version": version + 1}, content_type="application/json", **school5_h)
        print("  提交 状态=%s code=%s msg=%s" % (r.status_code, r.json().get("code"), r.json().get("msg")))
        links = list(ReportPerson.all_objects.filter(report_id=report.id)
                     .values_list("person_id", "position", "deleted_at"))
        print("  该报名全部关联行: %s" % links)
        snapshot("S5b edit-draft·丢id·改卡+删人")
    run("S5b 学校 edit-draft 重提 / person 丢 id / 改身份证+删人员", s5b)

    # ---- 场景 7：按详情接口回显原样提交（外层 id=关联行 id，person_id=Person id）----
    # 兼容桥验收：_person_item_id 优先认 person_id，应原地更新且删除生效
    def s7():
        report, pids = setup_report(school)
        link_ids = [l.id for l in ReportPerson.all_objects.filter(report_id=report.id).order_by("id")]
        payload = {"id": report.id, "choir_name": "驳回复现团", "name": "驳回复现节目",
                   "group": "大学组", "establishment": "管乐团", "contact_name": "联",
                   "contact_phone": "13800000000", "time_length": 120,
                   "person": [
                       {"id": link_ids[0], "person_id": pids[0], "name": "张三",
                        "card": "500101199909099999", "position": 0, "type": 0},
                       {"id": link_ids[1], "person_id": pids[1], "name": "李四",
                        "card": "500101200001022345", "position": 2, "type": 1},
                   ]}
        r = client.put("/api/committee/report/update", payload, content_type="application/json", **committee_h)
        print("  状态=%s code=%s msg=%s" % (r.status_code, r.json().get("code"), r.json().get("msg")))
        links = list(ReportPerson.all_objects.filter(report_id=report.id)
                     .values_list("person_id", "position", "deleted_at"))
        print("  该报名全部关联行: %s" % links)
        snapshot("S7 详情回显形·person_id桥·改卡+删人")
    run("S7 组委会 PUT update / person 按详情回显形（id=关联行, person_id=Person）/ 改卡+删人", s7)

    # ---- 场景 6：组委会代改，id 传成关联行 id（report_person.id）----
    def s6():
        report, pids = setup_report(school)
        link_ids = [l.id for l in ReportPerson.all_objects.filter(report_id=report.id).order_by("id")]
        payload = {"id": report.id, "choir_name": "驳回复现团", "name": "驳回复现节目",
                   "group": "大学组", "establishment": "管乐团", "contact_name": "联",
                   "contact_phone": "13800000000", "time_length": 120,
                   "person": [
                       {"id": link_ids[0], "name": "张三", "card": "500101199909099999", "position": 0, "type": 0},
                       {"id": link_ids[1], "name": "李四", "card": "500101200001022345", "position": 2, "type": 1},
                       {"id": link_ids[2], "name": "王五", "card": "500101200001023456", "position": 4, "type": 1},
                   ]}
        r = client.put("/api/committee/report/update", payload, content_type="application/json", **committee_h)
        print("  状态=%s code=%s msg=%s" % (r.status_code, r.json().get("code"), r.json().get("msg")))
        snapshot("S6 组委会代改·id=关联行id·改身份证")
    run("S6 组委会 PUT update / person.id 误传关联行 id", s6)


main()
print("====== 探针结束 ======")
