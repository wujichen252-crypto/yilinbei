"""Django ORM representation of the Laravel schema.

Column names intentionally follow the Laravel migrations so an existing PostgreSQL
database can be attached without a destructive rename. Foreign keys retain their
integer semantics because the source migrations did not declare database FKs.

NOT NULL string columns default to a single space: GaussDB (PostgreSQL 9.2.4
compatibility) treats empty-string writes on NOT NULL columns as NULL, so legacy
seed values use " " instead of "".
"""
import hashlib
import json
import re
from datetime import timedelta

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

# ---------------------------------------------------------------------------
# 身份证后 6 位（全系统统一的 card 口径）
#
# 2026-09-23 起，系统内所有「身份证」字段只收**后 6 位**：5 位数字 + 1 位数字或 X。
# 末位小写 x 由 normalize_card() 归一成大写 X（校验位大小写不敏感是身份证的既有惯例）。
#
# 【必须调用 normalize_card()，不要自己写 card[-6:]】
# 直接切片会把 `12345` 这类明显残缺的输入「补」成合法值，也会把 18 位号码静默截断成
# 一个看起来合法、实际无法与真后 6 位区分的值 —— 那是错配人员照片的来源。
# 校验在前、归一在后，两者不可分割，所以合并成一个函数。
CARD_PATTERN = r"^[0-9]{5}[0-9Xx]$"
CARD_ERROR = "身份证后6位应为6位，前5位为数字，末位为数字或X"
CARD_MAX_LENGTH = 6
CARD_VALIDATOR = RegexValidator(CARD_PATTERN, CARD_ERROR)


def normalize_card(value):
    """校验并归一身份证后 6 位。返回 (True, 归一值) 或 (False, 错误文案)。

    空值、空白、非字符串一律判为非法 —— 调用方必须显式处理错误，不要 fallback 成空串：
    卡片字段是 NOT NULL 的，写入空串在 GaussDB 上会变成 NULL（见模块 docstring）。
    """
    if value is None:
        return False, CARD_ERROR
    if not isinstance(value, str):
        value = str(value)
    text = value.strip()
    if not re.match(CARD_PATTERN, text):
        return False, CARD_ERROR
    # 前 5 位保证是数字，只需归一末位
    return True, text[:5] + ("X" if text[5] in "xX" else text[5])


class LegacyJSONField(models.TextField):
    """JSON behavior over Laravel's legacy VARCHAR/TEXT columns.

    The source migrations store these values as strings and model accessors
    decode them. Keeping the physical type textual avoids requiring JSONB
    support from PostgreSQL 9.2.4/GaussDB while preserving Python list/dict APIs.
    """
    description = "JSON encoded legacy text"

    def from_db_value(self, value, expression, connection):
        return self.to_python(value)

    def to_python(self, value):
        if value in (None, ""):
            return [] if self.default is list else value
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value

    def get_prep_value(self, value):
        if value in (None, ""):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return value


class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class UserManager(BaseUserManager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)

    def with_deleted(self):
        return super().get_queryset()

    def create_user(self, username, password=None, **extra_fields):
        if not username:
            raise ValueError("username is required")
        user = self.model(username=username, **extra_fields)
        user.set_password(password or "")
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault("type", 3)
        return self.create_user(username, password, **extra_fields)


class User(AbstractBaseUser):
    TYPE_SCHOOL = 0
    TYPE_CITY = 1
    TYPE_COMMITTEE = 2
    TYPE_ADMIN = 3
    TYPE_PROVINCE = 4
    # 中小学端：接管原市州端的报名功能（2026-09-24 起），市州端降级为只读。
    TYPE_PRIMARY_SECONDARY = 5
    id = models.BigAutoField(primary_key=True)
    username = models.CharField(max_length=30, unique=True, default=" ")
    nickname = models.CharField(max_length=255, default=" ")
    description = models.CharField(max_length=1000, default="", blank=True, null=True)
    tel = models.CharField(max_length=50, default="", blank=True, null=True)
    leader = models.CharField(max_length=50, default="", blank=True, null=True)
    type = models.IntegerField(default=0)
    # 报名特许：默认 False = 每所学校限报一支队伍、只能参加一个组别（红头文件原口径，
    # 高校端 type=0 与中小学端 type=5 同此默认）。True 用于中小学合并办学的学校，
    # 允许报两支（小学组、中学组各一支；每个组别仍限一支），由管理员/组委会授予。
    can_report_twice = models.BooleanField(default=False)
    # parent_id is used by Laravel controllers although it was absent from the
    # checked-in users migration; keeping it nullable is backwards compatible.
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.DO_NOTHING,
                               db_column="parent_id", related_name="children", db_constraint=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = UserManager()
    all_objects = models.Manager()
    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["nickname"]

    class Meta:
        db_table = "users"
        ordering = ["id"]

    @property
    def is_staff(self):
        return self.type == self.TYPE_ADMIN

    @property
    def is_superuser(self):
        return self.type == self.TYPE_ADMIN

    @property
    def is_active(self):
        return self.deleted_at is None

    def delete(self, using=None, keep_parents=False):
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])

    def restore(self):
        self.deleted_at = None
        self.save(update_fields=["deleted_at"])


class PersonalAccessToken(models.Model):
    id = models.BigAutoField(primary_key=True)
    tokenable_type = models.CharField(max_length=255, default="App\\Models\\User")
    tokenable_id = models.BigIntegerField()
    name = models.CharField(max_length=255, default=" ")
    token = models.CharField(max_length=64, unique=True, default=" ")
    abilities = models.TextField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "personal_access_tokens"
        indexes = [models.Index(fields=["tokenable_type", "tokenable_id"])]

    @classmethod
    def issue(cls, user, name="art"):
        import secrets
        # Sanctum returns "<database id>|<random secret>" and stores only the
        # SHA-256 digest of the secret (not of the complete bearer value).
        secret = secrets.token_hex(20)
        obj = cls.objects.create(tokenable_id=user.pk, name=name,
                                 token=hashlib.sha256(secret.encode()).hexdigest(),
                                 abilities="[\"*\"]", last_used_at=timezone.now())
        return obj, f"{obj.pk}|{secret}"

    @classmethod
    def authenticate(cls, plain):
        if not plain or "|" not in plain:
            return None
        token_id, secret = plain.split("|", 1)
        if not token_id.isdigit() or not secret:
            return None
        digest = hashlib.sha256(secret.encode()).hexdigest()
        token = cls.objects.filter(pk=int(token_id), token=digest).first()
        if not token:
            return None
        from django.conf import settings
        if token.last_used_at and token.last_used_at < timezone.now() - timedelta(hours=settings.TOKEN_TTL_HOURS):
            token.delete()
            return None
        token.last_used_at = timezone.now()
        token.save(update_fields=["last_used_at", "updated_at"])
        return User.objects.filter(pk=token.tokenable_id).first()


class Report(models.Model):
    id = models.BigAutoField(primary_key=True)
    user_id = models.IntegerField(db_index=True)
    choir_name = models.CharField(max_length=255, default=" ")
    name = models.CharField(max_length=255, default=" ")
    name1 = models.CharField(max_length=255, null=True, blank=True)
    school_name = models.CharField(max_length=255, null=True, blank=True)
    desc = models.CharField(max_length=1000, null=True, blank=True)
    group = models.CharField(max_length=255, default=" ")
    establishment = models.CharField(max_length=255, default=" ")
    establishment_name = models.CharField(max_length=255, null=True, blank=True)
    contact_name = models.CharField(max_length=255, default=" ")
    contact_phone = models.CharField(max_length=255, default=" ")
    contact_way = models.CharField(max_length=255, null=True, blank=True)
    time_length = models.IntegerField(default=0)
    spectrum = models.IntegerField(null=True, blank=True)
    file = models.IntegerField(null=True, blank=True)
    dinner_reservation = LegacyJSONField(default=list, blank=True, null=True)
    # 各时段就餐人数（第十二届新增）：与 dinner_reservation 并行、按下标对齐
    # registration_form.MEALS 的 6 个官方时段；元素 null/非负整数，counts[i]>0
    # 表示第 i 时段订 N 人。通知附件2：在对应位置写上就餐人数。渲染层在
    # _meal_cells 与字符串勾选合并（人数优先）；不参与配额/校验。
    dinner_reservation_counts = LegacyJSONField(default=list, blank=True, null=True)
    status = models.IntegerField(default=0, null=True, blank=True)
    remark = models.CharField(max_length=255, null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        db_table = "report"
        indexes = [models.Index(fields=["user_id"])]

    def delete(self, using=None, keep_parents=False):
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])


class ReportDraft(models.Model):
    """Server-owned, versioned text snapshot of an unfinished report form."""
    STATE_EDITING = 0
    STATE_SUBMITTED = 1

    SCOPE_SCHOOL = User.TYPE_SCHOOL
    # 市州端已降级为只读（2026-09-24），不再产生新草稿；常量保留供存量
    # scope=1 的历史草稿行识别与数据兼容。
    SCOPE_CITY = User.TYPE_CITY
    SCOPE_PRIMARY_SECONDARY = User.TYPE_PRIMARY_SECONDARY

    id = models.BigAutoField(primary_key=True)
    user_id = models.BigIntegerField()
    scope = models.SmallIntegerField()
    report_id = models.BigIntegerField(null=True, blank=True)
    payload = models.TextField()
    schema_version = models.IntegerField(default=1)
    version = models.IntegerField(default=1)
    state = models.SmallIntegerField(default=STATE_EDITING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "report_draft"
        indexes = [
            models.Index(
                fields=["user_id", "scope", "state", "updated_at"],
                name="draft_user_state_idx",
            ),
            models.Index(fields=["report_id"], name="draft_report_idx"),
        ]
        # 部分唯一索引：state=0 表示 STATE_EDITING。
        # 两条分别覆盖"新增报名"（report_id IS NULL）和"编辑被驳回报名"两条业务线，
        # 应用层锁 + IntegrityError 兜底见 create_or_get_draft / _edit_draft。
        constraints = [
            models.UniqueConstraint(
                fields=["user_id", "scope"],
                condition=models.Q(state=0, report_id__isnull=True),
                name="draft_one_new_editing",
            ),
            models.UniqueConstraint(
                fields=["user_id", "scope", "report_id"],
                condition=models.Q(state=0),
                name="draft_one_edit_editing",
            ),
        ]


class Person(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=255, default=" ")
    user_id = models.IntegerField()
    # 身份证后 6 位。**故意不唯一**：同一个后 6 位对应多个真实的人是正常情况
    # （末 6 位只有 10^6 种组合，本次改造的前提就是不拿它当身份）。
    # 身份基准是 Person.id；复用同一个人必须由客户端显式传 person_id，
    # 由 store_people() 授权后复用 —— 详见 apps/core/services.py。
    #
    # 没有 default：NOT NULL 且无默认值，逼调用方显式给值，杜绝 " " 这类占位符
    # 重新长出来（旧 default=" " 是 GaussDB 空串当 NULL 的遗留变通，与 6 位不变量互斥）。
    #
    # 【合并说明】master 侧此处曾是 `max_length=255, default=" "`，并配
    # services._normalize_card 做「18 位写入时自动截断」。合并时**不采纳**：
    # 自动截断把 18 位号码悄悄换成另一个人的后 6 位（前 6 位是地区码，同区人全一样），
    # 是本轮改造要根除的错配来源。写入路径只认 normalize_card()，非法即整批拒绝；
    # 18 位历史数据的清洗由迁移 0013 + normalize_person_cards 命令单独负责。
    card = models.CharField(max_length=CARD_MAX_LENGTH, validators=[CARD_VALIDATOR])
    age = models.IntegerField(null=True, blank=True)
    school = models.CharField(max_length=255, null=True, blank=True)
    phone = models.CharField(max_length=255, null=True, blank=True)
    gender = models.CharField(max_length=255, null=True, blank=True)
    major = models.CharField(max_length=255, null=True, blank=True)
    head = models.CharField(max_length=255, null=True, blank=True)
    instrument = models.CharField(max_length=255, null=True, blank=True)
    other = models.CharField(max_length=255, null=True, blank=True)
    remark = models.CharField(max_length=255, default="", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "person"


class ReportPerson(models.Model):
    id = models.BigAutoField(primary_key=True)
    report_id = models.IntegerField()
    person_id = models.IntegerField(null=True, blank=True, db_index=True)
    position = models.IntegerField()
    type = models.IntegerField()
    signature_order = models.IntegerField(null=True, blank=True)
    display_order = models.IntegerField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        db_table = "report_person"


class Logs(models.Model):
    id = models.BigAutoField(primary_key=True)
    user_id = models.BigIntegerField()
    type = models.IntegerField()
    content = models.CharField(max_length=5000, default=" ")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "logs"
        indexes = [models.Index(fields=["content"])]


class Files(models.Model):
    id = models.BigAutoField(primary_key=True)
    user_id = models.IntegerField()
    filename = models.CharField(max_length=255, default=" ")
    type = models.CharField(max_length=255, default=" ")
    size = models.FloatField()
    url = models.CharField(max_length=255, default=" ")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "files"


class ScanFiles(models.Model):
    id = models.BigAutoField(primary_key=True)
    user_id = models.IntegerField()
    type = models.IntegerField()
    files = LegacyJSONField(null=True, blank=True)
    status = models.IntegerField(default=0, null=True, blank=True)
    remark = models.CharField(max_length=255, default="", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "scan_files"


class Recommend(models.Model):
    id = models.BigAutoField(primary_key=True)
    user_id = models.IntegerField()
    file = LegacyJSONField(null=True, blank=True)
    status = models.IntegerField(default=0, null=True, blank=True)
    remark = models.CharField(max_length=255, default="", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "recommend"


class LiveReport(models.Model):
    id = models.BigAutoField(primary_key=True)
    user_id = models.IntegerField()
    report_id = models.BigIntegerField()
    name = models.CharField(max_length=255, default=" ")
    choir_name = models.CharField(max_length=255, default=" ")
    district_or_school_name = models.CharField(max_length=255, default=" ")
    group = models.CharField(max_length=255, default=" ")
    contact_name = models.CharField(max_length=255, default=" ")
    contact_phone = models.CharField(max_length=255, default=" ")
    files = LegacyJSONField(null=True, blank=True)
    status = models.IntegerField(default=0)
    remark = models.CharField(max_length=255, null=True, blank=True)
    is_other_show = models.CharField(max_length=255, null=True, blank=True)
    other_show_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "live_report"


class Crew(models.Model):
    id = models.BigAutoField(primary_key=True)
    live_report_id = models.IntegerField()
    name = models.CharField(max_length=255, default=" ")
    # 身份证后 6 位，口径同 Person.card。允许为空：live 报名里工作人员可以只填姓名，
    # 此时宁可存 NULL 也不要 " " 占位（占位符不满足 6 位不变量，还会污染导出）。
    card = models.CharField(max_length=CARD_MAX_LENGTH, null=True, blank=True,
                            validators=[CARD_VALIDATOR])
    age = models.IntegerField(null=True, blank=True)
    school = models.CharField(max_length=255, null=True, blank=True)
    phone = models.CharField(max_length=255, null=True, blank=True)
    gender = models.IntegerField(null=True, blank=True)
    major = models.CharField(max_length=255, null=True, blank=True)
    other = models.CharField(max_length=255, null=True, blank=True)
    musical_instruments = models.CharField(max_length=255, null=True, blank=True)
    arrival_time = models.DateField(null=True, blank=True)
    departure_time = models.DateField(null=True, blank=True)
    position = models.IntegerField(default=0)
    type = models.IntegerField(default=0)
    head = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crew"


class Leader(models.Model):
    id = models.BigAutoField(primary_key=True)
    live_report_id = models.IntegerField()
    name = models.CharField(max_length=255, default=" ")
    gender = models.IntegerField(default=0)
    linkman = models.IntegerField(default=0)
    age = models.IntegerField(default=0)
    unit = models.CharField(max_length=255, null=True, blank=True)
    # 身份证后 6 位，口径同 Person.card（见 Crew.card 的说明）
    card = models.CharField(max_length=CARD_MAX_LENGTH, null=True, blank=True,
                            validators=[CARD_VALIDATOR])
    phone = models.CharField(max_length=255, default=" ")
    arrival_time = models.DateField(null=True, blank=True)
    departure_time = models.DateField(null=True, blank=True)
    head = models.CharField(max_length=255, null=True, blank=True)
    remark = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "leader"


class Draw(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=255, default=" ")
    type = models.IntegerField()
    index = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "draw"


class Ticket(models.Model):
    id = models.BigAutoField(primary_key=True)
    type_name = models.CharField(max_length=255, default=" ")
    ticket_session = models.CharField(max_length=255, null=True, blank=True)
    time = models.CharField(max_length=255, null=True, blank=True)
    week = models.CharField(max_length=255, null=True, blank=True)
    concrete = models.CharField(max_length=255, null=True, blank=True)
    site = models.CharField(max_length=255, null=True, blank=True)
    local = models.CharField(max_length=255, null=True, blank=True)
    number = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ticket"


class TicketSubscribe(models.Model):
    id = models.BigAutoField(primary_key=True)
    ticket_id = models.IntegerField()
    name = models.CharField(max_length=255, default=" ")
    # 观展人身份证后 6 位，口径同 Person.card。
    # 【不唯一，且不能当身份用】同一后 6 位可能对应多个真实观众，所以预约去重
    # 只能按 (场次, 姓名, 后6位) 三元组，不能只看 card —— 见 views.make_ticket。
    card = models.CharField(max_length=CARD_MAX_LENGTH, null=True, blank=True,
                            validators=[CARD_VALIDATOR])
    phone = models.CharField(max_length=255, default=" ")
    ip = models.CharField(max_length=255, default=" ")
    code = models.CharField(max_length=255, default=" ")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ticket_subscribe"


class Student(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=255, default=" ")
    user_id = models.IntegerField()
    gender = models.IntegerField(null=True, blank=True)
    age = models.IntegerField(null=True, blank=True)
    nation = models.CharField(max_length=255, null=True, blank=True)
    grade = models.CharField(max_length=255, null=True, blank=True)
    major = models.CharField(max_length=255, null=True, blank=True)
    phone = models.CharField(max_length=255, null=True, blank=True)
    # 身份证后 6 位，口径同 Person.card
    card = models.CharField(max_length=CARD_MAX_LENGTH, null=True, blank=True,
                            validators=[CARD_VALIDATOR])
    batch = models.CharField(max_length=255, null=True, blank=True)
    head = models.CharField(max_length=255, null=True, blank=True)
    status = models.IntegerField(null=True, blank=True)
    remark = models.CharField(max_length=255, null=True, blank=True)
    arrival_time = models.DateField(null=True, blank=True)
    departure_time = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "students"


class Statistics(models.Model):
    id = models.BigAutoField(primary_key=True)
    user_id = models.IntegerField(null=True, blank=True)
    file_id = models.IntegerField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "statistics"


class PasswordReset(models.Model):
    email = models.CharField(max_length=255, db_index=True, default=" ")
    token = models.CharField(max_length=255, default=" ")
    created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "password_resets"
        managed = True
