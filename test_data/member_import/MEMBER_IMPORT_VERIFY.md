# 意林杯「参演人员名单」Excel 导入 验证手册（v4）

配套：`MEMBER_IMPORT_TEST_CASES.md`（规则与逐行预期）
适用版本：2026-09-23 v4（官方模板结构 + `personFields.js` 五条格式规则）

---

## 1. 验证总览

| 层 | 位置 | 校验内容 |
| --- | --- | --- |
| 第 1 层（前端，权威） | `PersonTable.vue:exportCheck` + `config/personFields.js` | 10 列必填 + 3 个中文枚举 + 5 条格式规则 |
| 第 2 层（后端） | `apps/core/services.py:126 store_people` | `name`+`card` 非空、`card` 全局唯一、全批事务 |

前端不过 → **请求根本不发出**（只有一条 `ElMessage.error`）。
前端过、后端不过 → **HTTP 200 + `{"code":1,"msg":"…"}`**。

---

## 2. 导入前必读

1. **不要改模板的前两行。** 第 1 行是英文字段名（`sheet_to_json` 的键行），第 2 行是中文标签行，**数据从第 3 行开始**。官方模板本身长这样，本测试集每个文件的前两行都与 `yl-frontend/public/static/参演人员导入模板.xlsx` **逐格一致**。
2. **工作表名 `用户表`**（解析不认表名，只取「第一张有数据的表」，但保持一致便于比对）。
3. **`card` 必须是 18 位、前 17 位数字、末位数字或 X**；**`phone` 必须是 `^1[3-9]\d{9}$` 的 11 位手机号**。本测试集用 `00` 开头的合成身份证与 `140`–`144` 段合成手机号，格式合法、但都不对应任何真实个人或可用号段。
4. **`name` 不能含数字、长度 2–20**；`age` 必须是 5–80 的整数；`school` 不能是纯数字。
5. **只读第一个有数据的表。** `06_header_error.xlsx` 的 7 个 Sheet 必须**逐个另存为单独文件**再测。
6. **报错行号 = Excel 行号 − 2**。
7. 官方模板列里**没有** `major`、`number`；第 11 列是一句提示语，用不到。

---

## 3. 需要预置数据的两份文件

`04_existing_members.xlsx` 与 `09_mixed.xlsx` 依赖「库中已存在同 `card` 的 `Person`」。**必须先造出来**，否则这些行会走「新增」分支，测不到复用/更新/冲突。

### 3.1 预置 `04_existing_members.xlsx`

| card | 库中 `name` 应为 | Excel 中的 `name` | 期望 |
| --- | --- | --- | --- |
| `000090200001010018` | 与 Excel 相同 | 库中已存在甲 | 复用/更新 |
| `000090200001020021` | 与 Excel 相同 | 库中已存在乙 | 复用/更新 |
| `000090200001030035` | 与 Excel **不同** | 库中已存在丙-改名 | **冲突 → 整批失败** |

```python
# python manage.py shell   （项目根目录，用项目自己的 .venv）
from apps.core.models import Person
rows = [
    ("000090200001010018", "库中已存在甲"),
    ("000090200001020021", "库中已存在乙"),
    ("000090200001030035", "与Excel不同的姓名"),   # 故意不同，制造冲突
]
for card, name in rows:
    Person.objects.update_or_create(
        card=card,
        defaults={"name": name, "user_id": <你的测试账号id>, "gender": "男", "age": 15,
                  "school": "测试学校", "phone": "14100000001"},
    )
```

> `phone` 已是合法 11 位格式——v3 及以前用 `TESTPHONE…`，那个值现在会被前端拒绝。库里的旧记录若也是 `TESTPHONE…`，不影响导入（后端不校验 phone），但建议一并更新。

### 3.2 预置 `09_mixed.xlsx`

`idx 86-90`（**Excel 第 88-92 行**）的 5 条：

| card | Excel `name` |
| --- | --- |
| `000090200001110115` | 库中已存在甲 |
| `000090200001120129` | 库中已存在乙 |
| `000090200001130132` | 库中已存在丙 |
| `000090200001140146` | 库中已存在丁 |
| `00009020000115015X` | 库中已存在戊 |

按 §3.1 同样方式写入（`name` 与 Excel 一致即走复用分支）。

> **注意**：`09_mixed.xlsx` 原样导入会在前端就失败（idx 96 姓名不能为空），请求不会发出，所以这 5 条预置数据**只有在删掉最后 5 行之后**才会被用到。

### 3.3 清理

```python
from apps.core.models import Person
# 全部测试卡片都是 0000 开头，与正式数据天然隔离
Person.objects.filter(card__startswith="0000").delete()
```

**只删除 `card` 以 `0000` 开头的记录。** 切勿用无过滤条件的 `delete()`。

---

## 4. 逐文件验证

### 4.1 前端层（不需要数据库）

打开报名表 → 参演人员 → 上传，观察 `ElMessage` 与表格行数。

| 文件 | 期望提示 | 期望条数 |
| --- | --- | --- |
| `01_normal.xlsx` | 无错误 | **38** |
| `02_minimal.xlsx` | 无错误 | **8** |
| `03_duplicate.xlsx` | 无错误 | **5**（表格里出现重复行，前端不去重） |
| `04_existing_members.xlsx` | 无错误 | **3** |
| `05_invalid.xlsx` | `导入失败！参演人员名单第1行姓名不能为空` | 0 |
| `06_header_error.xlsx` | 逐 Sheet 另存后测，见 §4.3 | — |
| `07_boundary.xlsx` | `导入失败！参演人员名单第27行姓名长度应为2-20个字符` | 0 |
| `08_special_characters.xlsx` | 无错误 | **32** |
| `09_mixed.xlsx` | `导入失败！参演人员名单第96行姓名不能为空` | 0 |
| `10_large.xlsx` | 无错误 | **1000** |

> 若 `07_boundary.xlsx` 报出的**不是**「第27行姓名长度应为2-20个字符」，而是更靠后的某条必填错误（如「第34行年龄不能为空」），说明你的构建把 `personFields.js` 的五条判定放在了 `exportCheck` 的必填判定**之后**。这不影响任何「应通过」的文件，只影响 `07` 的首错行——把第 27–65 行按行拆开即可逐条验证。

### 4.2 后端层

上传通过后点提交，观察返回。

| 文件 | 期望后端结果 |
| --- | --- |
| `01_normal` | `{"code":0}`，`person` +38，`position` 全为 0 |
| `02_minimal` | `{"code":0}`，+8 |
| `03_duplicate` | `{"code":0}`；甲/乙各只建 1 条，净增 **3** |
| `04_existing_members` | `{"code":1,"msg":"<card>-<name>,该身份证已被使用，请检查您的身份证和姓名是否输入正确"}`，**整批回滚** |
| `08_special_characters` | `{"code":0}`，+32 |
| `10_large` | `{"code":0}`，+1000 |

**`03_duplicate` 的净增验证**（关键）：

```python
from apps.core.models import Person
Person.objects.filter(card__startswith="000030").count()   # 提交前后都应为 3
```

同卡同名 → 复用同一 `Person`，只重建 `ReportPerson` 关联；`position`/`type` 取数组中**最后一次出现**的值。

### 4.3 `06_header_error.xlsx` 逐 Sheet

7 个 Sheet 各另存为独立 `.xlsx`，逐个上传：

| Sheet | 期望提示 |
| --- | --- |
| `CaseA_缺表头` | `第1行姓名不能为空` |
| `CaseB_拼写错误` | `第1行姓名不能为空` |
| `CaseC_多余表头` | **无错误** |
| `CaseD_顺序调整` | **无错误** |
| `CaseE_空表头` | `第1行学校名称不能为空` |
| `CaseF_重复表头` | **无错误** |
| `CaseG_前后空格` | `第1行姓名不能为空` |

> ⚠️ **7 个 Sheet 共用同一组 3 个 `card`**（`000011…`），与 `01_normal` 的 `000010` 隔离。
> 只有 **CaseC / CaseD / CaseF** 能导入成功，三者若接连测试**且都提交**，后两个会走「同卡同名 → 复用/更新」分支——这是刻意的，但别把它们和 `01_normal` 混着提交。

### 4.4 数据库核对

```python
from apps.core.models import Person, ReportPerson
from django.db.models import Count

# 1) 测试卡片总量（0000 前缀，不会命中正式数据）
print(Person.objects.filter(card__startswith="0000").count())

# 2) 重复卡：应为空（card 全局唯一）
print(Person.objects.values("card").annotate(n=Count("id")).filter(n__gt=1))

# 3) 特殊字符是否原样入库
print(Person.objects.filter(card__startswith="000040", name__contains="<script>").values("name"))

# 4) 关联与位置
rp = ReportPerson.objects.filter(person__card__startswith="000010")
print(rp.count(), rp.values("position", "type").distinct())
```

预期：第 2 项为空集；第 3 项能查到——说明前端**未做转义**（输出侧需自行处理）。

---

## 5. 绕过界面直接验后端（可选）

```python
from apps.core.services import store_people
u = <你的测试账号>
print(store_people(u, [{"name": "测试甲", "card": "000010200001010016"}]))
# -> (True, [{"person_id": ..., "position": 0, "type": 0}])

print(store_people(u, [{"name": " ", "card": "000010200001010017"}]))
# -> (True, ...)   ← 注意：纯空格名后端也放行（见下）
```

注意：

- `store_people` **不校验 18 位**，也不校验手机号、年龄、性别——全部只在前端。
- **纯空格 `name`（`"   "`）两层都会放行**：JS 里 `"   "` 是真值，Python 里 `"   "` 也是真值。想测「空名被拒」要用**真正的空串** `""`。

---

## 6. 代码修改确认

```bash
cd /e/github/yilinbei && git status --short && git diff HEAD --stat
```

预期：`test_data/` 为未跟踪目录，`apps/`、`config/`、`manage.py` **无任何改动**。
若 `git diff` 出现业务代码变更，说明流程被破坏，应回退。

---

## 7. 离线回放（无需启动项目）

```bash
node "C:/Users/34716/AppData/Local/Temp/ylb_replay_v4.js"
```

```text
01_normal.xlsx                 PASS  imported=38   (sheet=39)
02_minimal.xlsx                PASS  imported=8    (sheet=9)
03_duplicate.xlsx              PASS  imported=5    (sheet=6)
04_existing_members.xlsx       PASS  imported=3    (sheet=4)
05_invalid.xlsx                FAIL  导入失败！参演人员名单第1行姓名不能为空            (sheet=14)
06_header_error.xlsx           FAIL  导入失败！参演人员名单第1行姓名不能为空            (sheet=4)
07_boundary.xlsx               FAIL  导入失败！参演人员名单第27行姓名长度应为2-20个字符   (sheet=67)
08_special_characters.xlsx     PASS  imported=32   (sheet=33)
09_mixed.xlsx                  FAIL  导入失败！参演人员名单第96行姓名不能为空            (sheet=101)
10_large.xlsx                  PASS  imported=1000 (sheet=1001)

--- 06_header_error.xlsx per sheet ---
  CaseA_缺表头            rows=4 | 第一条被校验行: 姓名不能为空
  CaseB_拼写错误           rows=4 | 第一条被校验行: 姓名不能为空
  CaseC_多余表头           rows=4 | 第一条被校验行: PASS
  CaseD_顺序调整           rows=4 | 第一条被校验行: PASS
  CaseE_空表头            rows=4 | 第一条被校验行: 学校名称不能为空
  CaseF_重复表头           rows=4 | 第一条被校验行: PASS
  CaseG_前后空格           rows=4 | 第一条被校验行: 姓名不能为空
```

回放脚本的 `checkPersonBasics` 五条是照抄 `origin/main` 的 `personFields.js`，**它在 `exportCheck` 中的插入位置未经代码确认**（本地前端落后远端 44 个提交，且远端拉取被连接重置）。对本测试集的影响仅限 `07` 的首错行，理由见说明书 §3.3。

同目录另有 `ylb_classify.js`，可对任意一个 xlsx 逐行输出「该行是否合规 / 会报什么错」，用于把含错文件按行拆解时定位：

```bash
node ylb_classify.js 07_boundary.xlsx
```

---

## 8. 已知限制

| 限制 | 说明 |
| --- | --- |
| 每个含错文件只暴露 1 条错误 | 首错即整批中止。要逐条验，需按行拆文件（用 §7 的 `ylb_classify.js` 先算出每行的预期文案）。 |
| `personFields.js` 五条的插入位置未知 | 见 §4.1 的提示与说明书 §3.3。只影响 `07` 的首错行。 |
| 无法验证「校验位/行政区划码是否被校验」 | `07` idx 54/55 与 §6.8 的用例表明**当前只验格式**；若你的版本还校验区划码，`00` 开头会被拒——那就需要换一套合成规则，告诉我即可。 |
| 导出侧未覆盖 | 本集只测导入。导出（`export_services.py`）的 `=1+1` 公式化风险见说明书 §5。 |
| 无日期字段 | 成员模型与表单均无日期，故无日期用例。 |
| 未覆盖 `伴奏` | `exportCheck` 不接受它（`getPosition` 里那支是死代码），故无法构造合法用例。 |

---

## 9. 数据安全

所有姓名、学校均为虚构；身份证为 `00` 开头的 18 位合成号（地区码不存在于我国行政区划，校验位按国标算出），**结构上不可能对应任何真实个人**；手机号为 `140`–`144` 段的 11 位合成号（该段未分配给公众移动通信），**不是任何可用号码**；未从任何数据库导出真实信息；全部测试记录可用 `card__startswith="0000"` 一次性定位与清理。
