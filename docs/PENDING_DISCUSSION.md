# 待讨论归档（PENDING DISCUSSION）

> 2026-09-16 完整度审查（对照 `ylb/ylb_api/ylb_api`）产出的未决事项。
> 每项按模板记录：**状态 / 背景事实 / 待确认问题 / 建议行动 / 不行动的风险**。
> 本目录之外的缺口状态仍以根目录 `MIGRATION_GAPS.md` 为准，本文档承载"需要人拍板/需要外部材料"的议题。

---

## §1 幽灵列与未迁移表（需要生产库 schema）

- **状态：** 待外部材料（运维提供真实列清单）
- **背景事实：** 旧代码运行时引用了 `report.origin`、`report.territory`、`report.group_type`、`report_person.table_id`，但检入的 16 个 Laravel 迁移文件中都没有这些列（旧项目有人绕过迁移流程直接改生产库）。`students`、`statistics` 两表同理：模型存在、迁移文件缺失。Django 侧模型因此没有这些字段；当前以 `getattr`/`hasattr` 守卫静默跳过，**不崩溃但数据恒为空/0%**（如 `city/index/percent` 的中学组占比、province 详情 origin/territory 映射）。
- **待确认问题：** 生产库中这些列是否存在？物理类型是什么？
- **建议行动：** 请运维执行并把结果回传：
  ```sql
  SELECT table_name, column_name, data_type, character_maximum_length, is_nullable
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name IN ('report','report_person','students','statistics')
  ORDER BY 1,2;
  ```
  拿到清单后补 Django 模型字段并接回真实逻辑。**双轨迁移注意**：对已含物理列的旧库不能直接 `AddField`（会 ALTER 撞 duplicate column），需用 `SeparateDatabaseAndState`（state 侧 AddField、operations 侧为空）；SQLite/新装环境则用常规 AddField。两轨不可混发，逐条 review——这是受控流程，区别于 DEPLOYMENT.md 禁止的"未经 review 的 `--fake`"。
- **不行动的风险：** 挂旧库后统计类端点返回恒 0 比例、province 报表详情缺字段，前端页面数据错误但无报错，难被察觉。

## §2 导出文件视觉保真（需要旧系统 golden-file）

- **状态：** 待外部材料 + 待批准的大改动
- **背景事实：** Laravel 端 PDF 走 blade 模板渲染、Excel 走 maatwebsite/excel（多 sheet、合并单元格）；Django 端为 reportlab 单行截断手绘 + openpyxl 单 sheet。本次已修复**中文可渲染**（STSong-Light），但排版结构与旧文件不一致。已知残留：① STSong-Light 是**非嵌入字体**，阅读器无 Adobe-GB1/CJK 支持时中文仍显示空白（Chrome/Adobe Reader/多数国产阅读器可用）；② 9pt 下 `[:180]` 字符截断可能溢出一页宽度（CJK 字符更宽）。
- **待确认问题：** 验收标准是"结构级一致"（列、sheet、表头、行序）还是"像素级一致"？能否从旧系统各导出一份真实文件作为 golden-file？
- **建议行动：** 拿到 golden-file 后，若要求结构对齐，按 Laravel 的 `Export/DBTExport、JiemuExport、ZuofangPersonExport、ChouQianExportSheet、AllChouQianExport` 等实现类逐列重写 `apps/api/export_services.py` 与 PDF 层（含嵌入字体选项）。工作量以百行计，需单独排期。
- **不行动的风险：** 政府侧经办人拿到格式变化的报表，可能被要求返工；PDF 在无 CJK 字体的环境打开为空白页。

## §3 前端契约错位（重大——可能动摇迁移基线）

- **状态：** 待人工确认（**最高优先级**）
- **背景事实：** 检入的前端产物 `resources/dist/`（Vue CLI 构建，时间戳 2022-06-28，axios host=`//bigapp.scbdc.edu.cn/ylbxt`）与检入的 Laravel 源码对不上：
  | 前端引用 | 检入的 routes/api.php | 判断 |
  |---|---|---|
  | `/api/rules` | 无 | **死代码**（全 dist 仅定义处 1 次出现、0 调用），可不处理 |
  | `/api/v2/*`（约 15 条：admin/committee/school 的 report、team、student、leader 全套） | **完全不存在** | 前端是对着**另一版（更新的）后端**写的 |
  | `POST /api/files/` | 只有 `/api/file/create` | 被约 28 个 chunk 活跃引用，**疑似活功能** |
  | `/api/chouqian/school/*`、`/api/chouqian/jiemu/*` | 只有 `/api/admin/chouqian/*` | 前端路径缺 `admin` 段，对不上 |
  另外：`public/.htaccess` 为空文件、实际重写依赖 `web.config`(IIS) 或仓库外的服务器配置；`/ylbxt/` 子路径→应用的映射发生在检入代码之外。
- **待确认问题：** **`/ylbxt/` 生产环境实际运行的后端，是否就是这份检入的 Laravel 源码？** 需要生产服务器文件清单 / 部署历史 / git hash 佐证。
- **建议行动：** 先取证再动工。**在证据到手前，不投入任何 `/api/v2/*`、`/api/files/` 兼容垫片开发。** 若确认检入源码≠生产版本：本仓库全部"78/78 路由核对通过"的结论仅对该快照成立，迁移基线需以生产源码重审。
- **不行动的风险：** 切换当天前端调用 v2/files 接口集体 404——而这无法通过现有代码审查发现，属于验收盲区。

## §4 Sanctum 过期策略差异（需要业务拍板）

- **状态：** 待业务确认
- **背景事实：** 旧项目 `config/sanctum.php` 的 `expiration = null`——**token 在代码层面永不过期**（旧系统另有 TokenDestroy 中间件按 `last_used_at` 清理，实际生产是否生效未确认）。Django 侧 `TOKEN_TTL_HOURS` 默认 4 小时滑动过期 + `TokenCleanupMiddleware` 物理删除，是**行为收紧**。注意：`MIGRATION_GAPS.md` 与早期记录曾表述为"与旧一致"，本条予以纠偏——两者默认配置下并不一致。
- **待确认问题：** 4 小时过期是有意安全加固，还是应放开为"不过期"以完全兼容旧习惯？
- **建议行动：** 保持现状（TTL 可经 `.env` 配置，设大值即近似永不过期），业务确认后关闭本条。
- **不行动的风险：** 用户被更频繁登出，投诉指向"迁移后变严了"。

## §5 本次已修记录（2026-09-16，无需讨论，留痕备查）

1. **存量用户 bcrypt 登录兼容**——根因一句话：Django 3.2 `check_password` 对 `$2y$` 串解析出空算法名→ValueError 被吞→静默 False，且该格式**无法**通过 PASSWORD_HASHERS 注册自定义 hasher 解决（永远路由不到）。方案：`apps/core/services.py::verify_user_password` 用 `bcrypt` 包校验（`$2y$`→`$2b$` 归一化），成功后透明重刷为 PBKDF2。测试见 `tests/test_auth_permissions.py::LegacyBcryptLoginTests`。决策记录：不做密码错误路径的时序补偿（登录响应本身已通过不同文案泄露用户存在性，为有意保留的 Laravel 契约，时间加固是自欺）。
2. **PDF 导出中文字体**——`views.py::pdf_response` 注册并启用 `STSong-Light`（含 showPage 后重设字体）。视觉保真议题仍在 §2。
