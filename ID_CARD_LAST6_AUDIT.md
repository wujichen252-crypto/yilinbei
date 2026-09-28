# 身份证后六位改造专项审计报告

> 审计日期：2026-09-23
> 审计对象：`E:\github\yilinbei`（Django 后端）+ `E:\github\yl-frontend`（Vue 3 前端）
> 审计性质：**只读**。未修改任何 Python / JS / Vue / Model / migration / 数据库 / 配置 / 构建产物，未执行 migrate、未提交 Git。
> 结论口径：本次审计只描述**现状**与**风险**，不替上级决定业务规则。

---

## 0. 审计方法与一个必须先行说明的限制

### 0.1 已执行的检查

| 检查 | 手段 | 覆盖 |
| --- | --- | --- |
| `card` 定义与约束 | 读 `models.py` + 全部 6 个 migration | 完整 |
| `card` 业务依赖 | 全仓 `grep -rn "card"`（Python / Vue / JS）+ 定向读 | 完整 |
| 查询模式 | `grep` `get(card=` / `filter(card=` / `update_or_create` / `card__` | 完整 |
| 前端校验 | 读 `personFields.js` + 6 处绑定组件 + 2 个 Excel 模板（解包 xlsx） | 完整 |
| 测试依赖 | 逐个测试文件核对断言 | 完整 |
| **历史数据统计** | **未能执行 —— 见 0.2** | **缺失** |

### 0.2 历史数据统计（§6）本次**无法执行**，原因是环境事实而非疏漏

- 仓库内**没有 `.env`**（只有 `.env.example`），生产库连接信息不在代码库中；
- 本地 PostgreSQL（`127.0.0.1:5432`）**未运行**，实测 `Connection refused`；
- `pytest` 同样依赖该库（`django_config/settings.py:62-70`，Django `TestCase` 会建 `test_ylb` 库），因此**测试在本机也无法执行**。

**因此本报告不包含任何真实数据统计数字。** §6 改为交付**可直接执行的只读 SQL 脚本**，请运维在生产库上执行后回填。报告全文不出现任何真实身份证号。**在拿到这份统计之前，Phase 5（历史数据迁移）不具备开工条件。**

---

## 1. 需求理解

```
用户输入：身份证后六位（6 位）
数据库存储：身份证后六位（6 位）
约束变化：person.card 去掉 UNIQUE
身份判定：不再通过 card 判断「是否同一人」
```

这是一次**语义降级**，不是简单的长度截断：

| | 原系统 | 新系统 |
| --- | --- | --- |
| `card` 语义 | 身份唯一标识（完整身份证） | 非唯一辅助信息（后六位） |
| 唯一性 | 全局唯一（DB 约束） | 允许重复 |
| 是否可反查同一人 | 是（`card → Person`） | 否 |
| 「重复」的含义 | 数据错误 | **可能是正常数据** |

§18 的要求必须落实在评审中：**改造后「张三 123456 / 李四 123456」不是脏数据，不得当成数据错误报出。**

---

## 2. 当前实现

### 2.1 `card` 在哪里定义

**Model**（[apps/core/models.py:256](apps/core/models.py#L256)）：

```python
class Person(models.Model):
    card = models.CharField(max_length=255, unique=True, default=" ")
```

| 属性 | 值 |
| --- | --- |
| 类型 | `CharField`（字符串，**非整数**——见 2.5） |
| `max_length` | 255 |
| `unique` | **True** |
| `default` | `" "`（一个空格） |
| `null` / `blank` | 均未设置（即 NOT NULL、不允许空表单） |
| `db_index` | 未显式设置（`unique=True` 已隐含唯一索引） |
| validators | **无** |
| Meta constraints / indexes | 无（`Person.Meta` 只有 `db_table = "person"`） |

**迁移链**：

| migration | 操作 |
| --- | --- |
| `0001_initial.py:65` | `("card", models.CharField(max_length=255, unique=True))` —— **唯一约束诞生于此** |
| `0002` / `0003` | 未触碰 `person` |
| `0004_auto_20260921_1138.py:105-108` | `AlterField` 加 `default=' '`，**`unique=True` 原样保留** |
| `0005` / `0006` | 未触碰 `person`（只动 `report_draft`） |

**结论：`0001` 建立、`0004` 确认、此后无任何 migration 改动。生产库中该唯一约束必然存在**（前提是生产库由本仓库 migration 建立；若由 Laravel dump 导入，见 §5 P0-4）。

**关联表**：`ReportPerson`（[models.py:273-286](apps/core/models.py#L273-L286)）**没有任何唯一约束**，只有 `person_id` 上的 `db_index`。`Report` 亦无。全库**仅 3 个唯一约束**：`users.username`、`personal_access_tokens.token`、`person.card`。

### 2.2 `card` 在哪里被校验

| 层 | 位置 | 校验内容 |
| --- | --- | --- |
| **前端** | [personFields.js:31](../yl-frontend/src/config/personFields.js#L31) `RE_CARD = /^\d{17}[\dXx]$/` | 18 位、前 17 位数字、末位数字或 X/x。**不验校验位、不验生日、不验地区码**（源文件注释明确写「刻意不验」） |
| 前端文案 | personFields.js:89 | `身份证号应为18位，末位可以是数字或X` |
| 前端调用点 | `PersonTable.vue` `checkLine`410-411 / `exportCheck`446-447；`PersonTableMajor.vue` 269-270 / 303-304；`TeacherTable.vue` 223-224 | 仅这 3 个组件应用 |
| **后端** | **无任何格式校验** | 无正则、无长度检查、无 `RegexValidator`；只校验「非空」 |

> 关键事实：**该正则是全前端唯一的身份证正则**（`\d{17}` 全仓仅命中此一处），且**后端完全没有对应校验**。personFields.js:26 的文件头注释自己写着：「后端 `card` 是 CharField(max_length=255)、无格式约束，故这几条是纯粹的前端把关」。

**校验覆盖不完整**（这三点对改造范围有直接影响）：

1. **online 模块不校验**：`LeaderTable.vue:232`、`CrewTable.vue:262` 只判 `if (!row.card)`；
2. **管理员人员编辑不校验**：`admin/person.vue:128-136` 的 `card` 规则**只有 required**，且提交函数从不调用 `validate()`；
3. **无任何输入框设 `maxlength` / `minlength` / `type`** —— 长度只靠提交/导入时的正则兜底。

### 2.3 `card` 在哪里参与唯一性判断

**全部集中在 `store_people()` 一个函数内**（[apps/core/services.py:133-190](apps/core/services.py#L133-L190)）：

```python
existing_by_card = {                      # ← 第 136-141 行
    person.card: person
    for person in Person.objects.filter(card__in=[...])
}
names_by_card = {}                        # ← 第 142 行

for item in people:
    card = str(item.get("card", "")).strip()
    name = str(item.get("name", "")).strip()
    if not card or not name:
        return False, "身份证和姓名不能为空"
    ...
    if card in names_by_card and names_by_card[card] != name:        # ← 155-156 批内去重
        return False, f"{card}-{name},该身份证已被使用，请检查您的身份证和姓名是否输入正确"
    names_by_card[card] = name
    existing = existing_by_card.get(card)                            # ← 158 查已有 Person
    if existing and existing.name != name:                           # ← 159-160 跨批冲突
        return False, f"{card}-{name},该身份证已被使用，请检查您的身份证和姓名是否输入正确"

# 第二轮循环：命中已有 Person 则复用并刷新可选字段
person = saved_by_card.get(card) or existing_by_card.get(card)       # ← 167
if person:
    values.pop("name", None)      # ← 176 不覆盖已有姓名
    values.pop("user_id", None)   # ← 177 不改归属
```

语义固化在三条：

1. **同 card + 不同 name → 整批失败**（`@transaction.atomic`，全回滚）；
2. **同 card + 同 name → 复用同一个 `Person` 行**，只刷新可选字段，**永不改写 `name` / `user_id`**；
3. **无 card → 新建 `Person` 行**。

**这是全仓唯一把 `card` 当身份键的地方。** 除此之外，所有 `Person` 访问都走主键：

| 位置 | 访问方式 |
| --- | --- |
| `export_services.py:121` | `{person.id: person for ...}` |
| `person_export.py:127` | `{p.id: p for ...}` |
| `registration_form.py:197` | 按 `id__in` |
| `report_drafts.py:291` | `Person.objects.filter(pk=link.person_id)` |
| `services.py:216` | `Person.objects.filter(pk=link.person_id)` |
| `views.py:573` | `Person.objects.filter(pk=data.get("id"))` |
| `views.py:565` | 管理员检索 `card__icontains`（模糊，容忍重复） |

### 2.4 `card` 的四个写入入口（不是三个）

`store_people()` 被 **4 条**业务路径调用：

| # | 入口 | 位置 |
| --- | --- | --- |
| 1 | `POST /api/{school,city,province}/report/create` | [views.py:132](apps/api/views.py#L132) |
| 2 | `PUT /api/{school,city,province,admin,committee}/report/update` | [views.py:163](apps/api/views.py#L163)（admin/committee 走 `on_behalf=True`） |
| 3 | `POST .../report/drafts/{id}/submit`（新建） | [report_drafts.py:314](apps/core/report_drafts.py#L314) |
| 4 | 同上（驳回后重新提交） | [report_drafts.py:325](apps/core/report_drafts.py#L325) |

另有**第 5 条绕过 `store_people` 的直接写入**：

| 5 | `PUT /api/admin/person` | [views.py:569-580](apps/api/views.py#L569-L580) —— 直接 `setattr` + `save()`，**无任何唯一性预校验** |

这 5 处是 Phase 2/3 的**完整改造清单**（漏一处即漏一个入口）。

### 2.5 `card` 是字符串，历史数据转换必须保持字符串

`CardField = CharField`，DB 侧是 `varchar`。后六位**可能含前导 0**（如 `011234`），因此：

- 正确：`card[-6:]`
- **绝对禁止**：`int(card[-6:])` —— 会丢前导 0，且把 `011234` 变成 `11234`（5 位）
- 字段类型**不得**改成整数类型

### 2.6 当前"重复人员"的四个场景（现状记录）

| 场景 | 输入 | 当前行为 |
| --- | --- | --- |
| **A** | 同一次提交：张三 123456 + 张三 123456 | **通过**。第二批循环 `saved_by_card` 命中同一个 Person → `person` 表 **1 行**，`report_person` **2 行**（同一 `person_id`） |
| **B** | 同一次提交：张三 123456 + 李四 123456 | **整批失败**。命中 155-156 行 → `code:1`「该身份证已被使用…」，`Report` 与 `Person` 全部回滚 |
| **C** | 报名 A：张三 123456；报名 B：张三 123456 | **通过并复用**。B 命中 `existing_by_card` → 复用 A 建的同一 `Person` 行；`name` / `user_id` 不被覆盖，其他字段被刷新 |
| **D** | 报名 A：张三 123456；报名 B：李四 123456 | **整批失败**（B 被拒）。命中 159-160 行 → `code:1`；B 的 `Report` 一并回滚 |

**注意 C 场景的一个既有副作用**：跨报名复用会**刷新**同一个 `Person` 行。所以报名 B 提交的 `phone`/`school`/`head` 会改写报名 A 的人员信息（`name`/`user_id` 除外）。这个副作用在改造后需要重新定义归属语义。

---

## 3. 影响面

| 模块 | 文件 | 影响 | 等级 |
| --- | --- | --- | --- |
| **Model** | `apps/core/models.py:256` | `card` 去 `unique`、长度 255→6 | **必改** |
| **Migration** | 新增 1 个 `AlterField` | 删除 DB 唯一约束 | **必改** |
| **Service** | `apps/core/services.py:133-190` | `store_people` 的 card→Person 匹配逻辑整体失效 | **必改（核心）** |
| **Service** | `apps/core/report_drafts.py:314,325` | 调用方，需跟随 | 随 3 |
| **Service** | `apps/core/report_drafts.py:225,295` | draft 校验/序列化透传 `card` | 仅长度上限 |
| **View** | `apps/api/views.py:132,163,569` | 5 个写入入口 | **必改** |
| **View** | `apps/api/views.py:565` | 管理员按 `card__icontains` 检索 | 可用，但检索价值下降 |
| **前端-校验** | `personFields.js:31,89` | 18 位正则 → 6 位 | **必改** |
| **前端-组件** | 6 处 `card` 输入（见 §8.1） | placeholder / 长度 / 提示 | **必改** |
| **前端-Excel** | `PersonTable.vue` / `PersonTableMajor.vue` `exportCheck` | 18 位校验 | **必改** |
| **前端-Excel 模板** | 2 个 `参演人员导入模板*.xlsx` | C 列「身份证」表头与示例值 | 建议改 |
| **前端-照片** | `PersonTable.vue:630-645,775-785` | **已按后 6 位匹配照片** → 改造后必然撞号 | **P1，见 §5** |
| **前端-管理员** | `views/admin/person.vue:60,94-96,128-136` | 展示+编辑完整身份证 | 需确认（Q7） |
| **前端-展示** | `ShowPerson.vue:20,31`、`ShowOnlinePerson.vue:13,30` | 明文展示身份证 | 需确认（Q7） |
| **Excel 导入（后端）** | **不存在** | 后端无任何 Excel 导入代码（唯一上传路由白名单只放行图片/PDF） | 无需改 |
| **导出** | `export_services.py:57,243,252,262,265` | `指挥身份证`/`指导老师身份证` 两列 + `'` 前缀 | 需确认（Q8） |
| **导出** | `person_export.py`（`/api/export/person`） | **完全不导出 card** | **无需改** |
| **Admin** | `apps/core/admin.py` | 未注册 `Person`，无 Admin 界面 | 无需改 |
| **测试** | `tests/test_reports.py` 3 条 | 硬依赖 card 唯一 | **必改** |
| **数据库** | 生产库 `person.card` 唯一约束 + 存量数据 | 见 §6 | **P0** |

---

## 4. 当前逻辑依赖图

```
用户输入完整身份证
      ↓
前端 RE_CARD 正则（18 位）           ← PersonTable / PersonTableMajor / TeacherTable
      ↓                              （online 模块与 admin/person 不校验）
POST /report/create | PUT /report/update | draft submit
      ↓
store_people()                        ← 【唯一】以 card 为身份键的地方
      ├── existing_by_card {card: person}   ← card 当字典键
      ├── names_by_card   {card: name}      ← card 当字典键
      ├── 同 card 不同 name → 整批失败
      └── 同 card 同 name → 复用 Person 行
      ↓
Person  (card UNIQUE)  ←──── DB 唯一约束兜底
      ↓
ReportPerson (report_id, person_id, position, type)   ← 无唯一约束
      ↓
导出：/api/export/person 不含 card；/api/export/data 含 card 两列
```

---

## 5. 新需求后的冲突点

### P0 —— 会直接出错或阻塞上线

**P0-1｜`store_people` 的两处冲突校验会让合法数据整批失败**

`services.py:155-156` 与 `159-160` 在"同 card 不同 name"时返回失败并回滚整批。后六位可重复后，**「张三 123456 / 李四 123456」会被系统当成错误拒绝**——而这正是新需求明确要允许的场景（待 Q1 确认）。此处不改，新需求无法落地。

**P0-2｜`existing_by_card` 字典推导会静默吞掉重复行（本条最危险）**

```python
existing_by_card = {person.card: person for person in Person.objects.filter(card__in=[...])}
```

`card` 不再唯一后，`card__in` 会返回多行同 card 的记录，**字典推导保留最后一行、静默丢弃其余**。后果：

- 本批人员**全部**被挂到同一个（最后一行的）`Person` 上 → `report_person` 出现 N 行指向同一 `person_id`；
- 与 159-160 行叠加：只要那"幸存"的一行姓名与本批不同，**整批被拒**；
- **不报错、不写日志**，属于静默数据错误。

**这一行必须在 Phase 3 整体重写，不能只删 UNIQUE 了事。**

**P0-3｜历史数据截断后必然产生大量重复，而当前 DB 约束会拒绝写入**

存量完整身份证 → 后六位后，重复是**数学必然**（见 §6/§17）。只要 UNIQUE 还在，`UPDATE person SET card = right(card,6)` 会**大面积报唯一冲突**。因此**迁移顺序不可颠倒**：必须先删约束、再转数据。

**P0-4｜唯一约束的实际名称与类型需在生产库核对**

本仓库 migration 建的库，约束名由 Django introspection 决定；但若生产库是**由 Laravel dump 导入**（本仓库存在 `import_laravel_data` 命令，且 `DATABASE_MAPPING.md` / `MIGRATION_INVENTORY.md` 记载了双轨迁移），则该唯一约束可能是 Laravel 命名的 `person_card_unique`（唯一索引，而非 Django 的约束形式）。`AlterField(unique=False)` 依赖 introspection 正确定位，**上线前必须用 `\d person` 或 §6 的 SQL 核对实际约束名与类型**，不能假定。

**P0-5｜`default=" "` 这个补丁在去 UNIQUE 后语义变化**

`0004` 给唯一列 `person.card` 加 `default=" "` 是为兼容 GaussDB（PG 9.2.4 兼容模式）把空串当 NULL。此前该默认值实际不可用（第二条空 card 必然撞唯一约束）。**去掉 UNIQUE 后，`card` 缺失将静默写入一个空格且可以无限重复**，把"漏填"彻底隐藏。Phase 1 必须同时决定此默认值的去留（docs/迁移审计报告.md 缺陷 2 已预警）。

### P1 —— 业务逻辑需要重新定义

**P1-1｜前端照片上传**已经**在用后 6 位做匹配键（本次新发现）**

[PersonTable.vue:630-645](../yl-frontend/src/components/elementary/PersonTable.vue#L630-L645) 按 `card.substring(len-6)` 匹配照片行，且源码注释已记录该问题：

> ```
> // 【第十二届】收集全部命中再判，而不是 findIndex 取第一个：后 6 位重复时
> // 原来会静默把照片写到第一行并报成功，第二个人看起来是「没传上」。
> ```

[PersonTable.vue:775-785](../yl-frontend/src/components/elementary/PersonTable.vue#L775-L785) 的文件名规则同样是后 6 位：

```js
const expected = isTeacher ? item.name + tail : tail
// 教师照片：姓名+后6位.jpg    学生照片：后6位.jpg
```

**改造后 `card` 本身就是后 6 位**，于是：

- 学生照片的期望文件名**退化为一个纯 6 位串**，同班同名尾号不同学生**完全无法区分**；
- 教师照片靠 `姓名+后6位` 尚可区分，但同名学生仍会撞。

即：**新需求会让"后 6 位撞号"从一个边缘情况变成常态**，而前端已存在的兜底（收集全部命中）只能"不静默成功"，不能解决歧义。**照片命名与匹配规则必须一并重新设计**，否则会出现"照片传上去了但传错人"——这比报错更糟。**这是本次改造中风险最高、最容易被漏掉的一环。**

**P1-2｜`Person` 行的身份语义与归属刷新**

场景 C 下 `Person` 行被跨报名复用并刷新 `phone`/`school`/`head`。后六位不再唯一后，"哪一行算同一个人"失去依据，必须二选一（见 Q2）。这直接决定 `store_people` 是"复用"还是"新建"。

**P1-3｜`PUT /api/admin/person` 的历史回归会"意外消失"**

现状：改 `card` 撞唯一键 → Django **500**，而 Laravel 原版是 **200 + `json_fail('修改失败！')`**，`DJANGO_REGRESSION_AUDIT.md` 第 12 项，是一条**已确认的回归**。

去掉 UNIQUE 后，500 因"不再有冲突"而消失——**但这不等于修复**。这是**行为变化**而非**正确修复**：原版之所以返回失败文案，是因为唯一约束存在。新规则下唯一约束不该存在，因此**既不该 500、也不该报"修改失败"**，而应正常成功（或按 Q7 决定是否限制）。**必须显式决策并补测试，不能让它在无人察觉的情况下"消失"。**

**P1-4｜票务子系统（相邻影响）**

`ticket_subscribe.card`（[models.py:443](apps/core/models.py#L443)）是**另一张表、本来就是非唯一**，无 DB 约束，仅靠 `TicketController` 的 `lockForUpdate()` + `card` 去重（[views.py:1033](apps/api/views.py#L1033)）。若观展预约也改成只收后六位，**同票同尾号的不同人会被判为"已预约"**。该表本次**不需要 DB 变更**，但需要业务确认预约去重口径是否同步调整。

### P2 —— 需要调整但影响可控

- **P2-1｜`card` 字段长度**：255 → 6。注意 GaussDB 的 varchar 按**字节**计长（中文 ×3）；6 位数字/字母为 6 字节，安全。
- **P2-2｜管理员检索**（[views.py:565](apps/api/views.py#L565)）：`card__icontains` 在 6 位串上仍可用，但命中率大幅上升，检索结果噪声变大。
- **P2-3｜`report_drafts.py:225`** 只透传不校验格式，长度上限 255 → 需收紧为 6。
- **P2-4｜Excel 模板** C 列表头为「身份证」、示例为 18 位。导入解析依赖隐藏英文键行 `card`，**改表头中文文案不会破坏导入**，但要同步示例值。
- **P2-5｜重复导出**：`/api/export/data` 的 `指挥身份证`/`指导老师身份证` 两列会给 card 加 `'` 前缀（防 Excel 公式/精度问题）。该行为为原版既有，6 位串下**无实际必要**，但**建议保留不动**（原版行为优先）。

---

## 6. 历史数据统计（本次未执行 —— 交付只读 SQL）

> **本机未连接任何数据库，本节无数字。** 以下脚本**全部为 SELECT，无 UPDATE / DELETE / ALTER**，请在生产库执行后回填。输出已做掩码，不暴露完整身份证号。

### 6.1 总量与空值

```sql
SELECT count(*) AS person_total,
       count(*) FILTER (WHERE card IS NULL OR btrim(card) = '') AS card_empty
FROM person;
```

### 6.2 长度分布

```sql
SELECT length(btrim(card)) AS card_len, count(*) AS cnt
FROM person
GROUP BY 1 ORDER BY 2 DESC;
```

### 6.3 重复 card（**这是迁移风险的核心指标**）

```sql
-- 当前已存在的重复（改造前就有的脏数据，或历史导入造成）
SELECT card, count(*) AS person_count
FROM person
GROUP BY card HAVING count(*) > 1
ORDER BY 2 DESC;
```

```sql
-- 同 card 不同 name（当前被唯一约束"挡住"的潜在冲突）
SELECT card, count(DISTINCT name) AS distinct_names, count(*) AS rows
FROM person
GROUP BY card HAVING count(DISTINCT name) > 1
ORDER BY 2 DESC;
```

### 6.4 截断后会产生多少重复（**迁移风险预演，最关键**）

```sql
-- 不改数据，只预测：按后六位分组，统计会新增多少冲突
SELECT right(btrim(card), 6) AS last6,
       count(*)               AS person_count,
       count(DISTINCT card)   AS distinct_full_cards,
       count(DISTINCT name)   AS distinct_names
FROM person
WHERE card IS NOT NULL AND btrim(card) <> ''
GROUP BY 1
HAVING count(*) > 1
ORDER BY 2 DESC;
```

```sql
-- 汇总：全体人数、截断后不同后六位个数、预计冲突人数
SELECT count(*)                                   AS total_rows,
       count(DISTINCT right(btrim(card), 6))      AS distinct_last6,
       count(*) - count(DISTINCT right(btrim(card), 6)) AS expected_collisions
FROM person
WHERE card IS NOT NULL AND btrim(card) <> '';
```

### 6.5 异常数据

```sql
-- 长度既非 18 也非 6（脏数据、测试数据、占位空格）
SELECT length(btrim(card)) AS card_len, count(*) AS cnt
FROM person
WHERE btrim(card) <> '' AND length(btrim(card)) NOT IN (18, 6)
GROUP BY 1 ORDER BY 2 DESC;

-- 掩码示例（只取首尾，不输出完整号码）
SELECT id, left(btrim(card), 2) || '****' || right(btrim(card), 2) AS masked_card,
       length(btrim(card)) AS card_len
FROM person
WHERE btrim(card) <> '' AND length(btrim(card)) NOT IN (18, 6)
LIMIT 20;
```

### 6.6 约束现状核对（配合 P0-4）

```sql
SELECT conname, contype, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'person'::regclass;

SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'person';
```

**回填格式建议**：

```
长度分布：18 位=___   6 位=___   其他=___
空值/NULL：___
当前重复 card 组数：___  涉及行数：___
截断后不同后六位个数：___   预计冲突行数：___
同 card 不同 name 组数：___
```

**在拿到 6.4 的数字之前，无法评估 Phase 5 的风险等级，也无法确认"重复量是否在可接受范围"。**

---

## 7. 新业务规则待确认项（需上级答复，审计阶段不作决定）

> 以下每条都给出**建议**，但**建议不等于决定**。未获答复前不进入实施。

### Q1｜相同后六位、不同姓名是否允许？
后六位不再唯一即意味着允许。**若允许 → `card` 必须去 UNIQUE（确定）**；且 P0-1 的两处冲突校验必须移除。
**建议：允许。**

### Q2｜相同后六位 + 相同姓名，跨报名是否复用同一个 `Person`？（**最关键**）

- **方案 A（复用）**：沿用现有"同 card 同 name 复用"逻辑，仅把匹配键从完整身份证换成后六位。→ 后六位相同且姓名相同即视为同一人。**风险**：后六位 + 姓名仍会撞（同班同名同尾号并非不可能），且跨报名会互相刷新联系方式。
- **方案 B（每次报名独立建档）**：`store_people` 一律 `create`，不复用、不查重。→ 语义最清晰，`Person` 退化为"报名内的一个人"。**风险**：`person` 表行数膨胀；同一学生的历史信息不再聚合。

**审计倾向**：**方案 B 更贴合"后六位不具备身份唯一性"这一前提**——既然不能唯一标识一个人，就不该用它去聚合一个人。但 `Person` 是全局人员实体、`user_id` 承载归属，方案 B 会改变数据形态，**必须由上级定**。**在 Q2 答复前，Phase 3 无法开工。**

### Q3｜同一报名中出现相同后六位是否允许？
即场景 B。若 Q1 为"允许"，则此处应允许。**需确认是否改成"允许但给出前端提示"。**

### Q4｜后六位允许的字符范围？
- 纯数字 6 位？（身份证后六位可能是 `12345X`——**末位可以是 X**！）
- 还是数字 + 字母？

**注意**：完整身份证末位可为 `X`，故"后六位"**天然可能是 `12345X` 这类含字母的值**。现有前端正则 `^\d{17}[\dXx]$` 已容许末位 X，**因此新规则若限定"纯数字"会与真实身份证冲突**。这一条必须问清，否则会出现"用户照身份证填却被前端拒绝"。
**建议：`^[0-9Xx]{6}$` 或明确"只要 6 位字符"。**

### Q5｜历史完整身份证是否全部截取后六位？
**建议：是**，`card = right(btrim(card), 6)`，**字符串处理，禁止转整数**。
需同时确认：非 18 位的脏数据（§6.5）如何处置——截断、置空、还是保留？

### Q6｜历史完整身份证是否允许保留在备份数据库？
**建议：必须**。截断是**不可逆**操作（18 位 → 6 位后无法还原），**迁移前必须全量备份且备份需长期留存**。

### Q7｜管理员是否还能看到后六位？前端 3 处明文展示是否需要脱敏？
现状**无任何脱敏**：`admin/person.vue:60`（列表）、`ShowPerson.vue:20,31`、`ShowOnlinePerson.vue:13,30` 全部明文展示。
改为 6 位后敏感度大降，**但需确认是否顺势脱敏或限制展示范围**。

### Q8｜导出是否仍然导出后六位？
`/api/export/data`（及 committee 导出）含 `指挥身份证` / `指导老师身份证` 两列（[export_services.py:57](apps/api/export_services.py#L57)），值为 `"'" + card`。
**建议保留列、值变为 6 位后六位**（去掉 `'` 前缀亦可，但原版行为优先，建议不动）。

### Q9（审计新增）｜票务预约是否同步改为后六位？
见 P1-4。若不改，则票务仍收完整身份证，**与报名口径不一致**，需明确。

---

## 8. 实施阶段任务计划

> **前提：Q1–Q4 得到答复后方可开工。Q2 未定则 Phase 3 阻塞。**

### Phase 1｜数据库模型与约束

**改动**：
- `apps/core/models.py:256` → `card = models.CharField(max_length=6, unique=False, default=...)`（`default` 去留见 P0-5，需一并决策）
- 新增 migration：`AlterField(model_name='person', name='card', ...)`

**关键风险**：`AlterField(unique=False)` 会 `DROP` 唯一约束。**执行前必须按 §6.6 核对生产库实际约束名**（P0-4）。
**GaussDB 注意**：varchar 按字节计长，6 位数字安全；若 Q4 允许字母亦安全。

**验证**：迁移后 `\d person` 确认无唯一约束；`SELECT count(*) FROM person WHERE card=''` 应为 0。

### Phase 2｜输入校验（统一 5 个入口）

| 入口 | 文件 |
| --- | --- |
| 创建报名 | `views.py:132` |
| 修改报名（含 admin/committee on-behalf） | `views.py:163` |
| 草稿提交（新建 / 驳回重提） | `report_drafts.py:314,325` |
| 管理员人员修改 | `views.py:569-580` |
| 前端 6 处输入 + 2 处 Excel 导入 | 见 §8.1 |

**后端目前无任何 card 格式校验**（§2.2），Phase 2 需要**新增**统一校验函数（长度 6 + Q4 确认的字符集），并在上述 5 处调用。**这是新增逻辑，不是修改逻辑。**

### Phase 3｜重构 `store_people` 匹配逻辑（**核心，依赖 Q2**）

- 删除 `existing_by_card` 字典推导（P0-2，**必须整体重写而非删约束**）
- 删除 155-156 / 159-160 两处冲突校验（P0-1）
- 按 Q2 结论实现方案 A 或 B
- 若采用方案 B：`Person` 行数将显著增长，需评估 `person` 表规模与 `report_person` 关联语义

### Phase 4｜查询与展示逻辑

逐个复核（本报告 §2.3 已给出完整清单，仅 2 处涉及 card，其余均走主键）：
- `services.py:158,167`（Phase 3 覆盖）
- `views.py:565`（检索语义）
- `report_drafts.py:225,295`（长度上限）
- **确认无 `get(card=...)`** —— 已确认全仓不存在，故**无 `MultipleObjectsReturned` 风险**（这是本次审计的有利结论）

### Phase 5｜历史数据迁移（**依赖 §6 统计**）

```
数据库备份（Q6：必须）
    ↓
执行 migration 删除 UNIQUE        ← 顺序不可颠倒（P0-3）
    ↓
dry-run：统计截断后将产生的重复数
    ↓
UPDATE person SET card = right(btrim(card), 6)   ← 字符串，禁止 int()
    ↓
迁移后校验：长度分布 / 无空值 / 重复量与 dry-run 一致
```

**必须**：备份、migration、迁移脚本、dry-run、迁移后校验五项齐备。

### Phase 6｜前端

- `personFields.js:31,89`：正则 18 位 → Q4 确认的 6 位；错误文案改写
- 6 处输入：placeholder、加 `maxlength="6"`
- `PersonTable.vue` / `PersonTableMajor.vue` 的 `exportCheck` + `checkLine`
- **P1-1 照片命名与匹配规则重新设计**（学生照片纯后 6 位命名已不可用）
- Excel 模板 C 列表头与示例值
- `admin/person.vue` 展示/编辑（依 Q7）

### Phase 7｜测试

**必改（3 条，均在 `tests/test_reports.py`）**：

| 测试 | 现状 | 改造后 |
| --- | --- | --- |
| `test_reused_card_keeps_original_identity_but_refreshes_optional_fields` (L171) | 断言 card 复用保持原 `name`/`user_id` | **改为 Q2 的新语义** |
| `test_failed_create_rolls_back_people_created_earlier_in_request` (L29) | 断言同 card 不同 name → `code:1` + 回滚 | **改为允许（或按 Q1/Q3 新语义）** |
| `test_failed_update_keeps_report_links_and_people_unchanged` (L47) | 同上 | **同上** |

**需新增**：后六位重复+不同姓名 / 后六位重复+相同姓名 / 跨报名同后六位 / 同报名同后六位 / Excel 重复后六位 / 管理员修改 card / 导出 card / 历史数据迁移。

**无需改动**：其余 14 处涉及 card 的测试均只把 card 当普通字符串用（含 `test_export_person.py` 全部、`test_exports.py`、`test_export_form.py`、`test_report_drafts.py` 等）。注意 `test_files_scan_live.py` 的 card 属于 `Crew`/`Leader`（本就非唯一），`test_tickets_pagination_openapi.py` 属于 `TicketSubscribe`，均与本改造无关。

**注意**：`test_data/member_import/*.md` 中记载的预期 —— 「同 card 不同 name → `该身份证已被使用`」（`MEMBER_IMPORT_TEST_CASES.md:247`）—— **将随本改造失效，该文档需同步更新**。

### Phase 8｜生产部署

```
备份 → 部署代码 → 执行 migration → 历史数据迁移 → 数据校验 → API 测试 → 前端测试 → 生产验证
```

**与 Phase 5 的顺序不可颠倒**：先删约束，再转数据。

---

## 9. 最终结论

### 9.1 十五问直答

| # | 问题 | 结论 |
| --- | --- | --- |
| 1 | 当前 card 唯一约束在哪里？ | **`apps/core/models.py:256`** `unique=True`；DB 侧由 `0001_initial.py:65` 建立、`0004:105-108` 保留。全库仅 3 个唯一约束之一 |
| 2 | 哪些代码依赖 card 唯一？ | **仅 `store_people`（services.py:133-190）一处真正依赖**。其余全部走主键。另有 3 条测试、1 处管理员写入路径、6 处前端输入、2 处 Excel 校验 |
| 3 | 去掉 UNIQUE 后哪些地方会**直接出错**？ | ①`existing_by_card` 字典推导静默吞重复行（P0-2，最危险）；②155-160 两处冲突校验拒绝合法数据（P0-1）；③历史数据截断撞唯一约束（P0-3，故顺序不可颠倒）。**无 `get(card=)`，故无 `MultipleObjectsReturned`** |
| 4 | 哪些只是业务逻辑需要调整？ | 管理员接口回归"消失"（P1-3）、Person 归属刷新语义（P1-2）、票务口径（P1-4）、字段长度、检索语义、导出列 |
| 5 | 历史数据转换后产生多少重复？ | **未知 —— 本机无数据库，未能执行。** §6.4 已交付可直接运行的只读 SQL，**需运维回填后方可评估** |
| 6 | 是否存在数据迁移风险？ | **存在，且是本次最大风险**。截断不可逆；重复是数学必然；须先删约束再转数据；必须全量备份（Q6） |
| 7 | 前端有哪些 18 位身份证校验？ | **仅 1 处正则** `personFields.js:31 /^\d{17}[\dXx]$/`。调用点 3 个组件（PersonTable / PersonTableMajor / TeacherTable）；**online 模块与 admin/person 不校验**；**无 maxlength** |
| 8 | Excel 有哪些身份证校验？ | **后端无 Excel 导入代码**。前端 `exportCheck` 调 `checkPersonBasics` → 同一个 18 位正则。**无任何重复 card 检测** |
| 9 | API 有哪些身份证唯一性假设？ | 5 个写入入口（4 条经 `store_people`，1 条 `PUT /api/admin/person`）；读取侧无唯一性假设 |
| 10 | Person 模型在新规则下是否仍合理？ | **仍可用但需重新定义身份**。建议保留 `Person`（`ReportPerson` 已用 `person_id` 区分关联）、新增**业务唯一标识**（学籍号/手机号）或按 Q2 方案 B 退化为报名内人员。**审计不重构模型，仅指出风险** |
| 11 | 还缺哪些上级确认？ | **Q1–Q9，其中 Q2（跨报名是否复用）与 Q4（字符范围，涉及末位 X）为阻塞项** |
| 12 | 实施分几个阶段？ | **8 个**（Phase 1–8） |
| 13 | 每阶段改哪些文件？ | 见 §8 各 Phase |
| 14 | 每阶段如何验证？ | 见 §8 各 Phase 的「验证」 |
| 15 | 是否可以安全上线？ | **当前不可以。** 阻塞于：§6 数据统计未取得、Q1–Q4 未答复、P1-1 照片匹配规则未重新设计。三项齐备后可安全上线 |

### 9.2 五类归档

**【确定需要修改】**
1. `apps/core/models.py:256` —— `card` 去 `unique`、长度 6
2. 新增 migration —— 删除 DB 唯一约束
3. `apps/core/services.py:133-190` —— `store_people` 匹配逻辑整体重写（含 P0-2 字典推导）
4. `apps/api/views.py:132,163,569-580` —— 5 个写入入口
5. `apps/core/report_drafts.py:225,314,325` —— 长度上限 + 调用方
6. `personFields.js:31,89` —— 18 位正则与文案
7. 前端 6 处输入组件 + 2 处 `exportCheck`
8. `tests/test_reports.py` 3 条测试
9. `test_data/member_import/*.md` 预期文档

**【确定不需要修改】**
1. `apps/api/person_export.py`（`/api/export/person`）—— **完全不导出 card**
2. `ReportPerson` / `Report` —— 无唯一约束，键为 `person_id`
3. `apps/core/admin.py` —— 未注册 `Person`
4. 后端 Excel 导入 —— **不存在该功能**
5. `import_laravel_data.py` / `clean_report_combos.py` —— 不读 `card`
6. `tests/` 中其余 14 处 card 引用、`Crew.card` / `Leader.card` / `TicketSubscribe.card`（本就非唯一）
7. 其余 `Person` 访问路径（全部走主键，共 7 处）

**【需要上级确认】**
Q1 同后六位不同姓名 · **Q2 跨报名是否复用（阻塞）** · Q3 同报名同后六位 · **Q4 字符范围（阻塞，含末位 X）** · Q5 历史截断范围 · Q6 备份留存 · Q7 管理员可见性/脱敏 · Q8 导出 · Q9 票务口径

**【存在数据迁移风险】**
完整身份证 → 后六位**不可逆**；重复为数学必然（具体量待 §6.4）；`int()` 转整数会毁掉前导 0；先转数据后删约束会大面积报错（顺序不可颠倒）；`default=" "` 在去 UNIQUE 后将静默隐藏漏填。

**【存在生产阻断风险】**
1. **P0-2** `existing_by_card` 静默吞掉重复行 → 无报错的数据错误
2. **P0-3** 迁移顺序颠倒 → 大面积唯一约束冲突
3. **P1-1** 前端照片按后 6 位匹配/命名 → **改造后必然撞号，可能传错人且不报错**（本次新发现，最易遗漏）
4. **P0-4** 未核对生产库实际约束名 → migration 可能无法正确定位约束

---

## 10. 本次审计的边界

- **未修改任何代码、配置、数据库、构建产物**，未执行 `migrate` / `ALTER` / `UPDATE` / `DELETE`，未提交 Git。
- 报告仅创建 `ID_CARD_LAST6_AUDIT.md` 一个文件。
- **§6 历史数据统计未执行**（本机无数据库连接），已交付只读 SQL 待回填 —— 这是本报告的**唯一缺口**，也是 Phase 5 的前置条件。
- 曾用于比对的 Laravel 原版源码位于本机 `ylb_intial\ylb_api`，本次未再访问（相关结论引自既有 `DJANGO_REGRESSION_AUDIT.md`）。
