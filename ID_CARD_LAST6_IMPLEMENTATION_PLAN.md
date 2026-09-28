# 身份证后六位改造 · 实施前最终审计与实施计划

> 审计日期：2026-09-23
> 对象：`E:\github\yilinbei`（Django 后端）+ `E:\github\yl-frontend`（Vue 3 前端）
> 性质：**只读审计**。未修改 Python / Model / migration / Vue / JS / 数据库 / 配置；未执行 `migrate`、未删除 UNIQUE、未改历史数据、未 `npm build`、未 commit、未 push。
> 业务规则以本次任务书第五节**冻结条款**为准（15 条），不再重新解释。
> 前序文档：`ID_CARD_LAST6_AUDIT.md`（初版审计，其 §7 的 Q1–Q9 已被本次冻结条款解答）。

---

# 1. 最终结论

## **BLOCKED**

实施被 **3 项**真实阻塞，全部是**决策/数据**阻塞，**没有任何一项是技术不可行**：

| 编号 | 阻塞项 | 阻塞范围 | 解除方式 |
| --- | --- | --- | --- |
| **B1** | 历史数据统计**未取得**（本机无数据库连接） | 阻塞 §6 数据迁移方案的风险量化与 Phase 5 验证 | 运维在生产库执行 §6.1 的只读 SQL 并回填 |
| **B2** | **`person_id` 复用的授权规则未定义** | **阻塞 Phase 3（核心）** | 业务/安全负责人拍板 §2.4 的两个选项 |
| **B3** | **`card` 的 `default=" "` 去留未定** | 阻塞 Phase 1 migration 定稿 | 依任务书要求在 §5.3 标记 BLOCKER，需业务确认 |

**B2 是最关键的一条**：冻结条款第 9 条要求"复用优先依赖明确的 `person_id`"，但**未规定谁能复用谁的 `Person`**。当前系统**不存在**这个授权面（原因见 §2.3），一旦按第 9 条开始信任客户端 `person_id`，就**新开了一个越权写入通道**。这不是实现细节，是必须先定的安全前提。

**不阻塞但需拍板**：照片匹配键的具体替代方案（§7），已按冻结条款第 13 条给出推荐方案，可先行实施。

---

# 2. 首先确认 person_id 链路（本次审计最重要项）

## 2.1 结论

# **已有（但不完整）**

三个层面各自成立，且**后端已经把链路修到只差最后一步**：

| 环节 | 状态 | 证据 |
| --- | --- | --- |
| 前端**持有** Person PK | **有** | 编辑回填时读 `p.person_info.id`（`OrchestraForm.vue:1262-1308`、`ProgramForm.vue:872-912`），`person_info` 由后端 `report_dict` 的 `model_dict(Person)` 生成，含 `id` |
| 前端**发送** Person PK | **有**（键名是 `id`，**不是** `person_id`） | [draftPayload.js:117](../yl-frontend/src/services/draftPayload.js#L117) `id: has(r.id) ? String(r.id) : null` |
| 后端**接收并校验** | **有** | [report_drafts.py:47](apps/core/report_drafts.py#L47) `PERSON_FIELDS` 含 `id`；[:223](apps/core/report_drafts.py#L223) `result["id"] = str(item["id"])` |
| 后端**传递**到写入点 | **有** | `Submission.people`（[:161-163](apps/core/report_drafts.py#L161-L163)）→ `store_people` |
| 后端**使用**它做身份判定 | **没有** | [services.py:171](apps/core/services.py#L171) `values.pop("id", None)` —— **丢弃** |
| 链路可被**打断** | **会** | Excel 导入、手工加行、新建行这三处 `id` 丢失（见 §2.5） |

**一句话**：链路是通的，只差 `store_people` 愿意信它；但**信它的前提（授权规则）没定**（B2）。

## 2.2 前端是否已经持有 `person_id`

**持有，但键名叫 `id`，且**全前端从不使用 `person_id` 这个键名**。

`personToPayload`（[draftPayload.js:105-141](../yl-frontend/src/services/draftPayload.js#L105-L141)）逐键发出：

```
id (String|null) · name · card · age · gender · school · phone
instrument · head · major · other · remark · type · position
```

**`id` 就是 `Person.id`（Person 主键），字符串化。** 该文件 108-116 行的注释记录了一次真实事故，是本次审计最有力的证据：

> ```
> 【键名是 id，不是 person_id】后端 apps/core/report_drafts.py 的 PERSON_FIELDS
> 是**精确白名单**，多一个键就 400 …
> 原先发的是 person_id —— 于是**只要报名表里有一个人，暂存和提交就全部 400**，
> 空表反而通过。已用真实后端复现
> ```

即：**曾用过 `person_id` 这个键名，因后端白名单叫 `id` 而全线 400，已改回 `id`**。

各组件行对象：
- 五个表格组件**没有任何一处**给行赋 `id`；`id` 只可能来自父组件 `props.showdata`（编辑回填时由 `person_info` 展开带入）。`add()` 一律 push `{}`（`PersonTable.vue:351-353`、`PersonTableMajor.vue:210-212`、`TeacherTable.vue:164-166`、`LeaderTable.vue:149-151`、`CrewTable.vue:171`）。
- `admin/person.vue:154-157` `modify(row)` 深拷贝整行（含 `id`），`editSubmit()` 把含 `id` 的表单 PUT 给 `/api/admin/person`。

## 2.3 后端是否已经接收 `person_id`

| 接口 | 请求字段 | 是否存在 Person PK | 是否可信 | 最终如何确定 Person |
| --- | --- | --- | --- | --- |
| `POST /api/{school,city,province}/report/create` | `person[]`（**无字段白名单**） | 前端会发 `id` | **被丢弃** | `store_people` **按 `card` 匹配** |
| `PUT /api/{school,city,province,admin,committee}/report/update` | 同上 | 同上 | **被丢弃** | 同上 |
| `POST .../report/drafts`、`PUT .../drafts/{id}` | `payload.person[]`（**精确白名单**） | `id` 在白名单内 | 透传存储 | — |
| `POST .../drafts/{id}/submit` | `{version}` | 取自草稿 payload | 透传至 `store_people` | `store_people` **按 `card` 匹配** |
| 同上（驳回重提） | 同上 | 同上 | 同上 | 同上 |
| `PUT /api/admin/person` | `{id, ...Person字段}` | `id` = Person PK | **直接使用**（按 `pk` 定位） | `Person.objects.filter(pk=data.get("id"))` |

**关键安全事实（当前是安全的）**：`ReportPerson` 的 `person_id` **完全由服务端计算**——[services.py:189](apps/core/services.py#L189) `result.append({"person_id": person.id, ...})`，再由 `attach_report_people` 落库。**客户端无法影响任何 `ReportPerson.person_id`**。同理 `values.pop("user_id", None)`（[:177](apps/core/services.py#L177)）与创建分支的 `values["user_id"] = user.id` 使客户端**无法越权设置 `user_id`**。

> 即：**今天的 `person_id` 链路没有越权面，因为客户端的话根本不被听。**

## 2.4 如果已经存在 person_id，能否直接作为 Person 唯一引用？

**技术上可以**（`Person.id` 是 `BigAutoField` 主键，稳定唯一，冻结条款第 8 条已确认它是唯一内部标识）。

**但不能不加授权就启用。** 一旦 `store_people` 开始信任客户端 `person_id`：

- `store_people` 的复用分支会**改写目标 `Person` 行的可变字段**（`school`/`phone`/`head`/`instrument`…，见 [:180-184](apps/core/services.py#L180-L184)）；
- 而 `Person.user_id` 是唯一归属字段（`models.py:255`，`IntegerField`，非空）；
- 于是**甲校可以提交乙校的 `person_id`，改写乙校的人员信息**——这是一个今天不存在的越权通道。

**必须回答的问题：复用的授权谓词是什么？** 两个选项：

| 选项 | 规则 | 优点 | 缺点 |
| --- | --- | --- | --- |
| **B2-A（推荐）** | 仅当 `Person.user_id == 生效用户 id` 才允许复用；admin/committee 代报名时以**报名归属 `report.user_id`** 为准 | 无越权面；归属语义清晰 | 历史跨校数据编辑时可能报错，需逐个解释 |
| **B2-B** | 不校验归属，任何合法 `person_id` 都可复用 | 与"今天靠同 card 跨校复用"的宽松度一致 | 明确引入跨租户写入；必须额外限制可写字段 |

**取值不匹配时的行为也必须一并定**（这一半同样不能默认）：

| 选项 | 行为 | 风险 |
| --- | --- | --- |
| **B2-X（推荐）** | 返回 `code:1` 明确错误（如"人员不属于当前单位"） | 用户可能困惑，但**不会静默产生重复人员** |
| **B2-Y** | 降级为"无 person_id"，按第 10 条**新建** `Person` | 静默产生重复人员，用户无法察觉 |

> 推荐 **B2-A + B2-X**。理由：静默新建比明确报错更糟——重复人员一旦产生就再难发现，而明确的错误至少可被解释和纠正。

## 2.5 如果不存在 person_id —— 最小化补充方案（供参考，不实施）

链路整体已存在，**若 B2 选择 B2-A + B2-X，则补充面极小**：

| 需要做的事 | 位置 |
| --- | --- |
| 后端：`store_people` 不再 `pop("id")`，改为按 §5 的算法解析 | `apps/core/services.py:171` |
| 后端：`create_report`/`update_report` 的 `person[]` 无字段白名单，建议补白名单（与 draft 对齐） | `apps/api/views.py:122,159` |
| 前端：**Excel 导入保留已有 `id`** —— 目前 `importExcel` 重建行时丢掉 `id` | `PersonTable.vue:527-543`、`PersonTableMajor.vue:354-365` |
| 前端：**手工加行**本就不该有 `id`（新人员）—— 保持现状即可 | 五个组件 `add()` |
| 前端：`oldHeads[item.card]` 按 card 回填头像的键在 6 位 card 下会撞号 | `PersonTable.vue:521-524,541` |
| 防越权 | 由 B2 的授权谓词承担，**不是**前端职责 |

**不得自行实现。** 上表仅为范围界定。

---

# 3. `store_people()` 现状审计（冻结条款第 7/8/9/10 条的直接对象）

## 3.1 全部 card 相关位置逐条定性

| 位置 | 代码 | 把 card 当身份？ | 当复用条件？ | 当唯一键？ | 只是搜索/展示？ | 是否需要修改 |
| --- | --- | --- | --- | --- | --- | --- |
| [services.py:136-141](apps/core/services.py#L136-L141) | `existing_by_card = {person.card: person for ...}` | **是** | **是** | **是（隐含）** | 否 | **必改（整体重写）** |
| [services.py:139](apps/core/services.py#L139) | `filter(card__in=[...])` | **是** | **是** | 否 | 否 | **必改** |
| [services.py:155-156](apps/core/services.py#L155-L156) | `names_by_card[card] != name → 失败` | **是** | **是** | **是** | 否 | **必改（删除）** |
| [services.py:158-160](apps/core/services.py#L158-L160) | `existing.name != name → 失败` | **是** | **是** | **是** | 否 | **必改（删除）** |
| [services.py:163,167](apps/core/services.py#L163-L167) | `saved_by_card` / `existing_by_card.get(card)` | **是** | **是** | **是** | 否 | **必改** |
| [services.py:188](apps/core/services.py#L188) | `saved_by_card[card] = person` | 是 | 是 | 否 | 否 | **必改** |
| [services.py:171](apps/core/services.py#L171) | `values.pop("id", None)` | 否 | 否 | 否 | 否 | **必改（改为使用）** |
| [views.py:565](apps/api/views.py#L565) | `filter(Q(name__icontains=k) \| Q(card__icontains=k))` | 否 | 否 | 否 | **是（搜索）** | 可不改（噪声变大） |
| [report_drafts.py:225](apps/core/report_drafts.py#L225) | `_string(item.get("card"), ...)` | 否 | 否 | 否 | 否 | 改长度上限 255→6 |
| [report_drafts.py:295](apps/core/report_drafts.py#L295) | `"card": person.card` | 否 | 否 | 否 | **是（序列化）** | 不改结构 |
| [export_services.py:243,252](apps/api/export_services.py#L243-L252) | `"'"+getattr(person,"card","")` | 否 | 否 | 否 | **是（导出）** | 依冻结条款第 13 条外的展示口径 |

## 3.2 全仓确认：**不存在** `Person.objects.get(card=...)`

```bash
grep -rn "get(card\|filter(card=" --include="*.py" apps/     # → 0 命中（仅 card__in / card__icontains）
```

除上表外，**其余全部 7 处 `Person` 访问均走主键**（`id__in` / `pk=link.person_id`）：
`export_services.py:121`、`person_export.py:127`、`registration_form.py:197`、`report_drafts.py:291`、`services.py:216`、`views.py:573`，以及 `services.py:186`（创建）。

> **有利结论**：去掉 UNIQUE **不会**产生 `MultipleObjectsReturned`。初版审计担心的这一类生产故障**不成立**。

## 3.3 `existing_by_card` 的两重危险（必须整体重写，不能只删 UNIQUE）

```python
existing_by_card = {person.card: person for person in Person.objects.filter(card__in=[...])}
```

1. **字典推导静默吞重复**：`card` 不再唯一后，同 card 多行**只保留最后一行、其余静默丢弃**——不报错、不写日志。本批人员**全部**挂到那一个 `Person` 上。
2. **与 159-160 行叠加**：只要"幸存"那行姓名与本批不同 → **整批被拒**，用户看到"该身份证已被使用"。

**这是本次改造的最高风险单点。**

---

# 4. `store_people()` 目标算法（**只给设计，不改代码**）

## 4.0 统一前置：card 规范化与校验

```
normalize_card(raw):
    s = str(raw).strip()
    if s 为空: → 错误「身份证后6位不能为空」
    if not re.match(r'^[0-9]{5}[0-9Xx]$', s): → 错误「身份证后6位应为6位，末位可为数字或X」
    return s[:5] + ('X' if s[5] in 'xX' else s[5])        # 冻结条款第 12 条：x → X
```

对齐冻结条款第 11/12 条；正则与任务书 §九 的建议一致。

## 4.1 场景 A：明确 `person_id`

```
person_id=101, name=张三, card=123456
```

目标：**复用 `Person` 101**。必须按序验证：

1. `101` 是否为合法正整数？（非法 → 按"无 person_id"处理或报错，依 B2-X/Y）
2. `Person.objects.filter(pk=101)` **是否存在**？不存在 → 报错或新建（依 B2）
3. **是否允许当前调用方使用**？→ **B2 授权谓词**（`Person.user_id == 生效用户 id`）
4. 是否校验姓名/card？
   - **card 必须校验格式**（6 位规范）——它是普通属性，但仍是必填属性
   - **姓名不建议与已有值做相等性校验**：冻结条款第 7 条明确 card 不是身份标识，姓名也不是；用姓名做匹配即违反第 10 条的"不得猜测"。若要求"姓名必须一致才允许复用"，等价于把 name+card 当身份键 → **与第 15 条精神冲突**
5. 允许更新哪些可变字段？建议沿用现状语义：[services.py:176-177](apps/core/services.py#L176-L177) 已 `pop("name")`/`pop("user_id")`，**保持不变**（不覆盖姓名、不改归属），其余字段可刷新。

> **注意**：`id` 到达 `store_people` 时**已是字符串**（`_person` 用 `str()`，`personToPayload` 也 `String()`），须先转 `int` 再做 `pk` 查询。

## 4.2 场景 B：没有 `person_id`，且库里已存在同名同后六位

```
输入: name=张三, card=123456
库中: Person 101 张三 123456
      Person 102 张三 123456
```

目标：**不猜 101，也不猜 102 → 创建新的 `Person`**（冻结条款第 10 条）。

**实现要求**：**彻底不查 `card`**——连"用 card 缩小候选集"都不做。因为第 10 条禁止的是"**仅凭**非唯一字段判断身份"，而"缩小候选集"之后无论怎么选都是猜。

> 这一条直接判定了 `existing_by_card`（§3.3）必须**整段删除**，不是改造。

## 4.3 场景 C：相同后六位不同姓名

```
张三 123456
李四 123456
```

目标：**允许**，创建/关联**不同** `Person`（冻结条款第 4 条）。

**必须删除** [services.py:155-156](apps/core/services.py#L155-L156) 与 [:158-160](apps/core/services.py#L158-L160) 两处"该身份证已被使用"的冲突判定——**这两行是当前新需求无法落地的直接原因**。

## 4.4 场景 D：同一请求重复提交同一个明确 `person_id`

目标：**允许**，两个 `ReportPerson` 指向同一 `Person`，**不得因重复报错**（冻结条款第 6 条）。

**结构上已经支持**：`ReportPerson`（[models.py:273-286](apps/core/models.py#L273-L286)）**没有任何唯一约束**，只有 `person_id` 上的 `db_index`。`attach_report_people`（[:193-203](apps/core/services.py#L193-L203)）先软删旧关联、再 `bulk_create`，天然允许 N 行指向同一 `person_id`。→ **只需保证新算法不因重复而报错，无需 DDL 变更。**

## 4.5 场景 E：无 `person_id`，且同一请求内部重复提交完全相同人员

**现状**：`saved_by_card`（[:163,167,188](apps/core/services.py#L163-L188)）在**单次请求内**按 card 复用 → 结果为 `Person` **1 行**、`ReportPerson` **N 行**。

**证据（用于判定"保留还是逐项新建"）**：

| 证据 | 内容 |
| --- | --- |
| `ReportPerson` 结构 | 纯关联表，`(report_id, person_id, position, type)`，**无唯一约束** → **两种方案都合法**，结构不构成约束 |
| 冻结条款第 6 条 | "同一报名内相同人员重复出现：**允许**" —— 只说"允许出现"，**没说"必须复用"** |
| 冻结条款第 10 条 | "只有姓名、后6位等非唯一字段 → **不得猜测复用**" |
| `saved_by_card` 的本质 | 正是"凭 name+card 猜这是同一个人" |

**判断**：`saved_by_card` 的临时复用与第 10 条**同源冲突**——它用的判断依据（name+card）与跨请求场景完全一样，只是范围缩小到一次请求。**逐项新建才与第 10 条一致。**

**建议**：**移除请求内临时复用**；每项独立建档，**除非该项携带了明确 `person_id`**。

**但这一条需确认**——因为它会改变一个用户可见的结果：同一份 Excel 里同一人写两遍，将从"1 个 Person"变成"2 个 Person"。虽在冻结条款下合法（第 14 条已声明重复属合法结果），但属**行为变更**，列入 §10.2 待确认。

---

# 5. 数据库审计

## 5.1 现状

```python
# apps/core/models.py:256
card = models.CharField(max_length=255, unique=True, default=" ")
```

| 属性 | 现状 | 目标（冻结条款第 2/3 条） |
| --- | --- | --- |
| 类型 | `CharField` | `CharField`（**保持字符串**，不得改整数） |
| `max_length` | 255 | **6** |
| `unique` | **True** | **False** |
| `default` | `" "` | **BLOCKER B3 —— 见 5.3** |
| `null` | 未设（NOT NULL） | 待定（见 5.3） |
| `blank` | 未设 | 建议 `blank=True`（与 `null` 取舍一致） |
| `validators` | 无 | 建议加 `RegexValidator(^[0-9]{5}[0-9Xx]$)` |
| `db_index` | 未设（unique 隐含） | **建议显式加 `db_index=True`** —— 去掉 unique 后 `views.py:565` 的 `card__icontains` 检索将失去索引。注意 `icontains` 前置通配本就用不上 btree，**加不加收益都有限**，但便于精确查询 |
| Meta constraints | 无 | **禁止**新增任何 `card` 或 `name+card` 唯一约束（冻结条款第 15 条） |

## 5.2 唯一约束的 migration 溯源

| migration | 操作 |
| --- | --- |
| `0001_initial.py:65` | `("card", CharField(max_length=255, unique=True))` ← **唯一约束诞生** |
| `0002` / `0003` | 未触碰 `person` |
| `0004_auto_20260921_1138.py:105-108` | `AlterField` 加 `default=' '`，`unique=True` **原样保留** |
| `0005` / `0006` | 未触碰 `person` |

→ **生产库中该唯一约束必然存在**（前提：库由本仓库 migration 建立；若由 Laravel dump 导入，约束名可能是 Laravel 的 `person_card_unique` 唯一索引，见 §5.4 验证项）。

## 5.3 `default=" "` 是否仍然合理 —— **BLOCKER B3**

**它为什么存在**：`0004` 给三个**唯一列**加 `default=' '`，动机是兼容 GaussDB（PG 9.2.4 兼容模式）把**空串当 NULL** 的行为（见 `docs/迁移审计报告.md` 缺陷 2）。

**改造后的问题**：

1. 唯一约束移除后，`" "` 不再"只能有一行"，**可以无限重复** → 原本靠约束兜住的"漏填"变成**静默写入**；
2. `" "` 是 **1 个字符**，**不满足**新的 6 位不变量（`^[0-9]{5}[0-9Xx]$`）→ 一个**永远无法通过校验的合法存量值**；
3. `max_length=6` 下 `" "` 仍可写入（1 ≤ 6），不会被 DB 拦下。

**当前兜底程度**：`store_people` 会拒绝空 card（`str(" ").strip()` → `""` → falsy → [services.py:150-151](apps/core/services.py#L150-L151) 返回"身份证和姓名不能为空"）。**但 `PUT /api/admin/person` 无此校验**（直接 `setattr`，[:577-578](apps/api/views.py#L577-L578)），可直接写入任意值。

**三种可选（需业务/运维确认，不自行决定）**：

| 选项 | 做法 | 风险 |
| --- | --- | --- |
| **B3-1** | 保留 `default=" "` | 与 6 位不变量冲突；漏填被静默隐藏 |
| **B3-2** | 改为 `default=""` | **GaussDB 可能视 `''` 为 NULL → NOT NULL 违约**。需厂商按实例版本确认 |
| **B3-3** | 移除 default，并把列改为可空（`null=True, blank=True`） | 语义最诚实（"没有后六位"= NULL）；但需确认 GaussDB 与既有 `" "` 存量的兼容 |

**推荐 B3-3**（语义最诚实，且 NULL 天然不参与任何相等性比较，不会与 6 位值混淆）；**但因涉及 GaussDB 行为，按任务书要求标记 BLOCKER，需业务/运维确认。**

**存量 `" "` 数据的处置也须一并确认**：置 `NULL`、保持原样、还是归入 §6 的异常数据一起处理？

## 5.4 数据库实际约束验证（上线前必做）

```sql
-- 约束名与类型（AlterField(unique=False) 依赖 introspection 正确定位）
SELECT conname, contype, pg_get_constraintdef(oid)
FROM pg_constraint WHERE conrelid = 'person'::regclass;

-- 唯一索引（Laravel dump 导入的库可能是独立唯一索引而非约束）
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'person';
```

**为什么必须做**：Django `AlterField(unique=False)` 通过 introspection 找到"恰好覆盖该列的唯一约束/索引"再删除。若生产库来自 Laravel dump（本仓库确有 `import_laravel_data` 命令与双轨迁移记载），约束名与形态都可能不同。**不核对就 `migrate` 是本方案唯一可能"卡住"的技术点。**

---

# 6. 历史数据迁移审计

## 6.1 只读统计 SQL（**本次未执行 —— 本机无数据库连接**，原因见 §6.3）

全部为 `SELECT`，无 `UPDATE` / `DELETE` / `ALTER`。输出不含完整身份证号。

```sql
-- ① 总量 + NULL + 空串 + 纯空白
SELECT count(*)                                                        AS person_total,
       count(*) FILTER (WHERE card IS NULL)                            AS card_null,
       count(*) FILTER (WHERE card = '')                               AS card_empty,
       count(*) FILTER (WHERE card IS NOT NULL AND card <> '' AND btrim(card) = '') AS card_blank,
       count(*) FILTER (WHERE card = ' ')                              AS card_single_space
FROM person;

-- ② 长度分布（含 18 / 6 / 其他）
SELECT length(btrim(card)) AS card_len, count(*) AS cnt
FROM person
GROUP BY 1 ORDER BY 2 DESC;

-- ③ 长度为 18 / 6 / 其他的显式计数
SELECT count(*) FILTER (WHERE length(btrim(card)) = 18) AS len18,
       count(*) FILTER (WHERE length(btrim(card)) = 6)  AS len6,
       count(*) FILTER (WHERE length(btrim(card)) NOT IN (18,6)
                          AND btrim(card) <> '')        AS len_other
FROM person;

-- ④ 截取后 6 位后的重复数量  ★迁移风险核心指标
SELECT count(*)                                            AS total_rows,
       count(DISTINCT right(btrim(card), 6))               AS distinct_last6,
       count(*) - count(DISTINCT right(btrim(card), 6))    AS expected_collisions
FROM person
WHERE card IS NOT NULL AND btrim(card) <> '';

-- ⑤ 截取后 6 位 + 姓名的重复数量  ★决定"是否连姓名也不唯一"
SELECT count(*) AS dup_groups, sum(c) AS affected_rows
FROM (
  SELECT right(btrim(card),6) AS last6, btrim(name) AS nm, count(*) AS c
  FROM person
  WHERE card IS NOT NULL AND btrim(card) <> ''
  GROUP BY 1,2 HAVING count(*) > 1
) t;

-- ⑥ 当前（改造前）就已存在的重复 card
SELECT card, count(*) AS person_count
FROM person GROUP BY card HAVING count(*) > 1 ORDER BY 2 DESC;
```

**回填格式**：

```
Person 总数：___      NULL：___    空串：___    纯空白：___    单空格" "：___
长度 18：___     长度 6：___     其他长度：___
截取后 6 位：不同值 ___ 个，预计冲突 ___ 行
截取后（6位+姓名）：重复组 ___ 个，涉及 ___ 行
当前已重复 card：___ 组
UNIQUE 约束名与类型：___
```

## 6.2 DROP UNIQUE 与数据转换的正确顺序（任务书要求特别确认）

**任务书给的顺序是对的，必须严格遵守**：

```
备份 → 统计 → DROP UNIQUE → 转换历史 card → 校验 → 应用新代码
```

**严禁"先截断、再删 UNIQUE"**：截断后必然产生大量重复（§6.1 ④），此时唯一约束仍在 → **`UPDATE` 大面积报唯一冲突，迁移中途失败**。

**本次审计补充一个更细的顺序约束（初版审计未覆盖）**：

> **`max_length` 的修改必须排在数据转换之后。**

理由：`AlterField(max_length=6)` 在 PostgreSQL/GaussDB 上会生成 `ALTER TABLE person ALTER COLUMN card TYPE varchar(6)`。**只要表中还有一行 card 长度 > 6，该语句直接失败**（`value too long for type character varying(6)`）。

因此 Phase 1 的 migration **必须拆成两个**（详见 §8.1）。

## 6.3 为什么本次没有统计数字

- 仓库内**没有 `.env`**（只有 `.env.example`），生产库连接信息不在代码库；
- 本地 PostgreSQL `127.0.0.1:5432` **未运行**（实测 `Connection refused`）；
- 附带影响：`pytest` 也依赖该库（`django_config/settings.py:62-70`，Django `TestCase` 建 `test_ylb`），**测试在本机同样无法执行**；
- 跑任何 `manage.py` / `pytest` 前需先 `export SECRET_KEY=x`，否则 `settings.py:13` 直接 `RuntimeError`。

---

# 7. 照片改造方案（冻结条款第 13 条）

## 7.1 现状：照片身份键完全在**前端**、且**按身份证**

| 模块 | 照片身份键 | 批量文件名 | 匹配代码 |
| --- | --- | --- | --- |
| 参演人员 `PersonTable.vue` | **card 后 6 位**（学生）/ **姓名 + card 后 6 位**（教师） | `123456.jpg` / `张三123456.jpg` | `uploadFileBatch` 633-660 |
| 带队教师 `LeaderTable.vue` | **完整 card** | `{完整card}.jpg` | `findIndex(row => row.card === card)` |
| 参演人员 `CrewTable.vue` | **完整 card** | `{完整card}.jpg` | 同上 |
| `TeacherTable.vue` / `PersonTableMajor.vue` | **无照片上传** | — | — |

**关键事实**：

1. **后端对照片不做任何 card/文件名匹配**。它只是把 payload 里的 `head` URL 存到某一行（小学模块 = `Person` 行，按 `Person.id`；线上模块 = `Crew`/`Leader` 行）。“哪个 Person”由 `store_people` **按 card** 决定，与照片本身无关。
2. **OSS 对象键不含任何身份信息**：`oss_token`（`views.py:1122-1124`）与 `oss_upload`（`views.py:1191-1194`）都生成 `{biz}/{YYYYMMDD}/{uuid4}{ext}`。
3. 原客户端文件名只被写进 `files.filename`（`models.py:304`，由 `views.py:311-315` 原样落库）。
4. **上传发生在 `Person` 行存在之前**——这是决定性约束。新建行只有 `name`/`card`/`type`，**没有 `id`**，因为 `Person.id` 由服务端在提交时才分配，且 [services.py:171](apps/core/services.py#L171) 明确丢弃客户端 `id`。

## 7.2 两条路线与推荐

| | **方案 1（推荐，最小合规）** | **方案 2（`Person.id`，彻底）** |
| --- | --- | --- |
| 做法 | 学生键由"纯后 6 位"改为 **`姓名+后6位`**（与教师现有规则统一） | 照片改为按 `Person.id` 关联；需新增"按 person_id 挂照片"端点，并**把上传推迟到人员入库之后** |
| 是否满足第 13 条 | **满足**——后 6 位不再是**唯一**匹配键（姓名成为必要组成部分） | 满足 |
| 改动面 | `PersonTable.vue` 2 处 + 线上模块 2 处 | 3 个组件上传时序重排 + 后端新端点 + 前端状态机 |
| 新建行无 id 的问题 | **不存在**（name+card 在上传时都在行里） | **必须先解决**：新行上传时还没有 id |
| 历史照片 | **无需迁移**（见 7.4） | 无需迁移，但对历史数据无法回溯校验 |

**推荐方案 1**，理由：
- 冻结条款第 13 条禁止的是"把后 6 位作为**唯一**匹配键"，方案 1 精确满足；
- 任务书要求"**不要自行扩大范围**"，方案 2 是一次上传时序重构，应作为独立项目；
- 方案 1 不引入"上传时没有 id"这一无解时序问题。

## 7.3 方案 1 的具体改动点

**`PersonTable.vue`**：
- `beforeUploadSingle`（775-788）：`expected = isTeacher ? item.name + tail : tail` → **两者都改为** `item.name + tail`
- `uploadFileBatch` 匹配（633-660）：`const matched = personName ? item.type === 1 && item.name === personName : item.type === 0` → **学生分支也要求姓名相等**，即统一为 `item.name === personName && item.type === (personName ? 1 : 0)` 的等价形式（**姓名成为必比项**）
- `parsePhotoName`（678-686）：**保持现状**（`^\d{6}$` 与 `^\D.*\d{6}$` 都接受）→ **兼容旧文件名**，无需强制用户重命名历史照片
- 提示文案：`'…（学生照片：身份证号后6位；教师照片：姓名+身份证号后6位）'` → 统一为 `身份证后6位` 前的姓名要求
- **`oldHeads[item.card]`（521-524, 541）**：6 位 card 下会撞号 → 改为按 `姓名+后6位` 建键，或直接改为按 `name+card` 复合键

**`LeaderTable.vue`（213-216）/ `CrewTable.vue`（244-247）**：
- 匹配由 `row.card === stem` 改为 `row.name + row.card === stem`（或先试复合键、失败再回退纯 card 以兼容旧文件）
- **补多命中保护**：现状 `findIndex` 静默取第一个，重复时**静默写错行** → 应照 `PersonTable` 的 `hits` 模式改为"多命中即报错"

**保留兼容**：过渡期数据可能仍是 18 位，`tail = card.length >= 6 ? substring(len-6) : card` 的写法**必须保留**（幂等：6 位输入下 `tail === card`），这样代码在迁移前后都正确，**回滚也安全**。

## 7.4 历史照片**不需要迁移**（重要，避免无谓工作量）

照片的持久化形式是 `Person.head` 里的 **URL**（指向随机 UUID 对象键）。文件名规则**只在上传那一刻**用于"把文件路由到哪一行"。

→ **历史照片已按 `Person` 行存好，无需、也无法"重新匹配"**；`files.filename` 里那些 `123456.jpg` 记录**不需要处理**。

**唯一残留影响**：若运营要让用户**重新上传**历史照片，旧的 6 位纯数字文件名在 `parsePhotoName` 下仍被接受（向后兼容），但学生规则改为姓名+后6位后，旧文件名会**被 `beforeUploadSingle` 拒绝**。→ 若要支持重传，需保留"纯 6 位"作为学生**只读兼容**分支，或明确告知用户改名。

---

# 8. 实施计划

## 8.1 Migration 方案（**必须拆成 3 步**）

| 步骤 | 类型 | 作用 | 为何独立 |
| --- | --- | --- | --- |
| **Migration 1** | `AlterField` | `card`：`unique=True → unique=False`，**`max_length` 保持 255** | 删除唯一约束。**不能同时改 max_length**——见下 |
| **数据转换** | management command（**不是 migration**） | `card = right(btrim(card), 6)`，末位 `x→X` | 需 `--dry-run` + 分批 + 可校验；DDL migration 不适合承载大批量 DML |
| **Migration 2** | `AlterField` | `card`：`max_length 255 → 6`，并按 B3 决定 `default`/`null` | **必须排在数据转换之后**：此时表内所有值 ≤ 6 字符，`ALTER COLUMN TYPE varchar(6)` 才不会失败 |

**为什么 Migration 1 不能顺手改 max_length**：若先改 `max_length=6`，表中仍有 18 位数据 → `ALTER COLUMN TYPE` 直接报 `value too long`，**migration 失败且事务回滚**。反过来先删 unique 则无副作用（删约束不校验数据）。

**推荐用 management command 而非 `RunPython`** 承载数据转换，理由：可 `--dry-run` 预览、可分批提交避免长事务锁表、可重复执行（幂等）、可脱离 migration 单独回滚。

## 8.2 数据迁移方案与执行顺序

**推荐方案（维护窗口，最稳）** —— 与任务书 §六顺序一致：

```
① 备份数据库（冻结条款第 14 条后果不可逆，备份是硬要求）
        ↓
② 执行 §6.1 只读统计，量化重复量
        ↓
③ 停止写入（维护窗口 —— 见下方"为什么必须停写"）
        ↓
④ Migration 1：DROP UNIQUE
        ↓
⑤ 数据转换命令：dry-run → 正式执行 → 校验
        ↓
⑥ Migration 2：max_length 6
        ↓
⑦ 部署新后端 + 新前端（同时）
        ↓
⑧ 校验 + 冒烟
```

**为什么必须停写（本方案的关键论证）**：第 ④ 步之后、第 ⑦ 步之前，**旧代码仍在运行且仍在按 card 匹配**。此时库里已有（或即将有）重复后六位 → 旧 `store_people` 的 `existing_by_card` 会命中"最后一行"，凡姓名对不上就返回「该身份证已被使用」，**合法提交被随机拒绝**。这不是数据损坏，但会造成用户可见的大面积失败。**故 ④→⑦ 之间必须冻结写入。**

**零停机替代方案（记录备选，成本更高）**：先部署**同时兼容 18 位与 6 位**的新代码（新代码不按 card 匹配身份，故不受重复影响），再删约束、转数据，最后下线兼容逻辑。代价是需多维护一个过渡版本，且前端校验在过渡期须同时接受两种格式。**不推荐**——对竞赛报名系统，短维护窗口更简单可靠。

## 8.3 需要修改的文件

| 文件 | 当前作用 | 需要改什么 | 原因 | 风险 |
| --- | --- | --- | --- | --- |
| `apps/core/models.py:256` | `Person.card` 定义 | 长度 6、去 unique、按 B3 定 default/null、加 validator | 冻结条款 2/3 | 漏改 max_length 会致 migration 2 失败 |
| `apps/core/migrations/000X_*.py`（新） | — | Migration 1 / 2 | 同上 | 约束名需与生产库核对（§5.4） |
| `apps/core/services.py:133-190` | 人员写入核心 | **整体重写**：删 `existing_by_card`/`names_by_card`/两处冲突判定/`saved_by_card`；改为按 §4 算法（**优先 person_id**） | 冻结条款 4/5/6/7/9/10 | **最高**——错误实现会产生静默重复人员 |
| `apps/core/services.py:171` | `values.pop("id")` | 改为解析并使用 `person_id` | 冻结条款 9 | **依赖 B2 授权规则** |
| `apps/core/services.py:150-151` | 空值校验 | 改为 6 位格式校验 + `x→X` | 冻结条款 11/12 | 漏掉末端 X 会拒真数据 |
| `apps/core/report_drafts.py:225` | `card` 长度校验 | 上限 255→6，加正则 | 同上 | draft 与正式提交须同源 |
| `apps/core/report_drafts.py:47` | `PERSON_FIELDS` | 保持含 `id`（**不得改名**） | 冻结条款 9；改名会复发 400 事故 | 见 `draftPayload.js:108-116` 记载的既往故障 |
| `apps/api/views.py:122,159` | `create_report`/`update_report` 取 `person[]` | 建议补 person 字段白名单 | 与 draft 路径对齐 | 现状无白名单，字段随意进入 |
| `apps/api/views.py:569-580` | `PUT /api/admin/person` | 补 6 位校验（现无任何校验，可直接写任意值） | 冻结条款 2 | 管理员可写非法 card |
| `apps/api/views.py:1015,1033,1039` | 票务 `/ticket/my`、`/ticket/make` | **待确认是否同步**（票务另一张表，本就非唯一） | §10.2 待确认 | 不确认则报名/票务口径不一 |
| `yl-frontend/src/config/personFields.js:31,89` | 18 位正则与文案 | 改为 `^[0-9]{5}[0-9Xx]$` + `x→X` + 新文案 | 冻结条款 11/12 | — |
| `yl-frontend/src/services/draftPayload.js:117,120` | `id`/`card` 发送 | `card` 注释与规范化；`id` **必须保留** | 冻结条款 9 | `card` 注释仍写"18 位精度"，需更新 |
| `PersonTable.vue` | 输入/导入/照片 | 正则调用、`maxlength=6`、照片键（§7.3）、`oldHeads` 键、导入保留 `id` | 冻结条款 11/13 | 照片部分是本次最易漏项 |
| `PersonTableMajor.vue` | 同上（无照片） | 正则、`maxlength`、导入保留 `id` | 同上 | — |
| `TeacherTable.vue` | 同上 | 正则、`maxlength` | 同上 | — |
| `LeaderTable.vue` | 线上教师 + 照片 | `maxlength`；照片键加姓名 + 多命中保护；**补 6 位校验**（现仅判空） | 冻结条款 11/13 | 现存 `findIndex` 静默写错行 |
| `CrewTable.vue` | 线上参演 + 照片 | 同上 | 同上 | 同上 |
| `views/admin/person.vue:128-136,161-168` | 管理员人员管理 | 补 6 位校验；**`validate()` 从未被调用**需一并修 | 冻结条款 2 | 表单规则形同虚设 |
| `public/static/参演人员导入模板*.xlsx` | Excel 模板 | C 列表头「身份证」→「身份证后6位」，示例值改 6 位 | 用户引导 | 改中文不影响解析（解析靠隐藏英文键行） |
| `tests/test_reports.py` 3 条 | 测试 | 见 §8.5 | 冻结条款 4/5 | 必改 |
| `test_data/member_import/*.md` | 测试预期文档 | 「同 card 不同 name → 失败」的预期**已失效** | 冻结条款 4 | 不同步会误导后续测试 |

## 8.4 不需要修改的文件（已审计确认）

| 文件/模块 | 结论 |
| --- | --- |
| `apps/core/models.py` `ReportPerson` | 无唯一约束，`person_id` 已 `db_index` → **DDL 完全不用动** |
| `Report` 模型 | 与 card 无关 |
| `apps/api/export_services.py` | 已按 card 读写，6 位下语义自然成立；`'` 前缀为原版既有行为，**保持不动** |
| `apps/api/person_export.py` | `HEADINGS` **不含 card**，`/api/export/person` 根本不导出身份证 → **无需改** |
| `apps/api/registration_form.py` | 不消费 card |
| `apps/core/admin.py` | 未注册 `Person`，无 Admin 界面 |
| **后端 Excel 导入** | **不存在该功能**；唯一上传路由白名单只放行图片/PDF |
| `import_laravel_data.py` / `clean_report_combos.py` / `seed_tickets.py` | 不读 `card` |
| `Crew.card` / `Leader.card` / `TicketSubscribe.card` / `Student.card` | 均为**普通字段，本就非唯一** → 无需 DDL |
| 其余 7 处 `Person` 访问 | 全部按主键 → 不受影响 |
| `oss_token` / `oss_upload` 对象键规则 | `{biz}/{date}/{uuid4}` 与身份无关，**无需改** |
| 历史照片与 `files.filename` | 无需迁移（§7.4） |
| `tests/` 中其余 14 处 card 引用 | 只把 card 当普通字符串；`test_files_scan_live.py` 属 `Crew`/`Leader`，`test_tickets_pagination_openapi.py` 属 `TicketSubscribe` |

## 8.5 测试方案

**必改（3 条，均在 `tests/test_reports.py`）**：

| 测试 | 现状断言 | 改为 |
| --- | --- | --- |
| `test_failed_create_rolls_back_people_created_earlier_in_request` (L29) | 同 card 不同 name → `code:1` + 全回滚 | **允许**：`code:0`，且两个 `Person` 行 |
| `test_failed_update_keeps_report_links_and_people_unchanged` (L47) | 同上 | 同上 |
| `test_reused_card_keeps_original_identity_but_refreshes_optional_fields` (L171) | 同 card 复用 → 保留原 `name`/`user_id` | 改为**必须携带 `person_id`** 才复用；无 `person_id` 时**新建** |

**新增（覆盖任务书 §十 的 12 项）** —— 每条**必须断言 `Person.id` 与 `ReportPerson.person_id`**，不能只断言 HTTP 200：

| # | 用例 | 核心断言 |
| --- | --- | --- |
| 1 | 同 card 不同 name | `Person` 2 行；两个 link 指向不同 `person_id` |
| 2 | 同 card 同 name（无 person_id） | `Person` **2 行**（第 10 条：不得猜） |
| 3 | 同 card 不同 Person | 同上，且各自 `person_id` 正确 |
| 4 | **明确 person_id** | 复用**精确**那一行；`Person.count()` 不增 |
| 5 | 同名同 card 无 person_id | **不得**复用任一已有行；新建 |
| 6 | 同一人跨多份报名（带 person_id） | 两份报名的 link 指向**同一** `person_id` |
| 7 | 重复 `ReportPerson` | 同 report 下 N 行可指向同一 `person_id`，`code:0` |
| 8 | 末位 `X`/`x` | 入库存为**大写 X**；`x` 与 `X` 归一到同一值 |
| 9 | card 长度 | 5 位 / 7 位 / 含非法字符 → 拒绝；6 位通过 |
| 10 | UNIQUE 已移除 | 直接 `Person.objects.create(card=同值)` 两次成功 |
| 11 | 历史迁移后重复 | 迁移后重复 last6 不报错、可继续报名 |
| 12 | 照片匹配不依赖 card 唯一性 | 同后 6 位两人各自可匹配（姓名区分）；歧义时报错而非静默 |
| 13（建议） | **越权** | 提交他人 `person_id` → 按 B2 决策断言（报错 / 新建） |
| 14（建议） | 场景 E | 同一请求两行相同姓名+card 无 id → `Person` 2 行（若采纳 §4.5 建议） |

## 8.6 发布与回滚方案

**发布**

```
1  数据库全量备份（含 person / report_person / files；留存期 ≥ 报名周期）
2  停机冻结写入（维护窗口公告）
3  执行只读统计（§6.1），确认重复量与预期
4  Migration 1（DROP UNIQUE）
5  数据转换 dry-run → 正式执行 → 校验（长度分布、无 >6、无 " " 遗留、重复量与 dry-run 一致）
6  Migration 2（max_length 6 + B3 决定的 default/null）
7  部署新后端 + 新前端（同一批次）
8  API 验证：创建/修改/草稿提交/驳回重提/管理员改人员 五条路径
9  照片验证：学生与教师各传一张，确认按"姓名+后6位"匹配；构造同后6位两人，确认歧义被拦截
10 恢复写入，观察
```

**回滚条件（任一触发即回滚）**

- 报名创建/修改失败率超过阈值，或出现大量「该身份证已被使用」
- `Person` 行数异常增长（提示误删了复用逻辑或授权判定过宽）
- 照片匹配错误（照片挂到错人）
- 出现 `MultipleObjectsReturned` 或大量 500

**回滚步骤与硬约束**

- 代码可回滚；**数据转换不可逆**——退回 18 位身份证**不可能**，这正是备份必须留存的原因（冻结条款第 14 条 + 任务书 §七）。
- 回滚到旧代码会**重新要求 card 唯一**，而数据已重复 → **旧代码 + 新数据不可用**。因此回滚**只能**连同数据一起从备份恢复，**不能只回滚代码**。
- → **结论：本次改造的"可回滚"实际等价于"从备份整体恢复"。这是必须在上线前书面确认的取舍。**

---

# 9. 待确认清单（阻塞项置顶）

| 编号 | 问题 | 类型 |
| --- | --- | --- |
| **B1** | 生产库历史数据统计（§6.1 六组 SQL） | **阻塞** — 解除 Phase 5 风险量化 |
| **B2** | `person_id` 复用授权谓词（B2-A 同单位 / B2-B 不校验）**及**不匹配时的行为（B2-X 报错 / B2-Y 新建） | **阻塞 Phase 3** |
| **B3** | `card` 的 `default=" "` 去留（B3-1 保留 / B3-2 改空串 / B3-3 移除并允许 NULL），含存量 `" "` 处置 | **阻塞 Phase 1** |
| D1 | 照片匹配键：方案 1（姓名+后6位，推荐）还是方案 2（Person.id） | 需拍板 |
| D2 | 场景 E：同一请求内无 `person_id` 的重复行，是否移除临时复用（建议移除） | 需拍板 |
| D3 | 历史数据中的异常长度值（非 18/6）如何处置：截断 / 置空 / 保留 | 需拍板（依赖 B1 结果） |
| D4 | 票务 `/api/ticket/*` 是否同步改为后六位（当前收完整身份证） | 需拍板 |
| D5 | 是否需要支持"让用户重新上传历史照片"（影响是否保留纯 6 位文件名兼容分支） | 需拍板 |

---

# 10. 审计边界声明

- **未修改**任何 Python / Model / migration / Vue / JS / 数据库 / 配置；**未执行** `migrate`、未删除 UNIQUE、未改历史数据、未 `npm build`、未 `git commit`、未 `git push`。
- 本次仅新增一个文件：`ID_CARD_LAST6_IMPLEMENTATION_PLAN.md`。`ID_CARD_LAST6_AUDIT.md` 未改动。
- §6 数据统计**未执行**（本机无数据库连接，见 §6.3）——这是本计划唯一的证据缺口，也是 B1。
- 结论均基于直接读取源码与 SQL；Laravel 原版仅通过既有 `DJANGO_REGRESSION_AUDIT.md` 引用，本次未再访问。
