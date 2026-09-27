# 身份证口径切换（2026-09-27）：不再收 18 位全号，只收后六位。
#
# 两件事：
#   1. 历史数据归一化：18 位全号截为后六位（向前不可逆 —— 业务决定不收全号，
#      库里留全号反而是隐私负担；已导出的 PDF 文件不受影响）。card 不再有任何
#      唯一约束（本迁移同时去掉单列 unique，也不新增任何身份键约束），截断后
#      出现重复值完全放行 —— 业务口径：后六位允许重复，不做身份查重。
#   2. 同步 person.card 的列注释。
#
# 写入路径的配套归一化在 services._normalize_card（四个写入口都经过 store_people）。
from django.db import migrations, models

_CARD18_RE = r"^\d{17}[\dXx]$"
_CARD6_RE = r"^\d{6}$"
_BLANK_RE = r"^\s*$"

OLD_COMMENT = "身份证号，全表唯一"
NEW_COMMENT = "身份证后六位（2026-09-27 起不再收 18 位全号；允许重复，不做身份查重）"


def normalize_person_cards(apps, schema_editor):
    Person = apps.get_model("core", "Person")
    full_rows = list(
        Person.objects.filter(card__regex=_CARD18_RE)
        .only("id", "name", "card")
        .order_by("id")
    )
    for row in full_rows:
        row.card = row.card[-6:]
        row.save(update_fields=["card"])
    print("身份证归一化：%d 行 18 位全号截为后六位。" % len(full_rows))
    odd = Person.objects.exclude(card__regex=_CARD18_RE).exclude(
        card__regex=_CARD6_RE).exclude(card__regex=_BLANK_RE)
    odd_count = odd.count()
    if odd_count:
        print("  另有 %d 行 card 既非 18 位也非 6 位纯数字，原样保留（请人工核对）：" % odd_count)
        for row in odd.only("id", "name", "card")[:10]:
            print("    person %s %s card=%r" % (row.id, row.name, row.card))


def restore_cards(apps, schema_editor):
    # 归一化不可逆（18 位信息已删），反向仅作占位。
    pass


def _set_comment(schema_editor, comment):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return  # sqlite 等不支持 COMMENT ON，与 0007/0010 同一处理方式
    with connection.cursor() as cursor:
        cursor.execute("COMMENT ON COLUMN person.card IS %s" % (
            "'" + comment.replace("'", "''") + "'"))


def apply_comment(apps, schema_editor):
    _set_comment(schema_editor, NEW_COMMENT)


def drop_comment(apps, schema_editor):
    _set_comment(schema_editor, OLD_COMMENT)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_report_person_display_order"),
    ]

    operations = [
        migrations.RunPython(normalize_person_cards, restore_cards),
        migrations.AlterField(
            model_name="person",
            name="card",
            field=models.CharField(default=" ", max_length=255),
        ),
        migrations.RunPython(apply_comment, drop_comment),
    ]
