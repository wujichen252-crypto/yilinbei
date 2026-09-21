# GaussDB Migration Assessment

PostgreSQL(现网 Laravel 旧库)→ GaussDB(DWS 内核,PostgreSQL 9.2.4 基线)的数据迁移评估。
配合 [GAUSSDB_DEPLOYMENT.md](GAUSSDB_DEPLOYMENT.md)(部署步骤)与 [DATABASE_MAPPING.md](DATABASE_MAPPING.md)(表/列映射)使用。

评估日期:2026-09-21。验证状态:DWS 官方 FAQ 确认内核基于 PG 9.2.4;varchar 字节语义、空串行为、索引限制等参数细节来自华为文档知识整理,**未在真实实例上核对**,不同 8.1.x/8.2.x 版本的参数名与默认值有差异,落地前须由厂商按实例版本文档逐条确认。

## 结论摘要

- 表结构只使用基础类型(bigint/integer/double precision/varchar/text/date/timestamptz),**无 JSONB、数组、UUID、枚举、范围类型**——类型层面可平滑迁移。
- 两处会直接导致导入失败或丢数据:**varchar 按字节计长(中文 ×3)**、**`logs.content` 上的 btree 索引**;一处必踩坑:**导入后序列不同步**。
- 已完成的缓解:所有 NOT NULL 字符串字段 default 改为单空格(见下方"空串与 NULL")。

## 一、字段类型对照

物理类型以 `apps/core/migrations/0001_initial.py` 为准(旧库按 Laravel 迁移保留列名)。

| Django 字段 | PG 物理类型 | GaussDB(DWS) | 备注 |
| --- | --- | --- | --- |
| BigAutoField | bigint + sequence | 支持 | 导入显式 ID 后必须同步序列 |
| IntegerField / BigIntegerField | integer / bigint | 支持 | |
| FloatField | double precision | 支持 | `files.size` |
| CharField | varchar(n) | 支持 | **长度语义差异见风险 P0-1** |
| TextField / LegacyJSONField | text | 支持 | LegacyJSONField 用 text 存 JSON 字符串,刻意绕开 DWS 的 JSON/JSONB 差异 |
| DateTimeField(USE_TZ=True) | timestamptz | 支持 | Laravel 侧 `PRC` 时区语义需对齐验证 |
| DateField | date | 支持 | A 兼容模式下 date 可能带时间语义,验证 ORM 读回 |

## 二、风险清单

### P0-1 varchar(n) 按"字节"计长,不是"字符"

Laravel/PG 的 varchar(n) 按**字符**计数;GaussDB 系默认按**字节**计数(`nls_length_semantics=byte`;该参数较新版本才提供,且主要影响之后新建的表)。

UTF-8 下 1 个汉字占 3 字节,实际容量缩水为定义值的 1/3:

| 列 | 定义 | 按字节算的实际容量(汉字) |
| --- | --- | --- |
| users.nickname | varchar(255) | ≈ 85 |
| report.desc | varchar(1000) | ≈ 333 |
| logs.content | varchar(5000) | ≈ 1666 |

现有 PG 数据中超出字节上限的中文,导入时要么报 `value too long`,要么被 `td_compatible_truncation`(部分版本默认开启)**静默截断**——后者更危险,丢数据无报错。验证期间建议显式关闭该参数。

对策(二选一,先跑第四节的预检确认是否真的超限):

1. 厂商将实例 `nls_length_semantics` 设为 `char` 后建表;
2. 为 GaussDB 轨道单独放宽字段宽度(如 varchar(255) → varchar(765)),用 `SeparateDatabaseAndState`(state 侧改、operations 侧空)做成 state 侧变更,不影响 PostgreSQL 轨道迁移。两轨不可混发,逐条 review。

### P0-2 `logs.content` 上的 btree 索引

- PG 系 btree 索引行上限 ≈ 页大小的 1/3(8K 页约 2704 字节);varchar(5000) 的 UTF-8 中文最多 15000 字节,写入长内容时报 `index row size exceeds maximum`。
- 代码对 content 只用 `icontains`(`apps/api/views.py` 的日志搜索),前置通配符本就用不到 btree 索引。
- **对策:GaussDB 轨道建表时不创建该索引**(从迁移中拆出),不损失查询性能,躲开写入报错。

### P1-1 空串 '' ↔ NULL(A 兼容模式)

- A/ORA 兼容模式:'' 写入即存为 NULL、`'' IS NULL` 为真、`WHERE col = ''` 匹配不到任何行。PG/MySQL 兼容模式行为不同——**`GAUSSDB_COMPATIBILITY_MODE` 必须先从厂商确认,验证才能开始**。
- 已完成的缓解:全部 NOT NULL 字符串字段 default=" "(migration `0004_auto_20260921_1138`,仅 Python 侧默认值,对 PostgreSQL/GaussDB 无 DDL);`tokenable_type` 保留业务默认值 `"App\\Models\\User"` 不动。
- 代码核查结论:无 `filter(field="")`、无 `== ""` 判断、无原生 SQL/`.extra()`;`import_laravel_data.py` 用 `if row.get(key)` 跳过空值,不会写入 ''。可空字段写入 '' 变 NULL、读回 None,当前逻辑安全。
- 遗留注意:接口对 NOT NULL 字段现在返回 " "(单空格)而非 "",前端如有 `=== ""` 判断需排查。

### P1-2 序列不同步(必踩坑)

CSV 带显式 ID 导入后,所有 17 张表的 bigint 序列必须逐表 `setval`,否则第一条新 INSERT 即主键冲突。对应 runbook 第 4 步,注意是逐表、用厂商认可的方式。

### P2 验证项(不太会炸,但必须过一遍)

| 项 | 说明 |
| --- | --- |
| 保留字列名 | `report.group`、`draw.index` 及各表 `type/position/local/time/week/number` 等:ORM 自动加引号没问题;手写 SQL、BI 工具、导出脚本必须带双引号。DWS 保留字比 PG 多,以厂商清单为准 |
| 建库编码 | 必须 UTF-8,与源库一致,否则中文截断 |
| 分布式 DDL | DWS 为 MPP,建表涉及 `DISTRIBUTE BY` 分布键;vendor backend 通常会处理,小表行为/性能需过一遍 |
| 驱动与支持矩阵 | Django 3.2 官方支持 PG 9.4+,psycopg2-binary 2.9.9 对 9.2.4 内核不在官方验证范围。**必须用厂商验证过的 GaussDB backend/驱动**(`GAUSSDB_ENGINE` 切换已在 `django_config/settings.py` 就绪),不要直接用 psycopg2-binary 连 |
| 中文排序/搜索 | `icontains` 依赖 collation,按 runbook 第 5 步在两个引擎上对比 |
| 唯一约束与空串 | ''→NULL 后,唯一列的多个 NULL 不冲突;username/token/card 均有显式值,不受影响 |

## 三、字段类型与数据写入核查(已确认安全的部分)

- 无原生 SQL、无 `.extra()`、无 `filter(field="")`/`== ""` 判断(apps 全量 grep 核查)。
- `import_laravel_data.py`:空 CSV 值跳过,不会写 ''。
- `LegacyJSONField`:读写均按字符串处理,'' → NULL → `to_python` 正常兜底。
- 分页/排序:ORM offset/limit + 显式 ordering,无 PG 专有语法(DATABASE_MAPPING.md 风险 4)。

## 四、迁移前预检(在源 PostgreSQL 上执行)

找出所有"字节长度会超 GaussDB 列定义"的数据:

```sql
DO $$
DECLARE r record; n bigint;
BEGIN
  FOR r IN
    SELECT table_name, column_name, character_maximum_length AS len
    FROM information_schema.columns
    WHERE table_schema = 'public' AND data_type = 'character varying'
  LOOP
    EXECUTE format('SELECT max(octet_length(%I)) FROM %I.%I',
                   r.column_name, 'public', r.table_name) INTO n;
    RAISE NOTICE '%.%  上限=%字节  实际最大=%  %',
      r.table_name, r.column_name, r.len, n,
      CASE WHEN n > r.len THEN '<< GaussDB 按字节算会超限' ELSE 'OK' END;
  END LOOP;
END $$;
```

预检发现超限时按 P0-1 的对策处理;全部 OK 也建议抽样对比导入前后的行数与关键字段(max/avg 长度、NULL 数)确认无静默截断。

## 五、导入后验证清单

与 [GAUSSDB_DEPLOYMENT.md](GAUSSDB_DEPLOYMENT.md) 第 5 步对应,每项记录驱动/版本/模式、数据集、结果与回滚点,失败即阻断切换:

1. 17 张表逐表序列同步后,新 INSERT 主键无冲突。
2. NOT NULL 字段 default=" " 生效:ORM 不显式赋值的 INSERT 不报 NOT NULL violation。
3. JSON 字段(Report.dinner_reservation、ScanFiles.files、Recommend.file、LiveReport.files)读写 round-trip。
4. 中文 icontains 搜索、排序、分页与 PG 结果对比。
5. timezone:timestamptz 写入/读回与 Laravel PRC 数据对齐。
6. 唯一约束(username/token/card)与软删除过滤。
7. ticket 容量校验的 `select_for_update()` 行为(FOR UPDATE 在 9.2.4 基线可用,实测确认)。
8. 长文本写入:构造接近字节上限的中文 content/remark 写入,确认无 index/超长报错。

## 参考

- GaussDB(DWS) 与 PostgreSQL 兼容性说明:https://blog.csdn.net/gaussdb_dws/article/details/124275817
- 华为云 DWS 兼容 PG 版本 FAQ:https://support.huaweicloud.com/dws_faq/dws_03_0030.html
- 字节语义/空串行为参数细节属华为文档知识整理,未实时核对原文;请按实例版本在 support.huaweicloud.com 检索"GaussDB(DWS) 字符类型"、"nls_length_semantics"、"DBCOMPATIBILITY"。
