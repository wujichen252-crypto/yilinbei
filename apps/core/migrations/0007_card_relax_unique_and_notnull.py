# 身份证改造 · 第 1 步（共 2 步）：**只放松约束，不动列宽**。
#
# 为什么必须拆成两步（不要合并、不要调换顺序）：
#   person.card 现在既有 UNIQUE、又是 varchar(255)，而目标是不唯一 + varchar(6)。
#   如果一步到位，PostgreSQL/GaussDB 执行 `ALTER COLUMN card TYPE varchar(6)` 时，
#   只要表里还有一行 18 位身份证，就会直接报
#       value too long for type character varying(6)
#   整个迁移失败回滚。而**数据转换不能写在 migration 里**（见下），
#   所以列宽必须等到数据转换（management command `normalize_person_cards`）跑完后，
#   由 0008 单独收紧。
#
# 本迁移只做两件事，都可在有 18 位数据的表上安全执行：
#   1. 摘掉 person.card 的 UNIQUE —— 后 6 位必然碰撞，唯一约束在业务上已经不成立；
#      留着的直接后果是「不同的人撞号时第二条报名写不进去」，而不是数据变脏。
#   2. 摘掉 crew/leader/ticket_subscribe.card 的 NOT NULL 与 default=" "。
#      原来的 default=" " 是为了绕开 GaussDB「NOT NULL 列写空串被当成 NULL」的变通，
#      它留下的 " " 占位符既满足不了「必须是 6 位」的不变量，又会在导出里出现一个
#      看起来像数据的空格。改成允许 NULL 后，缺值就是 NULL，语义干净。
#      列宽仍保持 255，等 0008 收紧。
#
# 【本迁移不含任何数据删除/改写】
#   没有 delete()、没有 update()、不写入任何一行，业务数据一行都不碰。
#   person.card 去 UNIQUE 的数据库侧动作由下面那个 schema-only 的 RunPython 下发
#   显式 DDL（原因见「为什么不能整个交给原生 AlterField」），它只改约束、不读改行。
#   业务数据是否清空是**独立的人工决策**，不属于迁移的一部分。
from django.db import migrations, models

# --- person.card 去 UNIQUE 的数据库侧实现 ---------------------------------------
#
# 【为什么不能整个交给原生 AlterField】
#   Django 3.2 的 PostgreSQL 后端在「摘掉唯一约束」时，要先反射出约束的真实名字，
#   而 django/db/backends/postgresql/introspection.py::get_constraints 用的是
#
#       unnest(c.conkey) WITH ORDINALITY
#
#   —— WITH ORDINALITY 是 PostgreSQL 9.4 才引入的语法。本项目的目标库是
#   PostgreSQL 9.2 / GaussDB，执行到这一步会直接报
#       syntax error at or near "WITH ORDINALITY"
#   所以这一步的数据库侧动作必须改用显式 DDL，绕开那次反射。
#
# 【SeparateDatabaseAndState 的分工】
#   * state_operations    —— 原来的 AlterField 原样保留，Django migration state 正确
#   * database_operations —— PostgreSQL：显式 DDL（**不**调用 schema_editor.alter_field）；
#                            非 PostgreSQL（SQLite 测试库）：交给 Django schema_editor，
#                            因为 SQLite 没有 DROP CONSTRAINT、UNIQUE 的自动索引也不能
#                            DROP INDEX，只有重建表才能摘掉唯一约束
#   两边都只改 schema，不改变任何字段值。


def _card_field_relaxed():
    """本步 person.card 的目标定义：去 UNIQUE、去 default，列宽仍 255。"""
    return models.CharField(max_length=255)


def _card_field_relaxed_for_editor():
    """与 _card_field_relaxed() 同一份定义，供 schema_editor.alter_field 使用。

    必须与 state_operations 里那份完全一致：_field_should_be_altered 比较的是
    deconstruct 结果，两处一旦漂移，SQLite 侧会判定「无需变更」而静默变成 no-op，
    UNIQUE 就摘不掉了。
    """
    field = _card_field_relaxed()
    field.set_attributes_from_name("card")
    return field


# 历史约束名（两个都要摘）：
#   person_card_key    —— 0001_initial 把 UNIQUE 内联在列定义里，PostgreSQL 自动命名
#                        为 <表>_<列>_key；Django 建出来的库就是这个名字。
#   person_card_unique —— Laravel 原库的命名（原版 create_person_table 里 card 就是
#                        unique），从旧库带过来的库会叫这个。
# 都带 IF EXISTS：完整计划里执行本迁移时，UNIQUE 可能已被上游 0008_alter_person_card
# 摘掉，缺了不该报错。
_DROP_UNIQUE_SQL = (
    "ALTER TABLE person DROP CONSTRAINT IF EXISTS person_card_key",
    "ALTER TABLE person DROP CONSTRAINT IF EXISTS person_card_unique",
    # 后置条件：person.card 上不允许再存在单列 UNIQUE。
    # 不满足就让整个迁移失败回滚 —— 宁可报错，也不能出现「迁移记成功、schema 其实没变」。
    """
DO $$
DECLARE n integer;
BEGIN
    SELECT count(*) INTO n
      FROM pg_constraint c
      JOIN pg_attribute a
        ON a.attrelid = c.conrelid
       AND a.attnum = ANY (c.conkey)
     WHERE c.conrelid = 'person'::regclass
       AND c.contype = 'u'
       AND a.attname = 'card'
       AND array_length(c.conkey, 1) = 1;
    IF n > 0 THEN
        RAISE EXCEPTION 'person.card 仍有 % 个 UNIQUE 约束，schema 未达到预期', n;
    END IF;
END $$;
""",
)

# 反向：把 UNIQUE 加回来（恢复历史命名 person_card_key，不用 Django 自动生成的
# person_card_<hash>_uniq，以免同一个约束在正反两个方向上名字不同）。
# 「不存在才加」——反向路径上本迁移与 0008_alter_person_card 可能都要恢复同一个约束，
# 无条件 ADD 会在第二次报 constraint already exists。
_ADD_UNIQUE_SQL = (
    """
DO $$
DECLARE n integer;
BEGIN
    SELECT count(*) INTO n
      FROM pg_constraint c
      JOIN pg_attribute a
        ON a.attrelid = c.conrelid
       AND a.attnum = ANY (c.conkey)
     WHERE c.conrelid = 'person'::regclass
       AND c.contype = 'u'
       AND a.attname = 'card'
       AND array_length(c.conkey, 1) = 1;
    IF n = 0 THEN
        EXECUTE 'ALTER TABLE person ADD CONSTRAINT person_card_key UNIQUE (card)';
    END IF;
END $$;
""",
)


def _run_schema_sql(schema_editor, statements):
    """在 PostgreSQL 上执行显式 DDL。

    params 必须显式传 None：schema_editor.execute 的默认值是 params=()，而 psycopg2
    只要收到非 None 的参数就会做 %-占位符替换，上面 RAISE EXCEPTION 里的 % 会被
    误当成占位符而直接报错。
    """
    for statement in statements:
        schema_editor.execute(statement, params=None)


def _drop_person_card_unique(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        _run_schema_sql(schema_editor, _DROP_UNIQUE_SQL)
        return
    # 非 PostgreSQL：SQLite 只能靠重建表摘掉唯一约束，交给 Django 自己做。
    # （_remake_table 内部的 INSERT INTO ... SELECT 是 schema editor 的建表复制机制，
    #   不改变任何字段值，也不是业务数据写入。）
    model = apps.get_model("core", "Person")
    schema_editor.alter_field(
        model, model._meta.get_field("card"), _card_field_relaxed_for_editor()
    )


def _restore_person_card_unique(apps, schema_editor):
    model = apps.get_model("core", "Person")
    # 反向时 apps 是「本步之前」的状态：它说 card 不唯一，就说明这一步之前 UNIQUE
    # 已经被上游摘掉（完整计划里由 0008_alter_person_card 完成），反向不该再长出来。
    state_field = model._meta.get_field("card")
    if not state_field.unique:
        return
    if schema_editor.connection.vendor == "postgresql":
        _run_schema_sql(schema_editor, _ADD_UNIQUE_SQL)
        return
    schema_editor.alter_field(model, _card_field_relaxed_for_editor(), state_field)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_reportdraft_unique_editing"),
    ]

    operations = [
        # 1) person.card：去 UNIQUE、去 default=" "，列宽先不动。
        #    state 侧与原 AlterField 完全一致；数据库侧见文件上方说明。
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="person",
                    name="card",
                    field=_card_field_relaxed(),
                ),
            ],
            database_operations=[
                migrations.RunPython(
                    _drop_person_card_unique,
                    _restore_person_card_unique,
                ),
            ],
        ),
        # 2) crew.card / leader.card / ticket_subscribe.card：去 NOT NULL、去 default
        #    这三张表的 card 没有 UNIQUE，不触发上面那次反射，保持原生 AlterField。
        migrations.AlterField(
            model_name="crew",
            name="card",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AlterField(
            model_name="leader",
            name="card",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AlterField(
            model_name="ticketsubscribe",
            name="card",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
