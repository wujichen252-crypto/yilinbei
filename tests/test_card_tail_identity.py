"""身份证后六位口径：迁移 0013 的历史数据归一化 + store_people 的写入契约。

【本文件的定位（master → zyr 合并产物，2026-09-28）】
原 master 单分支版本的 tests/test_card_tail_identity.py 里有几项前提与 zyr 侧
「身份证后 6 位」改造**互相排斥**，已按合并要求删除：
  · NormalizeCardHelperTests —— 测的是 services._normalize_card（18 位在写入时
    **自动截断**为后六位）。该函数已在本次合并中删除：把 18 位号码静默换成另一个
    人的后 6 位（前 6 位是地区码，同区的人全一样）正是本轮改造要根除的错配来源。
    写入路径现在统一走 models.normalize_card，非法值一律整批拒绝。
  · test_full_id_saved_as_last_six、test_id_path_truncates_full_id_too ——
    编码的是同一个「写入自动截断」语义，已改写为断言**整批拒绝、一行不落库**。

保留下来的是与截断无关、仍然有独立价值的部分：
  · 不做身份查重：后 6 位允许重复，不带 person_id 就一律新建；
  · person_id 原地更新，以及「详情回显桥」（外层 id 是关联行 id、Person 主键在
    person_id 键上）这个曾经导致驳回后编辑删人无效的坑；
  · 失效 / 不存在的 id 必须显式报错，绝不静默新建；
  · 迁移 0013 对**历史存量** 18 位数据的归一化（一次性数据迁移，见下）。
"""
from importlib import import_module

from django.apps import apps
from django.test import TestCase

from apps.core.models import Person
from apps.core.services import store_people

from .base import ApiTestCase, card_for

MIGRATION = import_module("apps.core.migrations.0013_card_tail_identity")


class StorePeopleNoDedupTests(ApiTestCase):
    def setUp(self):
        self.school = self.create_user("school", 0)

    def item(self, **kw):
        return {"position": 0, "type": 0, **kw}

    def test_full_id_is_rejected_wholesale(self):
        # 18 位全号必须拒绝，绝不截成后 6 位；而且整批都不落库
        ok, message = store_people(self.school, [
            self.item(name="张三", card="510101199001011234"),
            self.item(name="李四", card=card_for("cti-li")),
        ])

        self.assertFalse(ok)
        self.assertIn("身份证后6位", message)
        self.assertEqual(Person.objects.count(), 0)

    def test_id_path_rejects_full_id_too(self):
        # 编辑流带 person_id 传 18 位全号 —— 同样拒绝，且原记录不被改写
        ok, stored = store_people(self.school, [
            self.item(name="张三", card=card_for("cti-zhang")),
        ])
        self.assertTrue(ok)

        ok2, message = store_people(self.school, [
            {"person_id": stored[0]["person_id"], "name": "张三",
             "card": "510101199001011234", "position": 0, "type": 0},
        ])

        self.assertFalse(ok2)
        self.assertIn("身份证后6位", message)
        self.assertEqual(Person.objects.count(), 1)
        person = Person.objects.get(pk=stored[0]["person_id"])
        self.assertEqual(person.card, card_for("cti-zhang"))

    def test_duplicates_are_created_without_dedup(self):
        # 身份查重已取消：同 (姓名, 后六位) 提交两次 → 两条独立记录，不报错不复用
        card = card_for("cti-dup")
        ok1, first = store_people(self.school, [self.item(name="张三", card=card)])
        ok2, second = store_people(self.school, [self.item(name="张三", card=card)])

        self.assertTrue(ok1 and ok2)
        self.assertNotEqual(first[0]["person_id"], second[0]["person_id"])
        self.assertEqual(Person.objects.count(), 2)

    def test_same_tail_different_names_both_created(self):
        card = card_for("cti-same-tail")
        ok, result = store_people(self.school, [
            self.item(name="张三", card=card),
            self.item(name="李四", card=card),
        ])

        self.assertTrue(ok)
        self.assertEqual(len({item["person_id"] for item in result}), 2)

    def test_id_path_updates_in_place(self):
        ok, stored = store_people(self.school, [
            self.item(name="张三", card=card_for("cti-ip-a")),
        ])
        self.assertTrue(ok)

        ok2, _ = store_people(self.school, [
            {"id": stored[0]["person_id"], "name": "张三",
             "card": card_for("cti-ip-b"), "position": 0, "type": 0},
        ])

        self.assertTrue(ok2)
        self.assertEqual(Person.objects.count(), 1)
        self.assertEqual(Person.objects.get(pk=stored[0]["person_id"]).card,
                         card_for("cti-ip-b"))

    def test_person_id_key_updates_in_place(self):
        # 详情回显桥：report_dict 的人员项外层 id 是关联行（report_person.id），
        # Person 主键在外层 person_id 键上。编辑流按详情回显原样提交时必须
        # 原地更新，而不是拿关联行 id 报「人员不存在」把整单挡下（驳回后编辑
        # 删人「数据库未改」的根因）。
        ok, stored = store_people(self.school, [
            self.item(name="张三", card=card_for("cti-bridge-a")),
        ])
        self.assertTrue(ok)

        ok2, _ = store_people(self.school, [
            {"id": 987654, "person_id": stored[0]["person_id"], "name": "张三",
             "card": card_for("cti-bridge-b"), "position": 0, "type": 0},
        ])

        self.assertTrue(ok2)
        self.assertEqual(Person.objects.count(), 1)
        self.assertEqual(Person.objects.get(pk=stored[0]["person_id"]).card,
                         card_for("cti-bridge-b"))

    def test_stale_id_is_rejected(self):
        ok, message = store_people(self.school, [
            {"id": 999999, "name": "张三", "card": card_for("cti-stale"),
             "position": 0, "type": 0},
        ])

        self.assertFalse(ok)
        self.assertIn("人员不存在", message)

    def test_other_units_person_id_is_rejected(self):
        other = self.create_user("school-other", 0)
        ok, stored = store_people(other, [
            self.item(name="张三", card=card_for("cti-other")),
        ])
        self.assertTrue(ok)

        ok2, message = store_people(self.school, [
            {"person_id": stored[0]["person_id"], "name": "张三",
             "card": card_for("cti-other"), "position": 0, "type": 0},
        ])

        self.assertFalse(ok2)
        self.assertIn("不属于当前单位", message)


class Migration0013NormalizeTests(TestCase):
    """迁移 0013 的**一次性历史数据归一化**：存量 18 位全号截为后六位。

    ⚠️ 这是**迁移行为**的测试，不是写入路径的契约 —— 写入路径（store_people /
    各接口）对 18 位是**一律拒绝**（见 StorePeopleNoDedupTests）。
    这条迁移之所以保留：它属于 master 已经上线的迁移历史，删掉会让存量库的
    迁移状态对不上；它只在部署时跑一次，处理的是「当年按全号存进去」的旧数据。
    """

    def setUp(self):
        # 注意：这里直接造 varchar(6) 列装不下的 18 位值，只在 SQLite 下成立
        # （SQLite 不校验长度）。生产库上跑的是同名的迁移函数，不是这段测试。
        Person.objects.create(user_id=1, name="张三", card="510101199001011234")
        Person.objects.create(user_id=1, name="李四", card="510101202501011234")
        Person.objects.create(user_id=1, name="王五", card="123456")
        Person.objects.create(user_id=1, name="老号", card="12345")
        Person.objects.create(user_id=1, name="空白默认", card=" ")

    def test_normalize_truncates_full_ids_and_leaves_the_rest_alone(self):
        MIGRATION.normalize_person_cards(apps, None)

        # 张三/李四全号不同但后六位相同 → 截断后重复，完全放行（无唯一约束）
        self.assertEqual(Person.objects.get(name="张三").card, "011234")
        self.assertEqual(Person.objects.get(name="李四").card, "011234")
        # 非 18 位的异常值原样保留，只打印提示、不中断（与 zyr 的运维命令
        # normalize_person_cards 不同：那个遇到异常值是整批中止的）
        self.assertEqual(Person.objects.get(name="王五").card, "123456")
        self.assertEqual(Person.objects.get(name="老号").card, "12345")
        self.assertEqual(Person.objects.get(name="空白默认").card, " ")
