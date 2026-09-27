"""身份证后六位口径（2026-09-27）：迁移 0013 归一化 + store_people 写入归一化。

口径：身份证只收后六位、允许重复，**不做任何身份查重** —— 带 id 的项按 id
原地更新（编辑流），不带 id 的一律新建；不再被引用的旧记录由孤儿清理回收。
"""
from importlib import import_module

from django.apps import apps
from django.test import TestCase

from apps.core.models import Person
from apps.core.services import _normalize_card, store_people

from .base import ApiTestCase

MIGRATION = import_module("apps.core.migrations.0013_card_tail_identity")


class NormalizeCardHelperTests(TestCase):
    def test_full_id_truncated_to_last_six(self):
        self.assertEqual(_normalize_card("510101199001011234"), "011234")
        self.assertEqual(_normalize_card("51010119900101123X"), "01123X")

    def test_non_full_id_passes_through_stripped(self):
        self.assertEqual(_normalize_card(" 011234 "), "011234")
        self.assertEqual(_normalize_card("sig-a"), "sig-a")
        self.assertEqual(_normalize_card(None), "")
        self.assertEqual(_normalize_card(""), "")


class StorePeopleNoDedupTests(ApiTestCase):
    def setUp(self):
        self.school = self.create_user("school", 0)

    def item(self, **kw):
        return {"position": 0, "type": 0, **kw}

    def test_full_id_saved_as_last_six(self):
        ok, result = store_people(self.school, [
            self.item(name="张三", card="510101199001011234"),
        ])

        self.assertTrue(ok)
        person = Person.objects.get(pk=result[0]["person_id"])
        self.assertEqual(person.card, "011234")

    def test_duplicates_are_created_without_dedup(self):
        # 身份查重已取消：同 (姓名, 后六位) 提交两次 → 两条独立记录，不报错不复用
        ok1, first = store_people(self.school, [self.item(name="张三", card="011234")])
        ok2, second = store_people(self.school, [self.item(name="张三", card="011234")])

        self.assertTrue(ok1 and ok2)
        self.assertNotEqual(first[0]["person_id"], second[0]["person_id"])
        self.assertEqual(Person.objects.count(), 2)

    def test_same_tail_different_names_both_created(self):
        ok, result = store_people(self.school, [
            self.item(name="张三", card="011234"),
            self.item(name="李四", card="011234"),
        ])

        self.assertTrue(ok)
        self.assertEqual(len({item["person_id"] for item in result}), 2)

    def test_id_path_updates_in_place(self):
        ok, stored = store_people(self.school, [self.item(name="张三", card="011234")])

        ok2, _ = store_people(self.school, [
            {"id": stored[0]["person_id"], "name": "张三", "card": "222222",
             "position": 0, "type": 0},
        ])

        self.assertTrue(ok2)
        self.assertEqual(Person.objects.count(), 1)
        self.assertEqual(Person.objects.get(pk=stored[0]["person_id"]).card, "222222")

    def test_id_path_truncates_full_id_too(self):
        # 编辑流带 id 传 18 位全号 → 原地更新为后六位
        ok, stored = store_people(self.school, [self.item(name="张三", card="011234")])

        ok2, _ = store_people(self.school, [
            {"id": stored[0]["person_id"], "name": "张三",
             "card": "510101199001011234", "position": 0, "type": 0},
        ])

        self.assertTrue(ok2)
        self.assertEqual(Person.objects.count(), 1)
        self.assertEqual(Person.objects.get(pk=stored[0]["person_id"]).card, "011234")

    def test_stale_id_is_rejected(self):
        ok, message = store_people(self.school, [
            {"id": 999999, "name": "张三", "card": "011234",
             "position": 0, "type": 0},
        ])

        self.assertFalse(ok)
        self.assertIn("人员不存在", message)


class Migration0013NormalizeTests(TestCase):
    """历史数据归一化：所有 18 位全号截为后六位；重复值放行（无任何唯一约束）。"""

    def setUp(self):
        Person.objects.create(user_id=1, name="张三", card="510101199001011234")
        Person.objects.create(user_id=1, name="李四", card="510101202501011234")
        Person.objects.create(user_id=1, name="王五", card="123456")
        Person.objects.create(user_id=1, name="老号", card="12345")
        Person.objects.create(user_id=1, name="空白默认", card=" ")

    def test_normalize_truncates_all_full_ids(self):
        MIGRATION.normalize_person_cards(apps, None)

        # 张三/李四全号不同但后六位相同 → 截断后重复，完全放行
        self.assertEqual(Person.objects.get(name="张三").card, "011234")
        self.assertEqual(Person.objects.get(name="李四").card, "011234")
        self.assertEqual(Person.objects.get(name="王五").card, "123456")
        self.assertEqual(Person.objects.get(name="老号").card, "12345")
        self.assertEqual(Person.objects.get(name="空白默认").card, " ")
