# 报名人员的指挥/指导老师人数限制（方案一：部分唯一索引 + 延迟约束触发器）。
# 纯 RunSQL：不新增字段、不改 models.py，makemigrations 不会产生漂移。
#
# 业务口径（2026-09-23 确认，只强制上限，详见《指挥与指导老师人数限制-方案.md》）：
#   1. 每张报名表（report）最多 1 名有效指挥（position=2 且 deleted_at IS NULL）；
#   2. 指挥身份取该行 report_person.type（0=学生、1=教师）；
#   3. 教师指挥 → 有效指导老师（position=4 且未软删）最多 1 人；
#   4. 学生指挥 → 有效指导老师最多 2 人，且组别必须是高校；
#   5. 组别 report.group 存中文名（小学组/中学组/大学组，历史数据与数字串混用）：
#      「小学组/中学组/中小学组/中小学教师组」禁止学生指挥；大学组放行；
#      空值/数字串/未知值放行，避免误伤无法分类的历史数据。
#   「指导老师至少 1 人」的下限不在此强制（现有流程允许有指挥零指导老师，
#   数据库 500 无友好提示），留给应用层后续校验。
#
# 实现说明：
#   - GaussDB 按 PostgreSQL 9.2 兼容语法书写：EXECUTE PROCEDURE（非 11+ 的
#     EXECUTE FUNCTION）、CASE 聚合（非 9.4+ 的 FILTER）、不用 IF NOT EXISTS。
#   - 延迟约束触发器（DEFERRABLE INITIALLY DEFERRED）按事务提交时的最终状态
#     校验：报名提交是「先软删全部旧链接、再批量插入新链接」（services.attach_
#     report_people），逐行即时校验会被中间状态误伤。
#   - SQLite 不支持延迟触发器/存储函数，用普通 AFTER 触发器内联同样的规则。
#     上限类规则下中间状态计数单调不超过最终值（软删先于插入、逐行插入只增），
#     即时校验与最终状态校验等价，标准流程不会被误伤。软删只改 deleted_at 时
#     计数只减不增，同样安全。
#   - 反向操作完整回滚（DROP 触发器/函数/索引）。
from django.db import migrations

# 前置支撑索引：触发器按 report_id 聚合，report_person 原有索引只覆盖 person_id。
SUPPORT_INDEX = "CREATE INDEX report_person_report_id_idx ON report_person (report_id);"

# 指挥唯一：部分唯一索引只约束「有效指挥行」，软删行不在索引内，
# 「软删旧的 → 插入新的」不会误报（SQLite 3.8+ 同样支持）。
CONDUCTOR_INDEX = (
    "CREATE UNIQUE INDEX report_person_one_conductor_idx ON report_person (report_id) "
    "WHERE position = 2 AND deleted_at IS NULL;"
)

PG_RULE_FUNCTION = """
CREATE OR REPLACE FUNCTION report_person_instructor_rule() RETURNS trigger AS $fn$
DECLARE
    v_report int;
    v_group  text;
    c_cnt    int;
    c_type   int;
    i_cnt    int;
BEGIN
    v_report := COALESCE(NEW.report_id, OLD.report_id);
    SELECT btrim("group") INTO v_group FROM report WHERE id = v_report;
    SELECT count(CASE WHEN position = 2 THEN 1 END),
           max(CASE WHEN position = 2 THEN type END),
           count(CASE WHEN position = 4 THEN 1 END)
      INTO c_cnt, c_type, i_cnt
      FROM report_person
     WHERE report_id = v_report
       AND position IN (2, 4)
       AND deleted_at IS NULL;
    IF c_cnt > 1 THEN
        RAISE EXCEPTION '报名 % 的指挥只能有 1 人', v_report;
    END IF;
    IF c_cnt = 1 AND c_type = 1 AND i_cnt > 1 THEN
        RAISE EXCEPTION '报名 % 的指挥是教师，指导老师最多 1 人（当前 % 人）', v_report, i_cnt;
    END IF;
    IF c_cnt = 1 AND c_type = 0 AND i_cnt > 2 THEN
        RAISE EXCEPTION '报名 % 的指挥是学生，指导老师最多 2 人（当前 % 人）', v_report, i_cnt;
    END IF;
    IF c_cnt = 1 AND c_type = 0
       AND v_group IN ('小学组', '中学组', '中小学组', '中小学教师组') THEN
        RAISE EXCEPTION '报名 % 的组别为「%」，中小学只能由教师担任指挥', v_report, v_group;
    END IF;
    RETURN NULL;
END;
$fn$ LANGUAGE plpgsql;
"""

PG_RULE_TRIGGER = """
CREATE CONSTRAINT TRIGGER report_person_instructor_rule_trg
AFTER INSERT OR UPDATE OR DELETE ON report_person
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE PROCEDURE report_person_instructor_rule();
"""

PG_FORWARD = [SUPPORT_INDEX, CONDUCTOR_INDEX, PG_RULE_FUNCTION, PG_RULE_TRIGGER]

PG_BACKWARD = [
    "DROP TRIGGER IF EXISTS report_person_instructor_rule_trg ON report_person;",
    "DROP FUNCTION IF EXISTS report_person_instructor_rule();",
    "DROP INDEX IF EXISTS report_person_one_conductor_idx;",
    "DROP INDEX IF EXISTS report_person_report_id_idx;",
]

# SQLite 侧：把与 GaussDB 相同的规则内联进三个触发器（无存储函数可复用）。
_LIVE_CONDUCTORS = (
    "SELECT COUNT(*) FROM report_person p WHERE p.report_id = {row}.report_id "
    "AND p.deleted_at IS NULL AND p.position = 2"
)
_CONDUCTOR_TYPE = (
    "SELECT MAX(p.type) FROM report_person p WHERE p.report_id = {row}.report_id "
    "AND p.deleted_at IS NULL AND p.position = 2"
)
_LIVE_INSTRUCTORS = (
    "SELECT COUNT(*) FROM report_person p WHERE p.report_id = {row}.report_id "
    "AND p.deleted_at IS NULL AND p.position = 4"
)
_REPORT_GROUP = (
    "SELECT TRIM(r.\"group\") FROM report r WHERE r.id = {row}.report_id"
)


def _sqlite_rule_trigger(event, row, when=""):
    return (
        "CREATE TRIGGER report_person_instructor_%s_trg AFTER %s ON report_person "
        "FOR EACH ROW%s BEGIN "
        "SELECT RAISE(ABORT, '指挥只能有 1 人') WHERE (%s) > 1; "
        "SELECT RAISE(ABORT, '指挥是教师时，指导老师最多 1 人') WHERE (%s) = 1 "
        "AND (%s) = 1 AND (%s) > 1; "
        "SELECT RAISE(ABORT, '指挥是学生时，指导老师最多 2 人') WHERE (%s) = 1 "
        "AND (%s) = 0 AND (%s) > 2; "
        "SELECT RAISE(ABORT, '中小学组别只能由教师担任指挥') WHERE (%s) = 1 "
        "AND (%s) = 0 AND (%s) IN ('小学组', '中学组', '中小学组', '中小学教师组'); "
        "END;"
        % (event.lower(), event, when,
           _LIVE_CONDUCTORS.format(row=row),
           _LIVE_CONDUCTORS.format(row=row), _CONDUCTOR_TYPE.format(row=row),
           _LIVE_INSTRUCTORS.format(row=row),
           _LIVE_CONDUCTORS.format(row=row), _CONDUCTOR_TYPE.format(row=row),
           _LIVE_INSTRUCTORS.format(row=row),
           _LIVE_CONDUCTORS.format(row=row), _CONDUCTOR_TYPE.format(row=row),
           _REPORT_GROUP.format(row=row))
    )


SQLITE_FORWARD = [
    SUPPORT_INDEX,
    CONDUCTOR_INDEX,
    # INSERT 只对未软删行校验；UPDATE/DELETE 全量触发（软删使计数只减不增，安全）。
    _sqlite_rule_trigger("INSERT", "NEW", " WHEN NEW.deleted_at IS NULL"),
    _sqlite_rule_trigger("UPDATE", "NEW"),
    _sqlite_rule_trigger("DELETE", "OLD"),
]

SQLITE_BACKWARD = [
    "DROP TRIGGER IF EXISTS report_person_instructor_insert_trg;",
    "DROP TRIGGER IF EXISTS report_person_instructor_update_trg;",
    "DROP TRIGGER IF EXISTS report_person_instructor_delete_trg;",
    "DROP INDEX IF EXISTS report_person_one_conductor_idx;",
    "DROP INDEX IF EXISTS report_person_report_id_idx;",
]


def _statements(schema_editor, forward):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        return PG_FORWARD if forward else PG_BACKWARD
    if vendor == "sqlite":
        return SQLITE_FORWARD if forward else SQLITE_BACKWARD
    raise RuntimeError(
        "report_person 指挥/指导老师约束不支持的后端：%s（仅 PostgreSQL/GaussDB 与 SQLite）" % vendor
    )


def apply_rule(apps, schema_editor):
    for sql in _statements(schema_editor, forward=True):
        schema_editor.execute(sql)


def drop_rule(apps, schema_editor):
    for sql in _statements(schema_editor, forward=False):
        schema_editor.execute(sql)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_alter_person_card"),
    ]

    operations = [
        migrations.RunPython(apply_rule, drop_rule),
    ]
