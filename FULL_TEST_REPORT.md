# 项目全量测试报告

> 本轮只做测试，**未修改任何业务代码 / API / Model / Serializer / URL / 迁移 / 数据库 / `.env` / 生产配置**。
> 结论全部基于实际代码、实际测试结果与实际接口响应。

## 0. 审计范围与前置说明

| 项目 | 说明 |
| --- | --- |
| 审计对象 | 意林杯（四川省管乐展示活动）报名系统 —— Laravel 7 → Django Ninja 重构版 |
| 审计基线 | `HEAD = 1e47f0a`（会话开始时工作区干净） |
| 审计后状态 | `git diff` / `git diff --cached` 均为空 → **无任何被跟踪文件改动** |
| 测试数据库 | 内存 SQLite（通过环境变量覆盖 `DB_ENGINE=django.db.backends.sqlite3`、`DB_NAME=:memory:`） |
| 未触碰的库 | 远程 `47.108.29.34:5432` PostgreSQL（会话期间该库存在活跃连接，**全程未连接、未读写**） |
| 审计测试位置 | `E:/github/ylb_audit/`（**仓库之外**，避免污染项目） |

**关于"Excel 导入"的重要纠正**：任务书描述的是"意林杯获奖数据导入（学校/组别/奖项/指导教师/作品编号/证书编号）"。
经全仓库检索（`YilinCup`、`certificate_number`、`作品编号`、`奖项`、`load_workbook`、`read_excel`），
**本仓库不存在任何 Excel 导入功能，也不存在上述字段与模型**。本项目的"意林杯"是**管乐展示活动报名系统**
（`Report` 节目报名表 / `Person` 人员 / `LiveReport` 现场 / `Ticket` 票务 / `Draw` 抽签），
见 [registration_form.py:22](apps/api/registration_form.py#L22) 的表头 `"意林杯"四川省第十二届管乐展示活动`。
因此第六轮"Excel 导入专项"在本仓库**无对应被测对象**（详见 §7）。

**并发写入提示（非本次审计产生）**：审计过程中仓库根目录出现新的未跟踪目录 `test_data/member_import/`
（10 个 `*.xlsx` + `_09_plan.md`，写入时间 01:54–01:56），**由另一进程写入，不是本轮审计产生**
（本轮测试全部运行在内存 SQLite 且从不写仓库文件）。当前代码中**没有任何模块引用 `test_data/`**。
该目录已被保留未做任何处理，请确认其归属。

---

## 1. 测试环境

| 项目 | 结果 |
| --- | --- |
| Python | 3.11.9（`.venv` 与系统 Python 一致） |
| Django | 3.2.24 |
| Django Ninja | 0.22.2（pydantic 1.10.15） |
| 虚拟环境 | `E:\github\yilinbei\.venv`（依赖与 `requirements.txt` 一致） |
| 数据库（生产配置） | PostgreSQL，`DB_HOST` 默认 `127.0.0.1:5432`；另有 GaussDB 分支（`GAUSSDB_*`） |
| 数据库（本轮实际使用） | 内存 SQLite（隔离测试库，Django TestCase 自动建/毁） |
| Git Branch | `master` |
| Commit | `1e47f0aec5612af4e48b6e7950502f9570c17af5` |
| Test Settings | `django_config.settings`（`pytest.ini` 指定；**项目无独立 test settings 文件**） |
| 测试框架 | pytest 9.1.1 + pytest-django 4.14.0（项目亦兼容 `manage.py test`） |
| 配置文件 | **无 `.env`**；`SECRET_KEY` 由环境变量注入（settings.py:11-13 未设置即 `RuntimeError`） |

**数据库风险结论（已按要求优先处置）**：
- 项目根目录**不存在 `.env`**，settings 默认 `DB_HOST=127.0.0.1`，本地 `5432` 无监听。
- 会话期间检测到本机对 `47.108.29.34:5432`（即 `.env.example` 中标注的生产 IP）的**活跃连接**。
- 因此本轮**全部测试通过环境变量强制指向内存 SQLite**，未对任何真实数据库执行建库/写库/删库操作。

### 第一轮：Django 基础检查

| 命令 | 结果 |
| --- | --- |
| `manage.py check` | `System check identified no issues (0 silenced).` |
| `manage.py makemigrations --check --dry-run` | `No changes detected` → 模型与迁移一致，无缺失迁移 |
| `manage.py export_openapi`（输出到临时文件） | 与仓库内 `openapi.json` **完全一致**（无漂移） |
| `manage.py check --settings=<测试settings>` | 项目无独立测试 settings，无法执行（不存在该配置） |

- URL 配置、Model 配置、settings 加载、import 与依赖均无报错。
- `INSTALLED_APPS` 中的 `corsheaders`、`apps.core`、`apps.api` 加载正常。

---

## 2. 测试统计

### 2.1 按类别（任务书表格口径）

| 类型 | PASS | FAIL | ERROR | SKIP |
| --- | ---: | ---: | ----: | ---: |
| 自动化测试（项目自带） | 86 | 0 | 0 | 0 |
| API（接口矩阵 + 畸形输入，15） | 2 | 13 | 0 | 0 |
| Excel 导入 | — | — | — | — |
| Excel 导出（17） | 12 | 5 | 0 | 0 |
| 数据库（18） | 18 | 0 | 0 | 0 |
| 权限（17） | 10 | 7 | 0 | 0 |
| 安全基线（13） | 12 | 1 | 0 | 0 |
| 性能/边界（3） | 2 | 1 | 0 | 0 |
| 对照探针（正常入参，3） | 3 | 0 | 0 | 0 |
| **合计** | **145** | **27** | **0** | **0** |

> "Excel 导入"一行留空：本仓库无该功能，无被测对象（非"跳过"）。
> 另有 57 个子测试（subTest）通过，未单列。

### 2.2 按审计模块

| 模块 | 文件 | PASS | FAIL | 耗时 |
| --- | --- | ---: | ---: | ---: |
| 项目自带测试集 | `tests/`（仓库内，未改动） | 86 | 0 | 10.4s |
| 权限/越权/数据隔离 | `test_audit_authz.py` | 10 | 7 | 1.8s |
| 接口状态码矩阵 | `test_audit_api_matrix.py` | 2 | 4 | 3.6s |
| Excel/PDF 导出 | `test_audit_exports.py` | 12 | 5 | 2.1s |
| 数据库/CRUD/畸形输入 | `test_audit_db.py` | 18 | 9 | 1.4s |
| 安全基线 | `test_audit_security.py` | 12 | 1 | 1.5s |
| 性能/边界/N+1 | `test_audit_boundary.py` | 2 | 1 | 10.1s |
| 正常入参对照探针 | `test_audit_probe_ok.py` | 3 | 0 | 0.6s |

### 2.3 第二轮：项目自带完整测试集

```
python -m pytest -q          # →  86 passed, 41 subtests passed in 11.19s
```

| 指标 | 值 |
| --- | --- |
| 总测试数 | 86 |
| PASS | 86 |
| FAIL | 0 |
| ERROR | 0 |
| SKIP | 0 |
| XFAIL | 0 |
| 耗时 | 约 11.2s |

**项目自带测试集全部通过**（登录契约、角色门禁、报表事务、软删除、人员身份复用、导出兼容、票务容量、分页、OpenAPI）。

### 2.4 第三轮：API 全量清单

- `openapi.json` 声明 **82 个路径 / 87 个操作**（与实际代码一致）。
- 已对全部 86 条 `方法 + 路径` 组合逐一探测：匿名 / type0 / type2 / type3 四种身份，外加错误方法与不存在 ID。

**状态码矩阵（节选异常项；`anon`=匿名，`t0/t2/t3`=学校/组委会/管理员）**

| 方法 路径 | anon | t0 | t2 | t3 | 判定 |
| --- | --- | --- | --- | --- | --- |
| `PUT /api/admin/chouqian/update` | 405 | 405 | 405 | 405 | **P0 路由遮蔽** |
| `GET /api/admin/chouqian/exportall` | 401 | 422 | 422 | 422 | **P0 路由遮蔽** |
| `POST /api/file/create` | 401 | 500 | 500 | 500 | **P1 缺必填即 500** |
| `POST /api/scan/cau` | 401 | 500 | 500 | 500 | **P1 缺必填即 500** |
| `POST /api/committee/user/` | 401 | 403 | 500 | 403 | **P1 空 body 撞唯一约束** |
| `GET /api/export/data` | 401 | **200** | **200** | 200 | **P0 无角色门禁** |
| `GET /api/ticket/my` | **200** | 200 | 200 | 200 | **P1 未认证 PII** |
| `GET /api/ticket/list`、`/message/{code}`、`POST /api/ticket/make` | 200 | 200 | 200 | 200 | 有意公开（见 §6） |
| `GET /api/health` | 200 | 200 | 200 | 200 | 设计如此，返回常量 |
| 其余 admin/* | 401 | 403 | 403 | 200 | 正确 |
| 其余 committee/* | 401 | 403 | 200 | 403 | 正确 |
| city/*、province/*、school/* | 401 | 仅 school 放行 | 403 | 403 | 正确 |

- **错误 HTTP 方法**：非 405/401/403 的响应为 `{}` → 错误方法均按 405/401/403 处理，无异常。
- **不存在 ID**：`/api/admin/report/{id}`、`/api/school/report/{id}`、`/api/ticket/message/{code}`、`/api/live/{id}` 均返回 200 + `code:1`/"获取成功但 data 为 null"，与 Laravel 的 200-业务失败契约一致。

---

## 3. P0

> 真正阻断运行 / 数据安全 / 核心业务。

### P0-1 组委会可越权把任意账号提权为管理员，并可复活已删除的管理员
- 位置：[views.py:478-485](apps/api/views.py#L478-L485)（`user_update_admin` 直接写入 `type`）、[views.py:516-541](apps/api/views.py#L516-L541)（`/committee/user/` 复用该函数）、[views.py:504-506](apps/api/views.py#L504-L506)（`user_restore_admin`）
- 复现：组委会（`type=2`）`PUT /api/committee/user/` `{"id": <任意用户>, "type": 3}` → `HTTP 200 {"code":0}`，该用户 `type` 由 `0` 变为 `3`。
- 复现：组委会 `PUT /api/committee/user/restore` `{"ids":[<已软删管理员>]}` → `deleted_at` 被置回 `None`，管理员账号复活。
- 实测证据：`victim.type 0 → 3`；`admin.deleted_at None → None（被清空）`。
- 影响：**整套角色权限体系可被完全绕过** —— 组委会可自造管理员，进而访问 `/api/admin/*` 全部接口（含全量导出、用户管理、人员管理）。
- 对照：[views.py:488-496](apps/api/views.py#L488-L496) 中**创建**用户时对组委会强制 `type=0`（实测通过），说明"组委会不得产生管理员"是既定意图，**PUT 路径漏掉了同一限制**。

### P0-2 `PUT /api/admin/chouqian/update` 完全不可达（405）—— 抽签排序保存功能整体失效
- 位置：[views.py:583-586](apps/api/views.py#L583-L586)（`GET /admin/chouqian/{type}`）注册在 [views.py:589-595](apps/api/views.py#L589-L595)（`PUT /admin/chouqian/update`）**之前**
- 实测：`PUT /api/admin/chouqian/update` → `405`（匿名/学校/组委会/管理员**四种身份全部 405**，说明在鉴权前就被路由拦掉）。
- 原因：字面量路径 `.../chouqian/update` 先被 `{type}` 模式匹配，`type="update"`，而该 operation 只允许 GET → 405。
- 影响：**"保存抽签顺序"这一核心管理功能完全不可用**。
- 佐证意图：作者已为 report 路由显式处理过同类问题并留下注释 [views.py:450-452](apps/api/views.py#L450-L452)（"PUT 必须注册在 GET `/{id}` 之前……否则 405"），但**未对 chouqian 应用同一规则**。

### P0-3 `GET /api/admin/chouqian/exportall` 不可达（422）—— 导出全部抽签表功能失效
- 位置：[views.py:598-604](apps/api/views.py#L598-L604)（`GET /admin/chouqian/export/{type}`）注册在 [views.py:607-617](apps/api/views.py#L607-L617)（`GET /admin/chouqian/exportall`）**之前**
- 实测：`GET /api/admin/chouqian/exportall` → `422`，响应体：
  `{"detail":[{"loc":["path","type"],"msg":"value is not a valid integer","type":"type_error.integer"}]}`
- 对照：`GET /api/admin/chouqian/export/1`（单类别导出）→ `200` + 正确 xlsx，证明不是权限或数据问题，纯粹是**路由遮蔽**。
- 影响：**"导出所有类别抽签排序表"功能完全不可用**。

### P0-4 `/api/export/data` 无角色门禁且不按归属过滤 —— 普通学校账号可导出全平台数据
- 位置：[views.py:382-388](apps/api/views.py#L382-L388)：`@api.get("/export/data", auth=auth)` → `Report.objects.all()`，**既无 `role_error`，也无 `user_id` 过滤**（对比同文件的 `/api/export/report` [views.py:361-366](apps/api/views.py#L361-L366) 正确按 `user_id=request.auth.id` 过滤）。
- 实测：学校账号（`type=0`）调用 `GET /api/export/data` → `HTTP 200`，导出的 xlsx 同时包含 `['本校节目', '他校节目']`。
- 矩阵佐证：`/api/export/data` 对 `type0/type2/type3` 一律 200。
- 影响：**任一登录账号可一次性拉取全部参赛单位的报名数据**（含联系人、电话、联系地址、曲目、人员名单），属跨租户数据泄露。

---

## 4. P1

### P1-1 报名表创建时 `status` 可被客户端直接指定 —— 绕过审核
- 位置：[views.py:97-120](apps/api/views.py#L97-L120)：`create_report` 的字段白名单 `values = {f.name for f in Report._meta.fields}` **未剔除 `status`**（[views.py:108-110](apps/api/views.py#L108-L110)），随后 `Report.objects.create(user_id=..., **payload)` 直接落库。
- 实测：`POST /api/school/report/create` 携带 `{"status": 1, "remark": "client-supplied"}` → 落库 `status=1`（"已通过"）、`remark='client-supplied'`。
- 对照：**同一文件的 `update_report` 明确剔除 status**（[views.py:142](apps/api/views.py#L142) `if key in fields and key not in {"status", "dinner_reservation"}`）并在结尾强制 `report.status = 0`（[views.py:148](apps/api/views.py#L148)）。实测 `PUT` 提交 `status:-1` 后被重置为 `0`。
- 影响：**创建与更新两条路径行为不一致**，客户端可在创建时自审批，绕过组委会/管理员的审核流程。

### P1-2 系统性 500：缺必填 / 类型错误 / 提交冲突均未做输入校验（13 项）
同一类根因（直接使用未校验的请求数据，异常未捕获，Django 以 500 兜底）：

| 请求 | 实际 | 根因 |
| --- | --- | --- |
| `POST /api/file/create` `{}` | 500 | `NOT NULL constraint failed: files.size`（[views.py:307-311](apps/api/views.py#L307-L311)；`Files.size` 无默认值 [models.py:261](apps/core/models.py#L261)） |
| `POST /api/scan/cau` `{}` | 500 | `NOT NULL constraint failed: scan_files.type`（[views.py:337-343](apps/api/views.py#L337-L343)；`ScanFiles.type` 无默认值 [models.py:273](apps/core/models.py#L273)） |
| `POST /api/login` body=`[]` | 500 | `parse_body` 返回 list，`data.get` → AttributeError（[services.py:27-33](apps/core/services.py#L27-L33)） |
| `POST /api/login` body=`"abc"` | 500 | 同上（JSON 标量） |
| `PUT /api/school/report/update` `{"id":"abc"}` | 500 | `filter(pk="abc")` → ValueError（[views.py:126](apps/api/views.py#L126)） |
| `POST .../report/create` `person:"not-a-list"` | 500 | 字符串被迭代（[services.py:127-153](apps/core/services.py#L127-L153)） |
| `POST .../report/create` `person:["str"]` | 500 | 元素非对象，`item.get` 失败 |
| `PUT /api/admin/report/check` `status:"pass"` | 500 | `update(status="pass")` → ValueError（[views.py:440-447](apps/api/views.py#L440-L447)） |
| `POST /api/ticket/make` `ticket_id:"abc"` | 500 | `filter(pk="abc")` → ValueError（[views.py:841](apps/api/views.py#L841)） |
| `GET /api/admin/report/list?status=0 OR 1=1` | 500 | `filter(status="0 OR 1=1")` → ValueError（[views.py:83-84](apps/api/views.py#L83-L84)） |
| `POST /api/admin/user/` `{"username":"<已存在>"}` | 500 | `users.username` 唯一约束未预校验（[views.py:488-496](apps/api/views.py#L488-L496)） |
| `PUT /api/admin/person` 改卡号为已占用值 | 500 | `person.card` 唯一约束未预校验（[views.py:570-580](apps/api/views.py#L570-L580)） |
| `POST /api/admin/user/`（空 body，重复调用） | 500 | `username` 落到默认 `" "`，第二次撞唯一约束 |

- 对照探针（正常入参）全部 200：`file/create`（含 size）、`scan/cau`（含 type）、`admin/person`（合法 head）→ 证明**端点本身可用，纯属校验缺口**。
- 影响：任一登录用户提交畸形/不完整请求即可稳定触发 500（可用性/健壮性风险）。

### P1-3 `/api/file/list` 水平越权（IDOR）：任意登录用户可读取他人文件记录
- 位置：[views.py:314-318](apps/api/views.py#L314-L318)：`Files.objects.filter(id__in=ids)` —— **无 `user_id` 归属过滤**。
- 实测：攻击者账号 `GET /api/file/list?ids[]=<他人文件id>` → `{"code":0,"data":[{... 他人 filename/url ...}]}`。
- 影响：可枚举他人上传文件的文件名与 URL（`Files.url` 常为可直接访问的 OSS 地址）。

### P1-4 `/api/ticket/my` 未认证即泄露预约人手机号与 IP（PII 枚举）
- 位置：[views.py:819-825](apps/api/views.py#L819-L825)：仅凭 query 的 `name` + `card` 查询，**无 `auth=`**。
- 实测（完全未认证）：`GET /api/ticket/my?name=李四&card=510199` → `code:0`，返回 `phone`、`ip`、`code` 等完整预约记录。
- 说明：票务接口公开是**文档化的有意设计**（见 §6），但 `/ticket/my` 以"姓名+身份证号"为凭据返回手机号与 IP，构成可批量枚举的个人信息泄露，建议单独评审。（`/api/ticket/message/{code}` 用随机 `code` 作凭据，风险相对低。）

---

## 5. P2

| # | 问题 | 位置 / 实测 |
| --- | --- | --- |
| P2-1 | **报表列表 N+1 查询**：`/api/admin/report/list?limit=10`（10 报表 × 3 人）实测 **76 条 SQL**；而 `/api/admin/export/data2`（50 报表 × 2 人）仅 **9 条**（批量良好） | [services.py:58-70](apps/core/services.py#L58-L70)、[services.py:199-215](apps/core/services.py#L199-L215)（`report_dict` 逐行查 user/links/person/files） |
| P2-2 | **`TOKEN_TTL_HOURS` 配置不生效**：中间件按**硬编码 4h** 清理令牌；`override_settings(TOKEN_TTL_HOURS=24)` 下，5 小时未使用的令牌仍被拒（401） | [middleware.py:17-26](apps/core/middleware.py#L17-L26) 硬编码 `timedelta(hours=4)`；而 [models.py:162-165](apps/core/models.py#L162-L165) 使用 `settings.TOKEN_TTL_HOURS` → 两者不一致 |
| P2-3 | **Excel 公式注入**：用户可控文本以 `=` 开头时被写为**公式类型**单元格（`data_type='f'`）。实测报表名 `=1+1` → 导出 xlsx 的 `D2` 单元格为公式 `=1+1` | [export_services.py:307](apps/api/export_services.py#L307)（`sheet.append(list(row))` 直接写入原始值）。（`@`/`+` 开头的值被 openpyxl 存为字符串，未构成公式。） |
| P2-4 | **导出中文文件名被 RFC2047 编码**：`Content-Disposition` 实际为 `=?utf-8?b?YXR0YWNobWVudDsgZmlsZW5hbWU9IuaVsOaNruWvvOWHui54bHN4Ig==?=`，解码后才等于 `attachment; filename="数据导出.xlsx"`，且**不含 `filename*=`**；浏览器通常不在 `Content-Disposition` 中解码 RFC2047，下载文件名可能异常 | [export_services.py:350](apps/api/export_services.py#L350)、[views.py:209](apps/api/views.py#L209)（Django 强制 latin-1 头所致） |
| P2-5 | **`/api/scan/list` 无角色门禁**：普通学校账号（`type=0`）调用 → `HTTP 200`，可列举全平台用户及联系方式（实测 `count=2`，含 `nickname`/`tel`） | [views.py:321-334](apps/api/views.py#L321-L334) |
| P2-6 | **导出无范围限制**：`/api/export/data`、`/api/admin/export/data1|data2` 一律全量导出，无分页/时间窗 | 实测 5000 条时 data2 达 **2.06s / 691KB**（见 §5.1 性能数据） |
| P2-7 | **`Report.user_id`、`ReportPerson.person_id`、`LiveReport.report_id` 无外键约束**：实测可写入不存在的 `user_id=999999`、`person_id=777777` 而不报错；用户软删除后报表仍指向该 id | [models.py:173](apps/core/models.py#L173)、[models.py:230](apps/core/models.py#L230)、[models.py:300](apps/core/models.py#L300)（沿用 Laravel 无 FK 语义，**属已知设计，此处仅记录**） |
| P2-8 | **`ticket_subscribe` 无 `(ticket_id, card)` 唯一约束**：仅靠应用层 `select_for_update` + `exists()` 校验；实测直接写入两条相同 `(ticket_id, card)` 成功。SQLite 下 `select_for_update` 为 no-op，**并发去重语义无法在本环境验证** | [models.py:393-405](apps/core/models.py#L393-L405)、[views.py:839-840](apps/api/views.py#L839-L840) |
| P2-9 | **空 body 创建出 `username=" "` 的账号**：`User.username` 默认值为 `" "`，创建接口未校验非空 | [views.py:488-496](apps/api/views.py#L488-L496)、[models.py:82](apps/core/models.py#L82) |

### 5.1 第十轮：性能 / 边界（内存 SQLite，仅供相对比较）

| 数据量 | data1 | data2 | export/data | report_list(limit=10) | data2 行数 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 0.014s / 6.2KB | 0.016s / 6.9KB | 0.013s / 6.2KB | 0.011s | 11 |
| 100 | 0.031s / 13.4KB | 0.037s / 18.2KB | 0.030s / 13.2KB | 0.011s | 111 |
| 1000 | 0.161s / 82.7KB | 0.304s / 129.2KB | 0.186s / 81.3KB | 0.012s | 1111 |
| 5000 | 1.210s / 430.4KB | 2.064s / 691.1KB | 1.304s / 421.3KB | 0.014s | 6111 |

- 耗时随行数近似线性，**未见超时或异常**；`report_list` 分页耗时与总数无关（符合预期），但**每页查询数随行数线性增长**（P2-1）。
- 此数据为 SQLite 内存库结果，**不能等同 PostgreSQL/GaussDB 生产表现**。

---

## 6. 已知差异（与旧项目的差异，非缺陷）

> 本地**无 Laravel 源码**（仅有 16 个历史迁移文件的转述），故差异判断依据仓库内
> `API_COMPATIBILITY.md` / `MIGRATION_GAPS.md` 的**文档化契约**，以及与 Laravel 惯例的对比。

| # | 差异 | 分类 |
| --- | --- | --- |
| 1 | 票务 `/api/ticket/list`、`/message/{code}`、`/my`、`/make` **保持公开无鉴权** | **有意改动**（`API_COMPATIBILITY.md` 明示 "Ticket endpoints remain public"；但 §P1-4 的 PII 暴露建议单独评审） |
| 2 | 票务截止时间硬编码 `2024-11-14 20:00:00`，当前（2026）已永久放行 | **有意改动**（与 Laravel 源码一致，文档第 1 条） |
| 3 | `school/index/percent`、`province/index/percent` 在 Laravel 无对应方法，Django 返回固定空规则 | **有意改动**（文档第 2 条，已在 `MIGRATION_GAPS.md` 记录） |
| 4 | `report.origin/territory/group_type`、`report_person.table_id` 运行时被引用但不在迁移中 → Django 未凭空建列 | **有意改动**（文档第 3 条） |
| 5 | 令牌时效：Laravel `expiration=null`（不过期）vs Django `TOKEN_TTL_HOURS` | **兼容性改进**（`MIGRATION_GAPS.md` §4 已记录）；但见 P2-2 配置未生效 |
| 6 | 返回结构与状态码契约（成功 200/`code:0`，业务失败 200/`code:1`，角色拒绝 403/`{"error":...}`，缺令牌 401） | **有意保留**，实测与文档 100% 一致 |
| 7 | 导出列序、表头、`ADMIN_DATA2_HEADINGS` 的 17 个乐器列、抽签工作表标题、固定中文附件名 | **有意保留**，实测与常量一致（见下） |
| 8 | 纯 bcrypt 哈希登录：Django `check_password` 静默拒绝 `$2y$`，改用 `verify_user_password` 并透明升级 PBKDF2 | **兼容性改进**（修复了 Django 3.2 的固有缺陷） |
| 9 | 前端引用但未注册的路径（`/api/rules`、`/api/files/`、`/api/chouqian/school/*`、`/api/v2/*` 等） | **尚无法判断**（`MIGRATION_GAPS.md` 列为待证据项，未加 shim） |
| 10 | `PUT /admin/chouqian/update`(405)、`GET /admin/chouqian/exportall`(422) | **疑似回归**（见 P0-2/P0-3：两个接口在 `views.py` 中均有实现且已写入 `openapi.json`，证明意图存在，仅因路由顺序不可达） |

### 6.1 Excel 导出专项核对结果（全部**通过**，未发现规则错误）

| 核对项 | 结果 |
| --- | --- |
| `ADMIN_DATA2_HEADINGS` 列数 = 1 + 17 乐器 + 1 合计 = **35** | ✅ 实测 35 列，末列 `合计` |
| 17 个乐器列与 `INSTRUMENTS` 逐列一致（含序） | ✅ `ADMIN_DATA2_HEADINGS[17:34] == INSTRUMENTS`，无重复列名 |
| 已知旧值归一化：`低音单簧→低音单簧管`、`中音萨克→中音萨克斯`、`次中音萨→次中音萨克斯`、`上低音萨→上低音萨克斯`、`低音大提→低音大提琴` | ✅ `低音单簧` 正确计入 `低音单簧管` 列 |
| 未知值与空值进入"其他" | ✅ `小提琴`/`""`/`None` 共 3 条全部计入 `其他` |
| 仅 `position` 0/1（正式/预备队员）参与乐器计数 | ✅ `position 2/4` 各 1 人时计数仍为 2 |
| 合计列 = 各乐器列之和 = 参与计数人数 | ✅ 7 == 7；2 == 2 |
| 指挥取值：多个指挥时取**最后一个**（保留 Laravel 行为） | ✅ 实测取 `指挥2` |
| 指挥/指导老师身份证前缀 `'`（防 Excel 科学计数） | ✅ 实测 `'510123456789012345` |
| `data1` / `report` 导出列数 = 19；`export_code` 未知识别为 `-`；`seconds_to_human`、`status_label` 映射 | ✅ |
| 空数据导出仅 1 行表头，表头与常量一致 | ✅ |
| Content-Type | ✅ `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| `/api/admin/user/export` 不泄露口令哈希 | ✅ 无 `pbkdf2`/`$2y$`/`$2b$`，`密码` 列为固定提示文案 |
| `/api/export/report`、`/api/export/person` 返回真实 PDF（`%PDF` 魔数 + 中文字体注册） | ✅ |
| 抽签单类别导出 `/api/admin/chouqian/export/1` | ✅ 200 + xlsx |
| 抽签全量导出 `/api/admin/chouqian/exportall` | ❌ **422**（P0-3，路由遮蔽） |

---

## 7. 无法验证项目

| 测试项目 | 原因 | 需要什么条件才能验证 |
| --- | --- | --- |
| **Excel 导入（全部子项）**、作品编号 / 证书编号 / 奖项 / 指导教师 Excel 表头与字段映射、`create/update/update_or_create/ignore` 语义、部分成功与事务边界 | **本仓库不存在该功能**（无 `YilinCup` 模型、无 `load_workbook`、无 Excel 上传接口；`import_laravel_data` 是 **CSV 用户导入**）。`test_data/member_import/*.xlsx` 夹具虽存在，但无任何代码引用 | 需先确认该功能归属于哪个仓库/分支（疑似正在被另一进程开发）；待实现后即可按本报告 §5.1 的表头/类型/编号/部分成功矩阵逐项验证 |
| PostgreSQL 9.2.4 真实行为 | 本地无实例、无凭证；远程 `47.108.29.34:5432` 属生产，**禁止连接** | 提供隔离的 PostgreSQL 9.2.4 实例与匿名化数据 |
| GaussDB 兼容性（类型、版本、模式、驱动） | 无实例、无厂商适配驱动 | 提供 GaussDB 版本/兼容模式/驱动 |
| `select_for_update` 并发语义（票务去重、超额、报表并发写） | **SQLite 不支持行锁**（`has_select_for_update=False`，静默降级为 no-op） | 在 PostgreSQL/GaussDB 上跑并发用例 |
| Laravel 逐行源码对比（URL/方法/参数/返回字段/状态码全量比对） | **本地无 Laravel 源码**，仅有历史迁移文件转述 | 提供 Laravel 仓库或生产 API 的抓包基线 |
| Excel/PDF **黄金文件**二进制比对 | 无官方模板基线（`API_COMPATIBILITY.md` 第 4 条明示待办） | 提供组委会《附件2》等官方模板与前端导出基线 |
| 七牛 / 阿里云 STS AssumeRole / OSS 真实直传与上传代理 | 无 AccessKey/Bucket/RoleArn 凭证 | 提供测试 Bucket 与 RAM 角色（本地已用 mock 验证了业务校验分支：biz、大小上限、扩展名、key 生成不取自用户文件名） |
| 700MB 级视频直传、大文件 multipart 超时 | 无凭证 + 无前端 | 前端联调环境 |
| SMTP / Redis / Celery 集成 | 未提供凭证/服务 | 提供测试 SMTP 与 Redis |
| 未被后端注册但前端引用的路径（`/api/rules`、`/api/v2/*`、`/api/chouqian/school/*`…） | 需前端调用基线才能判断是否需要 shim | 前端仓库 + 真实抓包 |
| 敏感字段脱敏规则（身份证/手机号/地址） | 规则需数据所有者确认（`MIGRATION_GAPS.md` 待确认项 4） | 数据所有者确认脱敏口径 |
| `school/index/percent`、`province/index/percent` 业务语义 | Laravel 无对应实现，无从对齐 | Laravel 源码或组委会规则说明 |

---

## 8. 最终结论

# FAIL

**判定依据**：

1. **项目自带测试集 86/86 全通过，`manage.py check` 无问题，迁移与模型一致，OpenAPI 无漂移** —— 工程基本面良好。
2. 但**独立回归测试发现 4 个 P0**：
   - 组委会可越权提权为管理员并复活已删管理员（**权限体系可被完全绕过**）；
   - `PUT /api/admin/chouqian/update` 405、`GET /api/admin/chouqian/exportall` 422（**两个核心业务功能完全不可用**）；
   - `/api/export/data` 对普通学校账号放行全平台数据导出（**跨租户数据泄露**）。
3. 另有 4 个 P1（自审批绕过审核、13 项畸形输入稳定触发 500、`/api/file/list` IDOR、`/api/ticket/my` 未认证 PII 泄露）。

**结论边界（重要）**：
- 本判定基于 **commit `1e47f0a`**、**内存 SQLite** 环境，以及**文档化契约**（本地无 Laravel 源码、无 PostgreSQL/GaussDB 实例）。
- 因此这是**当前 Django 实现的测试结论**，不代表已在生产数据库/GaussDB 上验证通过。
- 若仅看项目自带测试集，结果是 `PASS`；**加上独立审计，结论为 `FAIL`**，需修复 P0 后复测。

---

## 附录 A：复现方式（本轮使用的确切命令）

```bash
# 系统检查（不触碰真实库）
SECRET_KEY=<any> DB_ENGINE=django.db.backends.sqlite3 DB_NAME=:memory: \
  ./.venv/Scripts/python.exe manage.py check

# 迁移漂移检查（只读）
SECRET_KEY=<any> DB_ENGINE=django.db.backends.sqlite3 DB_NAME=:memory: \
  ./.venv/Scripts/python.exe manage.py makemigrations --check --dry-run

# 项目自带完整测试集
SECRET_KEY=<any> DB_ENGINE=django.db.backends.sqlite3 DB_NAME=:memory: \
  ./.venv/Scripts/python.exe -m pytest tests -q

# 本轮独立审计套件（位于仓库外，零污染）
PYTHONIOENCODING=utf-8 SECRET_KEY=<any> DJANGO_SETTINGS_MODULE=django_config.settings \
  DB_ENGINE=django.db.backends.sqlite3 DB_NAME=:memory: \
  ./.venv/Scripts/python.exe -m pytest E:/github/ylb_audit/ -q -p no:cacheprovider
```

## 附录 B：本轮新增文件清单（唯一改动）

| 路径 | 说明 |
| --- | --- |
| `E:/github/ylb_audit/test_audit_authz.py` | 认证/授权/越权/数据隔离（17 用例） |
| `E:/github/ylb_audit/test_audit_api_matrix.py` | 全量接口状态码矩阵（6 用例） |
| `E:/github/ylb_audit/test_audit_exports.py` | Excel/PDF 导出专项（17 用例） |
| `E:/github/ylb_audit/test_audit_db.py` | Model 约束/CRUD/软删除/畸形输入（27 用例） |
| `E:/github/ylb_audit/test_audit_security.py` | 注入/XSS/错误泄露/上传/CORS（13 用例） |
| `E:/github/ylb_audit/test_audit_boundary.py` | 10/100/1000/5000 性能与 N+1（3 用例） |
| `E:/github/ylb_audit/test_audit_probe_ok.py` | 正常入参对照探针（3 用例） |
| `E:/github/ylb_audit/matrix.out`、`openapi.fresh.json` | 原始输出与 OpenAPI 漂移比对产物 |
| `FULL_TEST_REPORT.md` | 本报告（仓库根目录，**唯一写入仓库的文件**） |

**未改动**：`apps/`、`django_config/`、`tests/`、`manage.py`、`pytest.ini`、`requirements.txt`、`openapi.json`、`docs/`、`deploy/`、`.github/`、`.env.example`、任何迁移与数据库数据。
