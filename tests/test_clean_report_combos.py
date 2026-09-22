from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.models import Report

from .base import ApiTestCase


class CleanReportCombosTests(ApiTestCase):
    """清洗「铜管乐团 + 不在 {小学组,中学组} 内的组别」的脏数据。"""

    def setUp(self):
        self.user = self.create_user("school1")
        self.dirty = self.make_report(self.user, choir_name="脏数据团",
                                      establishment="铜管乐团", group="大学组")
        self.ok_brass = self.make_report(self.user, choir_name="中学铜管",
                                         establishment="铜管乐团", group="中学组")
        self.ok_wind = self.make_report(self.user, choir_name="大学管乐",
                                        establishment="管乐团", group="大学组")

    def run_command(self, *args):
        out = StringIO()
        call_command("clean_report_combos", *args, stdout=out)
        return out.getvalue()

    def test_audit_lists_dirty_record_without_touching_it(self):
        output = self.run_command()
        self.assertIn(str(self.dirty.id), output)
        self.assertIn("铜管乐团", output)
        self.assertIn("大学组", output)
        self.dirty.refresh_from_db()
        self.assertIsNone(self.dirty.deleted_at)
        self.assertEqual(Report.objects.count(), 3)

    def test_audit_skips_valid_combos(self):
        output = self.run_command()
        self.assertNotIn("中学铜管", output)
        self.assertNotIn("大学管乐", output)

    def test_audit_includes_soft_deleted_records(self):
        self.dirty.delete()
        output = self.run_command()
        self.assertIn(str(self.dirty.id), output)
        self.assertIn("已删除", output)

    def test_soft_delete_only_touches_dirty_records(self):
        self.run_command("--soft-delete")
        self.dirty.refresh_from_db()
        self.ok_brass.refresh_from_db()
        self.ok_wind.refresh_from_db()
        self.assertIsNotNone(self.dirty.deleted_at)
        self.assertIsNone(self.ok_brass.deleted_at)
        self.assertIsNone(self.ok_wind.deleted_at)

    def test_soft_delete_skips_already_deleted_records(self):
        self.dirty.delete()
        deleted_at = self.dirty.deleted_at
        self.run_command("--soft-delete")
        self.dirty.refresh_from_db()
        self.assertEqual(self.dirty.deleted_at, deleted_at)

    def test_set_group_rewrites_dirty_records(self):
        self.run_command("--set-group", "中学组")
        self.dirty.refresh_from_db()
        self.ok_brass.refresh_from_db()
        self.ok_wind.refresh_from_db()
        self.assertEqual(self.dirty.group, "中学组")
        self.assertEqual(self.ok_brass.group, "中学组")
        self.assertEqual(self.ok_wind.group, "大学组")

    def test_set_group_rejects_group_outside_brass_dropdown(self):
        with self.assertRaises(CommandError):
            self.run_command("--set-group", "大学组")

    def test_soft_delete_and_set_group_are_exclusive(self):
        with self.assertRaises(CommandError):
            self.run_command("--soft-delete", "--set-group", "中学组")

    def test_ids_narrows_the_scope(self):
        self.run_command("--soft-delete", "--ids", str(self.ok_brass.id))
        self.dirty.refresh_from_db()
        self.assertIsNone(self.dirty.deleted_at)
