"""0009 迁移前置清洗（_soft_delete_duplicate_conductors）的回归测试。

生产事故（部署 #17-#25）：历史库里一单多条活跃指挥，CREATE UNIQUE INDEX
report_person_one_conductor_idx 报 UniqueViolation，migrate 原子回滚，
每次发布都卡死在第 6/9 步。清洗策略：每单保留最早一条（MIN(id)）活跃指挥，
其余软删。

SQLite 与 PostgreSQL/GaussDB 一样支持部分唯一索引，因此可以在 sqlite 测试库
上端到端复刻事故：先借 0009 已建好的触发器插入第一条指挥，再临时删掉触发器、
插入第二条（复现触发器诞生之前遗留的历史脏数据），断言唯一索引确实建不出来
→ 跑清洗 → 建得出来。
"""
from importlib import import_module

from django.apps import apps
from django.db import connection, IntegrityError
from django.test import TestCase
from django.utils import timezone

MIGRATION = import_module(
    "apps.core.migrations.0009_report_person_conductor_instructor_rule"
)
# 0009 在 sqlite 上创建的三个规则触发器（生产脏数据诞生于触发器之前）。
SQLITE_TRIGGER_NAMES = (
    "report_person_instructor_insert_trg",
    "report_person_instructor_update_trg",
    "report_person_instructor_delete_trg",
)


class DedupeConductorTests(TestCase):
    def setUp(self):
        from apps.core.models import Person, Report, ReportPerson

        self.report = Report.objects.create(user_id=1)
        teacher = Person.objects.create(user_id=1, name="甲", card="510101199001011234")
        student = Person.objects.create(user_id=1, name="乙", card="510101199001015678")
        # 第一条指挥走正常插入（触发器放行：当前计数为 1）。
        self.first_link = ReportPerson.objects.create(
            report_id=self.report.id, person_id=teacher.id, position=2, type=1
        )
        # 第二条指挥必须先删触发器再插入——复现 0009 上线前的历史脏数据。
        self._drop_sqlite_triggers()
        self.second_link = ReportPerson.objects.create(
            report_id=self.report.id, person_id=student.id, position=2, type=0
        )

    def _drop_sqlite_triggers(self):
        with connection.cursor() as cursor:
            for name in SQLITE_TRIGGER_NAMES:
                cursor.execute("DROP TRIGGER IF EXISTS %s" % name)

    def _try_create_unique_index(self):
        """True = 建成功；False = 被重复活跃指挥挡住（生产事故现场）。"""
        with connection.cursor() as cursor:
            try:
                cursor.execute(MIGRATION.CONDUCTOR_INDEX)
                return True
            except IntegrityError:
                return False

    def test_duplicate_conductors_block_index_until_dedupe(self):
        # 脏数据状态下唯一索引建不出来（= migrate 卡死的直接原因）。
        self.assertFalse(self._try_create_unique_index())

        MIGRATION._soft_delete_duplicate_conductors(apps)

        # 最早一条（MIN id）保留、较新的软删。
        self.first_link.refresh_from_db()
        self.second_link.refresh_from_db()
        self.assertIsNone(self.first_link.deleted_at)
        self.assertIsNotNone(self.second_link.deleted_at)

        # 清洗后索引建得出来（TestCase 事务回滚会自动撤掉这张索引）。
        self.assertTrue(self._try_create_unique_index())

    def test_dedupe_is_noop_without_duplicates(self):
        from apps.core.models import ReportPerson

        # 只剩一条活跃指挥（另一条已软删）：清洗必须零改动。
        self.second_link.deleted_at = timezone.now()
        self.second_link.save()
        before = ReportPerson.objects.count()

        MIGRATION._soft_delete_duplicate_conductors(apps)

        self.first_link.refresh_from_db()
        self.assertIsNone(self.first_link.deleted_at)
        self.assertEqual(ReportPerson.objects.count(), before)
