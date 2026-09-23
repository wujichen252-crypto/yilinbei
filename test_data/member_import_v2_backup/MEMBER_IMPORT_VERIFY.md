# 意林杯「参演人员名单」Excel 导入 验证手册

配套文件：`MEMBER_IMPORT_TEST_CASES.md`（规则与逐行预期）
适用版本：2026-09-23 修正版（英文表头）

---

## 1. 验证总览

| 层 | 位置 | 校验内容 |
| --- | --- | --- |
| 第 1 层（前端，权威） | `PersonTable.vue:415` `exportCheck` | 9 项必填 + 3 个中文枚举 |
| 第 2 层（后端） | `apps/core/services.py:126` `store_people` | `name`+`card` 非空、`card` 全局唯一、全批事务 |

**前端不过 → 请求根本不发出**（只有一条 `ElMessage.error`）。
**前端过、后端不过 → HTTP 200 + `{"code":1,"msg":"…"}`**（项目统一失败响应）。

---

## 2. 导入前必读

1. **表头必须是英文字段名**，见 `MEMBER_IMPORT_TEST_CASES.md` 第 2 节。中文表头会直接报「第1行姓名不能为空」。
2. **只读第一个 sheet**。`06_header_error.xlsx` 的 7 个 Sheet 必须**逐个另存为单独文件**再测。
3. **首条成员会被静默丢弃**。`01_normal.xlsx` 有 38 条，导入后 `form.person.length === 37`——这是正确行为，不是 bug 症状。
4. **报错行号 = Excel 行号 − 2**。
5. 身份证一律为文本单元格；测试值均以 `TESTID` 开头，不会与正式数据冲突。

---

## 3. 需要预置数据的两份文件

`04_existing_members.xlsx` 与 `09_mixed.xlsx` 依赖「库中已存在同 `card` 的 `Person`」。
**必须先造出来**，否则这些行会走「新增」分支，测不到复用/更新/冲突逻辑。

### 3.1 预置 `04_existing_members.xlsx` 所需

需要 3 条 `Person`，`card` 与姓名如下（`name` 决定是否触发冲突）：

| card | 库中 `name` 应为 | Excel 中的 `name` | 期望 |
| --- | --- | --- | --- |
| `TESTID90000000001` | 与原 Excel 第 1 条**相同** | 同左 | 复用/更新 |
| `TESTID90000000002` | 与 Excel 第 2 条**相同** | 同左 | 复用/更新 |
| `TESTID90000000003` | 与 Excel 第 3 条**不同** | 任意 | **冲突 → 整批失败** |

预置方式（任选其一，均不触碰业务代码）：

**A. 走正常接口**——用测试账号提交两份 `report/create`，`person` 数组里放上述 `card`+`name`。

**B. 直接用项目 shell**：

```python
# python manage.py shell   （在项目根目录，使用项目自己的 .venv）
from apps.core.models import Person
rows = [
    ("TESTID90000000001", "<与Excel第1条一致的姓名>"),
    ("TESTID90000000002", "<与Excel第2条一致的姓名>"),
    ("TESTID90000000003", "<一个与Excel第3条不同的姓名>"),
]
for card, name in rows:
    Person.objects.update_or_create(
        card=card,
        defaults={"name": name, "user_id": 1, "gender": "男", "age": 15,
                  "school": "测试学校", "phone": "TESTPHONE000001"},
    )
```

> `<…>` 里的姓名请直接打开 `04_existing_members.xlsx` 的 `name` 列读取，不要凭记忆填写。
> `user_id` 填你自己的测试账号 id。

### 3.2 预置 `09_mixed.xlsx` 所需

`idx 85-89`（Excel 第 87-91 行）的 5 条 `TESTID5…` 卡片。

```python
# 打开 09_mixed.xlsx，读 Excel 第 87-91 行的 card 与 name，按上面同样的方式写入
```

### 3.3 清理

```python
from apps.core.models import Person, ReportPerson
cards = ["TESTID9...", "TESTID5..."]        # 填实际值
Person.objects.filter(card__startswith="TESTID9").delete()
Person.objects.filter(card__startswith="TESTID5").delete()
```

**只删除 `TESTID` 前缀的记录。** 切勿用无过滤条件的 `delete()`。

---

## 4. 逐文件验证步骤

### 4.1 前端层验证（不需要数据库）

打开报名表 → 参演人员 → 上传对应文件，观察 `ElMessage` 与表格行数。

| 文件 | 期望提示 | 期望导入条数 |
| --- | --- | --- |
| `01_normal.xlsx` | 无错误 | **37** |
| `02_minimal.xlsx` | 无错误 | **7** |
| `03_duplicate.xlsx` | 无错误 | **4**（表格里出现重复行，前端不去重） |
| `04_existing_members.xlsx` | 无错误 | **2** ✔见下 |
| `05_invalid.xlsx` | `导入失败！参演人员名单第1行身份证不能为空` | 0（未导入） |
| `06_header_error.xlsx` | 逐 Sheet 另存后测，见 4.3 | — |
| `07_boundary.xlsx` | `导入失败！参演人员名单第10行年龄不能为空` | 0 |
| `08_special_characters.xlsx` | 无错误 | **31** |
| `09_mixed.xlsx` | `导入失败！参演人员名单第95行姓名不能为空` | 0 |
| `10_large.xlsx` | 无错误 | **999** |

> `04` 的 3 条数据里有 1 条是**同卡不同名**的冲突行。前端 `exportCheck` **不查重**，
> 所以前端只会显示 2 条（首条被跳过）。冲突要到提交时才由后端报出——见 4.2。

### 4.2 后端层验证

1. 按上表完成上传（只有前端通过的文件才能走到这一步）。
2. 点提交，观察返回。

| 文件 | 期望后端结果 |
| --- | --- |
| `01_normal` | `{"code":0}`，`person` +37，`position` 全为 0（正式队员） |
| `02_minimal` | `{"code":0}`，+7 |
| `03_duplicate` | `{"code":0}`；甲/乙各只建 1 条，净增 **3** |
| `04_existing_members` | `{"code":1,"msg":"<card>-<name>,该身份证已被使用，请检查您的身份证和姓名是否输入正确"}`，**整批回滚** |
| `08_special_characters` | `{"code":0}`，+31 |
| `10_large` | `{"code":0}`，+999 |

**`03_duplicate` 的净增验证**（关键）：

```python
from apps.core.models import Person, ReportPerson
# 提交前后各查一次
Person.objects.filter(card__startswith="TESTID3").count()   # 应稳定在 3
```

同卡同名 → 复用同一 `Person`，只新增/更新 `ReportPerson` 关联；`position`/`type` 取**数组中最后一次出现**的值。

### 4.3 `06_header_error.xlsx` 逐 Sheet

先把 7 个 Sheet 各另存为独立 `.xlsx`，逐个上传：

| Sheet | 期望提示 |
| --- | --- |
| `CaseA_缺表头` | `第1行姓名不能为空` |
| `CaseB_拼写错误` | `第1行姓名不能为空` |
| `CaseC_多余表头` | **无错误** |
| `CaseD_顺序调整` | **无错误** |
| `CaseE_空表头` | `第1行学校名称不能为空` |
| `CaseF_重复表头` | **无错误** |
| `CaseG_前后空格` | `第1行姓名不能为空` |

### 4.4 数据库核对

```python
from apps.core.models import Person, ReportPerson

# 1) 总量：所有测试卡片（前缀隔离，不会命中正式数据）
for p in ["TESTID0", "TESTID3", "TESTID4", "TESTID5", "TESTID8", "TESTID9", "TESTIDB"]:
    print(p, Person.objects.filter(card__startswith=p).count())

# 2) 重复卡：应为空（card 全局唯一）
from django.db.models import Count
print(Person.objects.values("card").annotate(n=Count("id")).filter(n__gt=1))

# 3) 特殊字符是否原样入库
print(Person.objects.filter(card__startswith="TESTID4", name__contains="<script>").values("name"))

# 4) 关联与位置
rp = ReportPerson.objects.filter(person__card__startswith="TESTID0")
print(rp.count(), rp.values("position", "type").distinct())
```

预期：第 2 项为空集；第 3 项能查到——说明前端**未做转义**（输出侧需自行处理）。

---

## 5. 绕过界面直接验后端（可选）

若只想验第 2 层，可用 `manage.py shell` 直接构造 `store_people` 的入参：

```python
from apps.core.services import store_people
from apps.core.models import User
u = User.objects.get(id=<测试账号>)
print(store_people(u, [{"name": "测试甲", "card": "TESTID00000000099"}]))
# -> (True, [{"person_id": ..., "position": 0, "type": 0}])

print(store_people(u, [{"name": " ", "card": "TESTID00000000098"}]))
# -> (False, '身份证和姓名不能为空')   ← 前端会放行、后端才拦下的那个用例
```

**只读 + 只写 `TESTID` 前缀记录**，不要用正式 `card` 调用。

---

## 6. 代码修改确认

```bash
cd /e/github/yilinbei && git status --short && git diff HEAD --stat
```

本测试集**只新增** `test_data/` 下的文件。预期 `git status` 中：

- `test_data/` 为未跟踪目录（`?? test_data/`）
- `apps/`、`config/`、`manage.py` 等**无任何改动**

若 `git diff` 出现业务代码变更，说明流程被破坏，应回退。

---

## 7. 回放验证（无需启动项目）

本测试集已用**前端真实逻辑**做过离线回放，可复现：

```bash
node "C:/Users/34716/AppData/Local/Temp/ylb_replay_import.js"
```

结果：

```text
01_normal.xlsx                 PASS  rows=37
02_minimal.xlsx                PASS  rows=7
03_duplicate.xlsx              PASS  rows=4
04_existing_members.xlsx       PASS  rows=2
05_invalid.xlsx                FAIL  导入失败！参演人员名单第1行身份证不能为空
06_header_error.xlsx           FAIL  导入失败！参演人员名单第1行姓名不能为空
07_boundary.xlsx               FAIL  导入失败！参演人员名单第10行年龄不能为空
08_special_characters.xlsx     PASS  rows=31
09_mixed.xlsx                  FAIL  导入失败！参演人员名单第95行姓名不能为空
10_large.xlsx                  PASS  rows=999
```

---

## 8. 已知限制

| 限制 | 说明 |
| --- | --- |
| 每个含错文件只暴露 1 条错误 | 首错即整批中止。要逐条验，需按行拆文件。 |
| 首条成员永远测不到 | 被 `i=1` 循环起点跳过。**若想验证某条数据，请把它放在第 2 行或之后。** |
| `major` 无前端校验 | `PersonTable.vue` 不检查 `major`；只有未被引用的 `PersonTableMajor.vue` 变体检查。 |
| 导出侧未覆盖 | 本集只测导入。导出（`export_services.py`）的 `=1+1` 公式化风险见说明书第 6.8 节。 |
| 无日期字段 | 成员模型与表单均无日期，故无日期用例。 |

---

## 9. 数据安全

所有姓名、学校、专业均为虚构；身份证以 `TESTID` 前缀合成，电话以 `TESTPHONE` 前缀合成；
未从任何数据库导出真实信息；全部测试记录均可用 `card__startswith="TESTID"` 一次性定位与清理。
