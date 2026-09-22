# /api/export/data 原版 vs Django 对照审计

审计范围：仅 `GET /api/export/data`。不修改任何代码/数据库/迁移/测试/配置，不提出修复方案。
原版路径：`C:\Users\34716\OneDrive\Desktop\contest\ylb_intial\ylb_api`
当前路径：`e:\github\yilinbei`

---

## 1. Laravel 原版

### 1.1 调用链总览

```
routes/api.php:23   Route::group(['middleware' => ['api','auth:sanctum'], 'namespace' => 'Api'])
routes/api.php:58   └─ Route::group(['prefix' => 'export'])
routes/api.php:61      └─ Route::get('/data', 'ExportController@exportReportData')
                            ↓ namespace = Api
app/Http/Controllers/Api/ExportController.php:115  exportReportData()
                            ↓
app/Models/Report.php                              Report（SoftDeletes）
                            ↓
app/Export/DataExport.php:7-19                     Excel 导出（纯透传）
```

### 1.2 Route

`routes/api.php:58-62`

```php
Route::group(['prefix' => 'export'], function () {
    Route::get('/report', 'ExportController@exportReport')->name('api.export.report');
    Route::get('/person', 'ExportController@exportReportPerson')->name('api.export.person');
    Route::get('/data',   'ExportController@exportReportData')->name('api.export.data');   // ← 本接口
});
```

该 `export` 组**没有自己的 `middleware` 键**，因此完全继承外层 `routes/api.php:23` 的分组：

```php
Route::group(['middleware' => ['api', 'auth:sanctum'], 'namespace' => 'Api'], function () {
```

同名控制器还有一份在 `Api\Admin\ExportController`，但它只被 `routes/api.php:106-107` 的
`/admin/export/data1`、`/admin/export/data2` 使用，与本接口无关。

### 1.3 Middleware / Auth

实际生效的中间件只有两组：

| 来源 | 中间件 | 作用 |
| --- | --- | --- |
| `app/Http/Kernel.php:42-47`（`api` 组） | `throttle:1000,1`、`SubstituteBindings`、`EnsureFrontendRequestsAreStateful`、`TokenDestroy` | 限流、路由绑定、Sanctum 有状态判定、清理 token |
| 路由声明 `auth:sanctum` | Sanctum 守卫 | **仅校验是否已登录** |

逐项核对，**不存在任何角色/身份判断**：

- `app/Http/Middleware/TokenDestroy.php:19-20` —— 只做 `personal_access_tokens` 中 `last_used_at <= -4 hour` 的删除，无权限逻辑。
- `app/Http/Middleware/Authenticate.php:7-20` —— 仅继承 Laravel 原生 `Illuminate\Auth\Middleware\Authenticate`，只重写了 `redirectTo()`，无角色逻辑。
- `app/Http/Kernel.php:57-73` 注册的角色门禁中间件（`admin`→`AdminConfine`、`committee`→`CommitteeConfine`、`city`→`CityConfine`、`province`→`ProvinceConfine`、`school`→`SchoolConfine`）**均未挂在 `export` 组或该路由上**。

对照可见这些门禁确实存在且确实按 `type` 判定，只是没被使用在 `/export/data`：

- `app/Http/Middleware/AdminConfine.php:18` → `$request->user()->type === 3`
- `app/Http/Middleware/CommitteeConfine.php:18` → `$request->user()->type === 2`
- `app/Http/Middleware/SchoolConfine.php:18` → `$request->user()->type === 0`

作为反证：同一个 `Kernel.php` 里注册的角色中间件，在管理端导出上是被真实使用的 ——
`routes/api.php:66` 的 `['middleware' => 'admin', 'prefix' => 'admin']` 包住了
`routes/api.php:105-108` 的 `/admin/export/data1|data2`。也就是说作者知道怎么做角色门禁，
并且对 `/admin/export/*` 做了，**但对 `/export/data` 没有做**。

### 1.4 Controller 与数据查询

`app/Http/Controllers/Api/ExportController.php:115-123`（`exportReportData()` 起手）：

```php
public function exportReportData() {
    $group = \request()->get('group') ?? null;
    $model = new Report();
    if ($group !== null) {
        $model = $model->where('group', $group);
    }
    $result = $model->with(['person', 'person.personInfo', 'user', 'file1', 'spectrum1'])
        ->orderBy('id', 'ASC')
        ->get();
```

关键事实：

- **查询条件只有 `group`（且仅在显式传入时）**。没有 `user_id`、没有 `school_id`、没有 `type`、没有 `status`。
- **没有调用 `request()->user()`**。整个方法体内不出现当前登录者身份 —— 与同文件另外两个方法形成鲜明对比。
- 全表 `->get()`，即等价于 `Report::all()`。唯一隐式收窄来自 `app/Models/Report.php:5,9` 的 `SoftDeletes`（排除软删除行）。
- `app/Models/Report.php` 中**没有 `booted()`、没有 `addGlobalScope()`**（`grep` 仅命中 `$fillable` 与 `SoftDeletes`），不存在模型层全局过滤。
- `app/Export/DataExport.php:7-19` 实现 `FromArray`，`array()` 直接返回构造入参，无二次过滤。

### 1.5 同文件内对照组（说明"不按 user_id 过滤"不是路由层意外）

| 方法 | 行号 | 查询条件 |
| --- | --- | --- |
| `exportReport()` | `ExportController.php:16-17` | `where('user_id', $user->id)->where('status', '>=', 0)` ✅ 按本人过滤 |
| `exportReportPerson()` | `ExportController.php:84-85` | `where('user_id', $user->id)->where('status', '>=', 0)` ✅ 按本人过滤 |
| `exportReportData()` | `ExportController.php:115-123` | 仅可选 `group` ❌ 不做任何身份过滤 |

三个方法在同一个类、同一路由组、同一中间件下。前两个主动取了 `request()->user()` 并绑定 `user_id`，
第三个**没有取 user 也不绑定**。所以这是同一个作者在同一上下文里的**有意区别对待**，不是路由挂载失误。

### 1.6 普通学校账号（type = 0）在原版的实际行为

- **能否访问**：能。只要持有有效 Sanctum token 即可通过 `auth:sanctum`；`export` 组无 `school`/`admin`/`committee` 任何角色门禁，`type=0` 不会被拦截。
- **数据范围**：**全平台报名数据（B）**。查询为全表扫描，`type=0` 与 `type=3` 得到的行集完全相同。
- **过滤行为**：不按 `school_id` 过滤、不按 `user_id` 过滤、不按角色过滤。

### 1.7 导出字段

`ExportController.php:124-144` 表头 19 列；`:197-217` 逐行 19 列：

所属单位(`user->nickname`)、乐团名称(`choir_name`)、自选曲目名称(`name`)、指定曲目名称(`name1`)、
参报代码(`code`)、参展学校名称(`school_name`)、描述(`desc`)、组别(`group`)、领队姓名(`contact_name`)、
领队电话(`contact_phone`)、联系地址或邮箱(`contact_way`)、节目时长(`time_length`)、集体照(`spectrum`)、
视频文件(`file`)、状态(`getStatus`)、正式队员(`person0`)、预备队员(`person1`)、指挥(`person2`)、指导教师(`person4`)。

输出文件名 `数据导出.xlsx`（`:219`）。

---

## 2. Django 当前版

### 2.1 调用链总览

```
django_config/urls.py:7                        path("api/", api.urls)
apps/api/views.py:382-388                      @api.get("/export/data", auth=auth)
apps/api/views.py:61                           auth = BearerAuth()
apps/api/export_services.py:354-355            reports_export_response(qs, "数据导出.xlsx")
apps/api/export_services.py:146-177            report_data_rows()
apps/core/models.py:194                        Report.objects = SoftDeleteManager()
```

### 2.2 URL / View / Auth

`apps/api/views.py:382-388`：

```python
@api.get("/export/data", auth=auth)
def export_data(request):
    qs = Report.objects.all().order_by("id")
    if request.GET.get("group"):
        qs = qs.filter(group=request.GET["group"])
    write_log(request.auth, 6, "导出报送数据")
    return reports_export_response(qs, "数据导出.xlsx")
```

- 路由注册：`django_config/urls.py:7` → `path("api/", api.urls)`，Ninja 内路径 `/export/data` ⇒ 实际 `GET /api/export/data`。
- 鉴权：仅 `auth=auth`（`apps/api/views.py:61` 的 `BearerAuth()`），对应原版 `auth:sanctum`。
- **无 `role_error` 调用**。`role_error`（`apps/api/views.py:55-60`）是本项目现成的角色门禁工具，形如：

  ```python
  def role_error(request, expected):
      user = request.auth
      if not user or user.type != expected:
          return response({"error": "无该页面操作权限！"}, 403)
      return None
  ```

  本接口没有使用它，也没有任何等价的 `request.auth.type` 判断。

### 2.3 QuerySet

- 实际查询即 `Report.objects.all()` —— 与审计假设一致。
- `Report.objects`（`apps/core/models.py:194`）= `SoftDeleteManager`，等价于 Laravel 的 `SoftDeletes`，隐式排除已删除行。
- 无 `user_id=request.auth.id`、无 `school_id`、无 `type`/角色过滤、无 `status` 过滤。
- 唯一的可选过滤条件 `group`，与原版 `group` 参数语义对应。
- 全接口仅在 `write_log(request.auth, 6, ...)` 处读取了当前身份，**该调用只写日志，不参与数据筛选**。

### 2.4 同文件内对照组

| 方法 | 行号 | 查询条件 |
| --- | --- | --- |
| `export_report` | `apps/api/views.py:361-363` | `Report.objects.filter(user_id=request.auth.id, status__gte=0)` ✅ 按本人过滤 |
| `export_person` | `apps/api/views.py:367-374` | `Report.objects.filter(user_id=request.auth.id, status__gte=0)` ✅ 按本人过滤 |
| `export_data` | `apps/api/views.py:382-386` | `Report.objects.all()` ❌ 不做身份过滤 |

| 方法 | 行号 | 角色门禁 |
| --- | --- | --- |
| `admin_export_data1` | `apps/api/views.py:620-627` | `role_error(request, 3)` ✅ |
| `admin_export_data2` | `apps/api/views.py:629-635` | `role_error(request, 3)` ✅ |

即：Django 侧同样具备"按 user_id 过滤"（两个 PDF 导出）与"按角色门禁"（管理端两个导出）两种现成手段，
但 `/export/data` 与 Laravel 原版一样，两者都**没有**用。

### 2.5 普通学校账号（type = 0）的实测行为

使用外部审计套件（`E:\github\ylb_audit\`，未写入仓库）以 `type=0` 账号请求 `GET /api/export/data`：

- HTTP 状态：**200**
- 响应：xlsx，内容同时包含 `['本校节目', '他校节目']` —— 即**全平台数据**。

结论与代码一致：普通学校账号可以导出全平台报名数据。

### 2.6 导出字段

`apps/api/export_services.py:34-38` `REPORT_DATA_HEADINGS` 共 19 列，
`apps/api/export_services.py:146-177` `report_data_rows()` 逐行 19 列，
人员列取 `_joined(members, 0/1/2/4)`（正式队员/预备队员/指挥/指导教师，跳过 position 3）。
输出文件名 `数据导出.xlsx`（`apps/api/export_services.py:354`）。

---

## 3. 行为对照

| 对照项 | Laravel 原版 | Django 当前版 | 是否一致 |
| --- | --- | --- | --- |
| 路由位置 | `routes/api.php:61` | `django_config/urls.py:7` → `apps/api/views.py:382` | ✅ |
| 鉴权方式 | `auth:sanctum`（`routes/api.php:23`） | `auth=auth` / `BearerAuth`（`views.py:382,61`） | ✅ |
| 角色门禁中间件 | 无（`export` 组无 `middleware` 键） | 无（未调用 `role_error`） | ✅ |
| 普通学校账号能否访问 | 能（仅需有效 token） | 能（实测 200） | ✅ |
| 数据范围 | **B：全平台报名数据** | **B：全平台报名数据** | ✅ |
| 是否按学校过滤 (`school_id`) | 否 | 否 | ✅ |
| 是否按用户过滤 (`user_id`) | 否 | 否 | ✅ |
| 是否有 status 过滤 | 否（含待审核/未通过） | 否 | ✅ |
| 隐式范围收窄 | `SoftDeletes`（`Report.php:5,9`） | `SoftDeleteManager`（`models.py:194`） | ✅ |
| 实际查询 | `(new Report())->...->get()` 全表 | `Report.objects.all()` | ✅ |
| 可选过滤参数 | `group`（仅传入时生效） | `group`（仅 truthy 时生效） | ✅ |
| 导出字段 | 19 列（`ExportController.php:124-144,197-217`） | 19 列（`export_services.py:34-38,146-177`） | ✅ 表头与顺序逐列一致 |
| 人员列取值 | position 0/1/2/4 | position 0/1/2/4 | ✅ |
| 输出文件名 | `数据导出.xlsx` | `数据导出.xlsx` | ✅ |

补充差异（不影响本问题结论，仅作事实记录）：

- 空字符串参数的边界处理略不同：原版 `\request()->get('group') ?? null` 在 `group=` 时得到 `''` 并会执行 `where('group','')`；Django `if request.GET.get("group"):` 对空串为假、不加条件。二者在"传空 group"这一边界上行为不同，但与权限/数据范围无关。
- 原版为 `Excel::download` + `FromArray`；Django 为 openpyxl 的 `workbook_response`。属实现栈差异。

---

## 4. 最终结论

### 情况 A —— 原版即可导出全平台数据，Django 可导出全平台数据

**"普通学校账号可以导出全平台报名数据"不是 Django 重构造成的。**

判定依据（全部来自原版代码本身，非推测）：

1. 原版该接口**只挂了 `auth:sanctum`**，`export` 路由组（`routes/api.php:58`）没有任何角色中间件，而 `type=0` 是可以通过 `auth:sanctum` 的 —— 原版普通学校账号本就能访问。
2. 原版该接口的查询（`app/Http/Controllers/Api/ExportController.php:115-123`）**只有可选的 `group` 条件，没有 `user_id` / `school_id` / 角色 / 状态条件**，且方法体内从未读取当前登录用户 —— 数据范围在全平台。
3. 这**不是遗漏而是有意区别对待**：同文件同路由组下的 `exportReport()`（`:16-17`）与 `exportReportPerson()`（`:84-85`）都显式绑定了 `where('user_id', $user->id)`；同项目的 `/admin/export/data1|data2` 也显式挂了 `admin` 角色中间件。原版作者掌握这两种收窄手段，却**未**施加于 `/export/data`。
4. Django 侧是**忠实迁移**：`auth=auth` 对应 `auth:sanctum`，`Report.objects.all()` 对应原版全表 `get()`，`group` 参数语义、19 列表头与顺序、人员 position 映射、`SoftDeleteManager` 对应 `SoftDeletes`、输出文件名，逐项一致；且同样未调用现成的 `role_error` 与 `user_id` 过滤。

因此，按本次审计采用的分类，结论为 **情况 A：原版既有业务行为，不属于 Django 重构回归**；亦不构成 **情况 B**（原版并非仅限本校）或 **情况 C**（原版不存在未被迁移的额外权限逻辑）。

本结论仅陈述"原版是什么行为、Django 是否忠实迁移"，**不评价该行为是否合理、不提出修复方案**。
