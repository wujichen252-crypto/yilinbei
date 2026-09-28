# 身份证改造 · 第 2 步（共 2 步）：收紧列宽到 6 位，并挂上格式校验器。
#
# 【本迁移有前置条件，执行顺序不许颠倒】
#     0007（放松约束） → `manage.py normalize_person_cards`（数据转换） → 0008（本迁移）
# 合并 master 之后，这三步之间会夹进 master 的 0007_db_comments…0015 整条链
# （其中 0013_card_tail_identity 也会截断 person.card），本文件因此排在最后执行 —— 见下方
# dependencies 处的合并说明。运维侧的动作序列不变：
#     migrate core 0007_card_relax_unique_and_notnull
#   → normalize_person_cards（先 dry-run，确认后再 --apply）
#   → migrate（master 链 + 本文件收尾）
#
# 原因：`ALTER COLUMN TYPE varchar(6)` 在表里还有 18 位/7 位/带空格的值时**必然失败**
# （value too long）。0007 只是把「唯一」和「非空」这两条约束摘掉，列宽仍是 255，
# 因此 0007 之后、数据转换之前，系统处于「列宽 255、值已是后 6 位」的中间态 ——
# 这是设计好的过渡态，不对外服务，只用于跑数据转换。
#
# 【数据转换为什么不在本文件里】
#   把 18 位截成后 6 位是**不可逆**的（截完就再也还原不回完整身份证），
#   而且需要「报告 + 人工确认 + 可 dry-run 重跑」这些 migration 给不了的能力。
#   所以它落在 apps/core/management/commands/normalize_person_cards.py，
#   由运维显式执行。migration 只负责 schema，不碰业务数据。
#
# 【校验器的作用范围，不要误解】
#   CharField 的 validators 只在 full_clean() 时触发，`objects.create()` / `.save()`
#   都不会调用它 —— 也就是说**它拦不住写入**。真正拦写入的是两道：
#     服务端 apps/core/models.py 的 normalize_card()（store_people / views 里显式调用）；
#     DB 层的 varchar(6) 列宽。
#   这里挂 validators 是为了让 `full_clean()`（后台 admin、表单、shell 手工操作）
#   与那两道口径一致，不是为了「靠它守住」。
from django.core.validators import RegexValidator
from django.db import migrations, models

CARD_PATTERN = r"^[0-9]{5}[0-9Xx]$"
CARD_ERROR = "身份证后6位应为6位，前5位为数字，末位为数字或X"


def _card_field(**kwargs):
    return models.CharField(max_length=6, validators=[RegexValidator(CARD_PATTERN, CARD_ERROR)], **kwargs)


class Migration(migrations.Migration):

    # 【合并说明 · 为什么这里多了一条 master 侧的依赖】
    # 本文件是 zyr 分支「身份证后 6 位」的第 2 步（收尾），master 侧另有一条
    # 从 0006 一路推到 0015 的迁移链，其中
    #   0013_card_tail_identity 会把 person.card 从 varchar(6) 口径**改回**
    #   `CharField(default=" ", max_length=255)` 并顺手截断历史 18 位数据。
    # 合并前两条链各有一个叶子节点（本文件 + 0015_report_dinner_reservation_counts），
    # Django 会直接报 "Conflicting migrations detected; multiple leaf nodes"，
    # 而且**谁最后跑不确定**：若 0013 在本文件之后执行，最终 schema 会退化成
    # varchar(255) + default=" "，与 models.Person.card（6 位、无默认值）不一致，
    # `makemigrations --check` 立刻失败。
    #
    # 这里把 0015 加进依赖（而不是新建一个空 merge migration 去汇合）是**故意的**：
    #   · `MigrationGraph.forwards_plan()` 对一个节点的多个依赖是当成 **set** 做 DFS 的，
    #     仅靠 "0016 = merge(0008, 0015)" 无法保证 0008 在 0013 之后执行 —— 同一张图
    #     在不同进程里可能排出不同的顺序（字符串 set 的迭代序受 hash seed 影响）。
    #     真依赖边则是硬约束：0008 必定在 0015 之后、也就是必定在 0013 之后执行。
    #   · 于是整条链收敛成单叶子且顺序确定：
    #       0006 → 0007_card_relax（松约束）→ 0007_db_comments → 0008_alter_person_card
    #       → 0009 → 0010 → 0011 → 0012 → 0013_card_tail_identity（历史归一化）
    #       → 0013_merge → 0014 → 0015 → 0008_card_last6_length（**最后**收紧到 6 位）
    #     注意 0007_card_relax 与 master 链之间没有依赖边，两者先后由 Django 决定；
    #     两种顺序都安全：0008_alter_person_card 本身就会摘掉 person.card 的 UNIQUE，
    #     所以 0013 截断时不存在唯一约束冲突；而五张表的 card 最终定义全部由本文件
    #     重写，谁先谁后都不影响收尾状态。
    #
    # 【升级存量库时的注意点（仅当有库已经把 zyr 单分支的 0008 跑过）】
    # 那种库里 0008 已应用、0015 未应用，Django 的 InconsistentMigrationHistory 会拦住
    # migrate。正确做法是先退回到 0007 再整体前滚（varchar(6)→varchar(255) 是放宽，不丢数据）：
    #     python manage.py migrate core 0007_card_relax_unique_and_notnull
    #     python manage.py migrate
    # 部署在 master 侧的库不受影响：master 的迁移依赖没动，0008 本身未应用 —— 无矛盾。
    dependencies = [
        ("core", "0007_card_relax_unique_and_notnull"),
        ("core", "0015_report_dinner_reservation_counts"),
    ]

    operations = [
        # person.card —— 全系统唯一一处曾经是 UNIQUE 的 card，0007 已摘掉。
        # null=False 保持不变（必须显式给值），且**没有 default**：默认值会掩盖漏填。
        migrations.AlterField(
            model_name="person",
            name="card",
            field=_card_field(),
        ),
        # 其余四处允许为空（业务上人可以不填身份证），列宽同样收到 6
        migrations.AlterField(
            model_name="crew",
            name="card",
            field=_card_field(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="leader",
            name="card",
            field=_card_field(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="ticketsubscribe",
            name="card",
            field=_card_field(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="student",
            name="card",
            field=_card_field(blank=True, null=True),
        ),
    ]
