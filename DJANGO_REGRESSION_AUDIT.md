# Django 重构回归专项审计

审计对象：当前 Django 项目（`e:\github\yilinbei`，commit `1e47f0a`）对照 Laravel 原版（`C:\Users\34716\OneDrive\Desktop\contest\ylb_intial\ylb_api`）。
本轮只做对照，**未修改任何代码/测试/数据库/迁移/配置**。

---

## 1. 审计原则

本报告只判断一件事：**Laravel → Django 是否发生了行为回归。**

判定分类（严格按此执行，不按安全最佳实践自行加码）：

| 分类 | 含义 | 处理 |
| --- | --- | --- |
| 情况 A | 原版没有该问题，Django 出现了 | **真正的 Django 回归** |
| 情况 B | 原版也存在同样行为 | 原版既有行为，**不计入回归** |
| 情况 C | 原版行为不同，Django 改成了另一种行为 | 迁移回归，需重点报告 |
| 情况 D | 原版无对应功能 / 无法从原版代码确认 | 标记**无法确认**，不猜 |

已按要求排除、本轮不再作为问题处理：`/api/export/data`（上轮已定案为原版既有行为）、组委会用户修改 `type`、抽签相关（`/api/admin/chouqian/update`、`/api/admin/chouqian/exportall`）。

原版侧证据获取方式说明：原版仓库**未随附 `vendor/`**（`.gitignore:5` 忽略 `/vendor`，`git ls-files vendor` 为空，从未被 git 跟踪），因此凡行为落在框架内部（PhpSpreadsheet / Symfony HttpFoundation）而非 `app/` 代码的，一律记为「无法确认」，不以框架常识代替证据。

---

## 2. 真正的 Django 回归

以下 5 项均已确认属于**重构产生的行为回归**（情况 A / C）。编号 R-*。

### R-1 异常兜底契约未迁移：原版「一律吞成 200 + code:1」变成 Django 未捕获异常 → 500

| 项 | 内容 |
| --- | --- |
| **Laravel 原版** | 每个 controller 方法整体包在 `try { ... } catch (\Exception $e) { ... return json_fail(...); }` 里；`json_fail()` 返回 `response()->json(['code'=>1,'msg'=>...,'data'=>...])`，**HTTP 200**。`app/Exceptions/Handler.php` 的 `render()`/`report()` 只是 `parent::render()`/`parent::report()`，未对任何异常类型做特判，因此异常能否逃逸完全取决于 controller 本地是否捕获。原版 `app/` 全树 **0 处** `$request->validate()`、**无** `app/Http/Requests` 目录、`Validator` 导入后从未调用，即**原版不做输入校验，而是用 catch-all 把一切失败收敛为业务错误响应**。 |
| **Django 当前版** | `api = NinjaAPI(...)`（[views.py:53](apps/api/views.py#L53)）未注册任何 exception handler；`MIDDLEWARE`（[settings.py:33-45](django_config/settings.py#L33-L45)）只有 `TokenCleanupMiddleware` / `AuditRequestMiddleware`，无兜底。异常直接冒泡至 Django 500 处理器。 |
| **差异** | 同一份畸形请求：原版 200 + `{"code":1,"msg":...}`；Django 500。**这是本轮最重要的回归** —— 它把原版"错误也走业务响应契约"的全局设计整体丢失，且不限于个别端点。 |
| **严重程度** | **高** |

原版 catch-all 位置（逐条可查）：`Api/Admin/ReportController.php:11-37`、`Api/FileController.php:16-32`、`Api/ScanFilesController.php:45-71`、`Api/AuthController.php:32-49`、`Api/Admin/UserController.php:54-72`、`Api/Admin/PersonController.php:27-44`、`Api/TicketController.php:62-148`、`Api/School/ReportController.php:47-103`、`Api/City/ReportController.php:47-107`、`Api/Province/ReportController.php:47-89`；`json_fail()` 定义于 `app/Helpers/json.php:21-23`。

#### 逐端点对照（上一轮 P1-2 的 13 项全部复核）

| # | 请求 | Laravel 原版 | Django 当前版 | 是否回归 |
| --- | --- | --- | --- | --- |
| 1 | `POST /api/file/create` `{}` | 200 `json_fail(<DB错误>)`，`catch` 捕获 `QueryException`（`Api/FileController.php:16-32`） | **500**（`files.size` NOT NULL，[views.py:307-311](apps/api/views.py#L307-L311)） | **回归** |
| 2 | `POST /api/scan/cau` `{}` | 200 `json_fail('新增或修改失败！')`（`Api/ScanFilesController.php:45-71`，`$data['type']` 未定义→`ErrorException` 被捕获） | **500**（`scan_files.type` NOT NULL，[views.py:337-343](apps/api/views.py#L337-L343)） | **回归** |
| 3 | `POST /api/login` body=`[]` | 200 `json_fail('用户不存在')`（`request('username')`→null→`whereNull`→无匹配） | **500**（`parse_body` 返回 list，`.get` → AttributeError，[services.py:27-33](apps/core/services.py#L27-L33)） | **回归** |
| 4 | `POST /api/login` body=`"abc"` | 同上，200 `json_fail('用户不存在')` | **500** | **回归** |
| 5 | `PUT /api/school/report/update` `{"id":"abc"}` | **500**：`Report::find('abc')`→null→`null->fill($data)` 抛 PHP `Error`，而 `catch (\Exception)` **不捕获 `Error`**（`Api/School/ReportController.php:127-130`） | **500** | **情况 B（原版既有）** |
| 6 | `POST .../report/create` `person:"not-a-list"` | 200 `json_fail(...)`（`count("string")` 在 PHP 7.x 为 warning→`ErrorException`→被 `store()` 捕获，`Api/PersonController.php:16-62`） | **500** | **回归**（备注：仅 PHP 8 下原版会因 `TypeError` 同为 500） |
| 7 | `POST .../report/create` `person:["str"]` | 200 `json_fail(...)`（`"str"['card']` 为 warning→`ErrorException`→被捕获；PHP 8 下同样是 warning 路径） | **500** | **回归** |
| 8 | `PUT /api/admin/report/check` `status:"pass"` | 200 `json_fail('审核失败!')`（`Api/Admin/ReportController.php:39-55`，`QueryException` 被捕获） | **500**（`update(status="pass")`→ValueError，[views.py:440-447](apps/api/views.py#L440-L447)） | **回归** |
| 9 | `POST /api/ticket/make` `ticket_id:"abc"` | 200 `json_fail('预约失败！')`（`$data['card']` 未定义→`ErrorException`；即便有 card 也走 `if(!$ticket) json_fail('票不存在！')`） | **500**（[views.py:841](apps/api/views.py#L841)） | **回归** |
| 10 | `GET /api/admin/report/list?status=0 OR 1=1` | 200 空结果集（`where('status',$status)` 为绑定参数，非拼接 SQL；`Api/Admin/ReportController.php:25-27`） | **500**（`filter(status="0 OR 1=1")`→ValueError，[views.py:83-84](apps/api/views.py#L83-L84)） | **回归** |
| 11 | `POST /api/admin/user/` 重复 `username` | 200 `json_fail('创建失败！')`（无唯一性预校验，靠 DB 约束，`QueryException` 被 `catch` 捕获） | **500**（[views.py:488-496](apps/api/views.py#L488-L496)） | **回归** |
| 12 | `PUT /api/admin/person` `card` 撞唯一 | 200 `json_fail('修改失败！')`（`Api/Admin/PersonController.php:27-44`） | **500**（[views.py:570-580](apps/api/views.py#L570-L580)） | **回归** |
| 13 | `POST /api/admin/user/` 空 body（重复调用） | 第一次即 200 `json_fail('创建失败！')`（原版 `username` 列 NOT NULL 且无默认值，插入即失败并被捕获） | 第一次**成功**创建脏账号，第二次 **500** | **回归**（并叠加 R-3） |

**12 项回归 / 1 项情况 B**。

### R-2 报表列表由「eager loading」退化为 N+1

| 项 | 内容 |
| --- | --- |
| **Laravel 原版** | 四个列表端点全部使用嵌套预加载：`->with(['person','person.personInfo','file','spectrum','user'])`（`Api/Admin/ReportController.php:29-31`、`Api/School/ReportController.php:29-31`、`Api/City/ReportController.php:29-31`、`Api/Province/ReportController.php:29-31`）。方法体内**没有任何逐行循环**，直接 `return json_page($res, $count);`。整请求 SQL 数**恒为 7**（1×`count()` + 1×主查询 + 5 条关联预加载），与页大小无关。 |
| **Django 当前版** | `report_page()` 用 `report_dict()` 逐行序列化（[services.py:199-215](apps/core/services.py#L199-L215)）：每行 1 次查 `user`、1 次查 `ReportPerson`、每个 link 再 1 次查 `Person`、2 次查 `Files`。实测 `/api/admin/report/list?limit=10`（10 报表 × 3 人）= **76 条 SQL**。 |
| **差异** | 同一端点、同一页大小：原版 7 条 SQL（常数），Django 76 条（随行数线性增长）。**这是重构引入的性能退化**，且原版已经把预加载写好了，退化发生在移植时把 `with()` 换成了逐行查询。 |
| **影响面** | `/api/admin/report/list`、`/api/school/report/list`、`/api/city/report/list`、`/api/province/report/list`（四个端点共用 `report_dict`）。 |
| **对照佐证** | 同一 Django 代码库的导出路径并未退化 —— `admin/export/data2`（50 报表 × 2 人）仅 9 条 SQL，说明批量能力存在，只是列表路径没用上。 |
| **严重程度** | **中** |

### R-3 `User.username` 被 Django 凭空赋予默认值 `" "`

| 项 | 内容 |
| --- | --- |
| **Laravel 原版** | `$table->string('username',30)->comment('用户名')->unique();`（`database/migrations/2014_10_12_000000_create_users_table.php:18`）—— **NOT NULL、无默认值**、唯一。`database/` 与 `app/` 全树搜索 `username` 默认 `" "` → NOT FOUND；console 命令一律显式赋值（`ImportUser.php:46` `'superadmin'`、`CreateZwhUser.php:46` `'shzh'.($i+1)`）。创建接口无校验（`Api/Admin/UserController.php:54-72`），空 body 会因 NOT NULL 触发 `QueryException` → 被 catch → 200 `json_fail('创建失败！')`。 |
| **Django 当前版** | `username = models.CharField(max_length=30, unique=True, default=" ")`（[models.py:82](apps/core/models.py#L82)），首列迁移亦为 `CharField(max_length=30, unique=True)`（`0001_initial.py:16`，模型层 `default=" "` 未落库为 DB 默认）。空 body 创建时 `username` 落到 `" "` → **第一次调用成功落库**一个 `username=" "` 的脏账号，第二次撞唯一约束 500。 |
| **差异** | 同为空 body 创建用户：原版**拒绝**（业务错误），Django**静默产生脏账号**。Django 新增了一个原版不存在的默认值语义。 |
| **严重程度** | **中** |

### R-4 `/api/scan/list` 返回的 `scanfile` 未按 `type` 过滤

| 项 | 内容 |
| --- | --- |
| **Laravel 原版** | `$type = \request('type') ?? 0;`（`Api/ScanFilesController.php:21`）→ 不传 `type` 时 `$type = 0`；预加载始终带条件 `->with(['scanfile' => function ($query) use ($type) { $query->where('type', $type); }])`（`:31-33`）。即：**不传 `type` 只返回 `type=0` 的扫描件；传 `type=N` 只返回 `type=N`**。 |
| **Django 当前版** | `d["scanfile"] = [model_dict(x) for x in ScanFiles.objects.filter(user_id=u.id)]`（[views.py:344](apps/api/views.py#L344)）—— **完全不带 `type` 条件**，返回该用户全部扫描件。 |
| **差异** | 同一个用户、同一个请求，`scanfile` 数组内容不同：凡该用户存在多种 `type` 的扫描件，Django 返回的行数多于原版。Django 仅在 `type` 参数存在时才用它筛**用户**（`:339-342`），但用于筛**扫描件**的 `type` 条件丢失。 |
| **严重程度** | **低** |

### R-5 未加括号的 OR 链被改写为显式 AND 组合

| 项 | 内容 |
| --- | --- |
| **Laravel 原版** | 两处使用未分组的 `orWhere`：<br>① `Api/Admin/ReportController.php:21-23`：`where('name','like','%kw%')->orWhere('choir_name','like','%kw%')`，随后再 `.where('group',$group)`、`.where('status',$status)` → SQL 形如 `name LIKE %kw% OR (choir_name LIKE %kw% AND group=? AND status=?)`。<br>② `Api/ScanFilesController.php:22-25`：`User::where('type',0)->OrWhere('type',1)->OrWhere('type',4)` 之后再 `.where('nickname','like','%kw%')` → `type=0 OR type=1 OR (type=4 AND nickname LIKE ...)`。 |
| **Django 当前版** | 用显式分组后 AND：`qs.filter(Q(name__icontains=kw) | Q(choir_name__icontains=kw))` 再 AND `group`/`status`（[views.py:92-99](apps/api/views.py#L92-L99)）；`scan_list` 用 `User.objects.filter(type__in=[0,1,4])` 再 AND `keyword`（[views.py:331-338](apps/api/views.py#L331-L338)）。 |
| **差异** | `keyword` 与非空 `group`/`status`（或 `type`）同时出现时，**结果集不同**：原版的 `OR` 使 `type=0`/`name LIKE` 那一支不受其余条件约束，Django 则对整体做 AND。 |
| **对照佐证（无差异处）** | `school`/`city`/`province` 三个列表的 keyword 只查 `name` 单字段、无 `orWhere`（`Api/School/ReportController.php:22-24` 等三处一致），与 Django 的 `qs.filter(name__icontains=keyword)` 一致，**不构成回归**。 |
| **严重程度** | **低** |

---

## 3. 原版既有问题

以下均为上一轮报告中发现、但经对照确认 **Laravel 原版同样如此** 的行为（情况 B），**不计入 Django 回归**。

| 编号 | 上一轮判定 | Laravel 原版实际行为 | 结论 |
| --- | --- | --- | --- |
| P1-1 | 报名表创建时 `status` 可被客户端指定 | `$data = $request->all();` → `(new Report)->create($data)`，而 `status` **在 `$fillable` 中**（`app/Models/Report.php:12`，含 `'status'`）；三个 create 方法**都不强制、不剔除** `status`（`Api/School/ReportController.php:47-103`、`Api/City/ReportController.php:47-107`、`Api/Province/ReportController.php:47-89`）。DB 列默认 `0`（`database/migrations/2020_09_22_033042_create_report_table.php:34`）。 | **原版既有行为，非回归** |
| P1-3 | `/api/file/list` 可读取他人文件 | `Files::whereIn('id', $ids)->get();`（`Api/FileController.php:43`）—— **无 `user_id` 条件**，无归属校验；路由仅 `api` + `auth:sanctum`（`routes/api.php:23`），无角色中间件。 | **原版既有行为，非回归** |
| P1-4 | `/api/ticket/my` 未认证返回 PII | `ticket` 组（`routes/api.php:230-235`）位于 `auth:sanctum` 组（`:23` 开、`:228` 闭）**之外**，且自身 `Route::group` 数组**无 `middleware` 键**。`getMyTicket`（`Api/TicketController.php:47-61`）不调用任何 `auth()`/`user()`，按 `name`+`card` 查询，返回完整 `TicketSubscribe`（该模型**无 `$hidden`**，序列化含 `phone`/`ip`/`code`）。 | **原版既有行为，非回归** |
| P2-5 | `/api/scan/list` 无角色门禁 | `ScanFilesController@getList`（`Api/ScanFilesController.php:18-39`）路由同样只挂 `api`+`auth:sanctum`；查询 `User::where('type',0)->OrWhere('type',1)->OrWhere('type',4)`，**不按请求者过滤**，返回 `nickname`/`tel`/`leader`/`description` 等。五个角色中间件（`Kernel.php:68-72`）均未挂到该路由。 | **原版既有行为，非回归** |
| P2-6 | 导出无范围限制 | `Api/ExportController.php:116-123`（`export/data`）、`Api/Admin/ExportController.php:19-21`（`data1`）、`:125-127`（`data2`）**均无** `limit`/`take`/分页/时间窗，一律全表 `->get()`。 | **原版既有行为，非回归** |
| P2-7 | 无外键约束 | `database/migrations/` 共 16 个建表文件，搜索 `foreign`/`foreignId`/`constrained`/`references`/`unsignedBigInteger` → **0 命中**。全库仅 3 个唯一约束：`users.username`、`personal_access_tokens.token`、`person.card`。`report.user_id` 为普通 `integer` + `index`（`2020_09_22_033042_create_report_table.php:18,38`）。 | **原版既有设计，非回归** |
| P2-8 | `ticket_subscribe` 无 `(ticket_id, card)` 唯一约束 | 建表脚本 `2024_07_15_085909_create_ticket_subscribe_table.php:16-25` 只有 `id`/`ticket_id`/`name`/`card`/`phone`/`ip`/`code`/timestamps，**无任何 unique**。原版同样只依赖应用层 `lockForUpdate()` + `first()` 去重（`Api/TicketController.php:74-78`）。 | **原版既有行为，非回归** |
| P2-3 | Excel 公式注入（应用层） | `DataExport::array()` 原样返回入参（`app/Export/DataExport.php:16-19`）；调用方把 `choir_name`/`name`/`desc`/`school_name`/`contact_*`/`nickname`/人员姓名等**原样**塞入数组（`Api/ExportController.php:197-217`、`Api/Admin/ExportController.php:93-113,209-245`）。全树搜索 `WithCustomValueBinder`/`setCellValueExplicit`/`ValueBinder` → **NOT FOUND**。唯一清洗是 `data2` 中身份证号加 `'` 前缀（`Api/Admin/ExportController.php:194,202,206`），Django 亦保留该行为。 | **原版既有行为，非回归**（框架层见 §5） |
| P2-2 | `TOKEN_TTL_HOURS` 配置不生效 | 原版 `config/sanctum.php:29` `'expiration' => null`（不按时间过期），且 `createToken('art')` 不传过期参数（`Api/AuthController.php:55-57`）；真正起作用的是 `TokenDestroy` **硬编码 4 小时**（`app/Http/Middleware/TokenDestroy.php:17-22`，注册于 `Kernel.php:46`）。Django 中间件同样硬编码 4h（[middleware.py:19](apps/core/middleware.py#L19)）→ **与原版一致**。 | **4h 语义非回归**；`settings.TOKEN_TTL_HOURS` 默认值亦为 4（[settings.py:139](django_config/settings.py#L139)），出厂行为相同。配置项与另一处校验点（[models.py:163](apps/core/models.py#L163)）不同步，属 **Django 自身新增配置项未完全接线**，不是相对原版的行为回归。 |

---

## 4. 已确认符合原版

经本轮对照（或上一轮已定案）确认与原版一致的项，**均不构成回归**：

| 项 | Laravel 原版 | Django 当前版 |
| --- | --- | --- |
| `/api/export/data` 全平台导出、普通学校账号可访问 | `export` 组（`routes/api.php:58-62`）只挂 `api`+`auth:sanctum`，查询仅可选 `group`（`Api/ExportController.php:115-123`） | 同样 `auth=auth` + `Report.objects.all()`（[views.py:382-388](apps/api/views.py#L382-L388)） |
| 报名表**更新**强制 `status = 0` | `$data['status'] = 0;`（`Api/School/ReportController.php:128`、`Api/City/ReportController.php:132`、`Api/Province/ReportController.php:109`） | `report.status = 0`（[views.py:161](apps/api/views.py#L161)） |
| 报送上限 8 条（仅 province） | `if ($exist_count >= 8) return json_fail('目前您的单位已超报送限制,无法再继续进行报送!');`（`Api/Province/ReportController.php:53-56`） | `if province and Report.objects.filter(user_id=user.id).count() >= 8`（[views.py:106-107](apps/api/views.py#L106-L107)） |
| 票务码生成规则 | `substr(date("YmdHis"),2,12) . mt_rand(100000,999999) . mt_rand(100,999)`（`Api/TicketController.php:133`） | `strftime("%y%m%d%H%M%S") + 6位 + 3位`（[services.py:227-233](apps/core/services.py#L227-L233)） |
| 票务开放时间闸门 | `new DateTime('2024-11-14 20:00:00')`（`Api/TicketController.php:89,98-101`） | 同一硬编码时刻（[views.py:1004](apps/api/views.py#L1004)） |
| 角色门禁实现方式 | `AdminConfine`→`type===3`、`CommitteeConfine`→`2`、`CityConfine`→`1`、`ProvinceConfine`→`4`、`SchoolConfine`→`0`（各文件 `:18`，注册于 `Kernel.php:68-72`），拒绝体统一为 `['error' => '无该页面操作权限！']` + 403 | `role_error(request, expected)` 同判定同响应体（[views.py:55-60](apps/api/views.py#L55-L60)） |
| 原版不做输入校验 | `app/` 全树 0 处 `$request->validate()`，无 `app/Http/Requests`，`Validator` 仅 import 未使用；模型 `$rules = []` 全空 | 同样无 schema 校验，按 Model 字段白名单透传 |
| 唯一约束集合 | `users.username`、`person.card`、`personal_access_tokens.token` | 三者一致（`0001_initial.py:16,65` 等） |

> 附注（上一轮已记录，不重复判定）：`/api/export/data` 对空串 `group=` 的处理，原版用 `!== null` 严格判断会附加 `where('group','')`，Django 用 truthy 判断会跳过。此为边界差异，与权限/数据范围无关。
> 另一个方向已被排除：原版 `admin/report/list` 等处用 `$status != null`（PHP 松散比较，`'' != null` 为 false），空串同样不过滤，故这些端点**没有**空串差异。

---

## 5. 无法确认

| 项 | 为何无法确认 | 需要什么条件 |
| --- | --- | --- |
| **P2-4 导出 `Content-Disposition` 文件名编码** | 原版 `app/` 侧**未设置任何下载头**，只把中文文件名传给 `Excel::download()`（`Api/ExportController.php:219`、`Api/Admin/ExportController.php:116,247`）；实际 header 由框架内部生成。原版仓库 **无 `vendor/`**（`.gitignore:5` 忽略，`git ls-files vendor` 为空，历史从未跟踪），无法读取框架源码，故无法确认原版是否使用 RFC5987 `filename*=`，也就无法判断 Django 的 RFC2047 编码（`=?utf-8?b?...?=`，[export_services.py:350](apps/api/export_services.py#L350)）相对原版是否构成浏览器兼容性回归。 | 补齐 `vendor/`（`composer.lock` 已锁定 `laravel/framework v7.30.6`、`maatwebsite/excel 3.1.48`、`symfony/http-foundation v5.4.24`），或对原版运行态抓包取真实响应头 |
| **P2-3 的框架层行为** | 应用层已确认双方均无清洗（见 §3 P2-3），但「`=` 开头的字符串是否被写成公式类型单元格」取决于 `phpoffice/phpspreadsheet 1.19.0`（经 `maatwebsite/excel 3.1.48`），该源码同样不在仓库内。 | 同上，需补齐 `vendor/` |

---

## 6. 最终结论

### 6.1 真正的 Django 回归：**5 项**

| 编号 | 回归内容 | 严重程度 |
| --- | --- | --- |
| **R-1** | 原版 controller 的 `try/catch (\Exception) → json_fail()` 兜底契约未被迁移，Django 无任何全局异常处理；13 项畸形输入用例中 **12 项**由原版的 `200 + {"code":1}` 变为 **500** | **高** |
| **R-2** | 报表列表由原版的 `with([...])` 嵌套预加载（恒定 7 条 SQL）退化为 `report_dict()` 逐行查询（实测 76 条 / 10 报表），影响 admin/school/city/province 四个列表端点 | **中** |
| **R-3** | `User.username` 被 Django 赋予原版不存在的默认值 `" "`：原版空 body 创建会被 DB 拒绝并返回业务错误，Django 静默产生 `username=" "` 的脏账号 | **中** |
| **R-4** | `/api/scan/list` 返回的 `scanfile` 丢失 `type` 过滤（原版默认只返回 `type=0`，Django 返回全部） | **低** |
| **R-5** | 原版未加括号的 `orWhere` 链被改写为显式 AND 组合，`keyword` 与 `group`/`status`/`type` 同时出现时结果集不同（`/api/admin/report/list`、`/api/scan/list`） | **低** |

其中 **R-1 是本轮最重要的发现**：它不是一个端点的瑕疵，而是原版「失败也走业务响应契约」的全局设计在移植时整体丢失。R-2 是明确的性能退化，且原版已经写好了预加载。R-3 是 Django 主动新增了原版没有的默认值语义。

### 6.2 只是原版既有行为：**9 项**（不计入回归）

`/api/export/data` 全平台导出（上轮已定案）、报名表创建时 `status` 可被客户端指定（P1-1）、`/api/file/list` 可读他人文件（P1-3）、`/api/ticket/my` 未认证返回 PII（P1-4）、`/api/scan/list` 无角色门禁（P2-5）、导出无范围限制（P2-6）、无外键约束（P2-7）、`ticket_subscribe` 无唯一约束（P2-8）、Excel 应用层无清洗（P2-3）。

这 9 项在原版 Laravel 中**同样存在**，Django 属忠实迁移，按本轮判定标准**不予修复**。

### 6.3 暂时无法确认：**2 项**

`Content-Disposition` 文件名编码（P2-4）、Excel 公式注入的框架层归因（P2-3 框架侧）。二者都卡在同一个客观障碍上：**原版仓库未随附 `vendor/`，框架源码不可读**。在补齐 `vendor/`（或原版运行态抓包）之前，不得猜测。

### 6.4 结论边界

- 本报告的原版依据为 `C:\Users\34716\OneDrive\Desktop\contest\ylb_intial\ylb_api` 的**实际源码**（路由、控制器、模型、迁移、`app/Exceptions/Handler.php`、`config/sanctum.php`），非文档转述 —— 这修正了上一轮报告「本地无 Laravel 源码」的局限。
- Django 侧的畸形输入状态码来自上一轮的实测，本轮未重新执行测试；Django 侧代码位置均为本轮实际阅读确认。
- 原版的错误响应行为是在 **MySQL + strict 模式 + PHP 7.x** 前提下的推断路径（如 `QueryException` 的具体触发）；行为差异的**方向**（原版 200/code:1 vs Django 500）由 `try/catch` 结构与 `json_fail()` 的返回码直接决定，不依赖具体数据库。第 6 号用例在 PHP 8 下原版亦为 500，已在 §2 表中标注。
- 本轮**未修改任何文件**，唯一新增为 `DJANGO_REGRESSION_AUDIT.md` 与上一轮的 `EXPORT_DATA_ORIGINAL_VS_DJANGO.md`。
