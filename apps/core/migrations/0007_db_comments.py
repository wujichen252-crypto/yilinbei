# 为所有业务表和字段补中文注释。
# 只发 COMMENT ON（PostgreSQL 系统目录里的元数据），不读写、不改任何表数据，
# 也不重建表或索引。Django 3.2 没有 db_comment 字段选项（4.2 才有），所以走 RunSQL。
# 非 PostgreSQL 后端（如测试用的 sqlite）不支持 COMMENT ON，直接跳过。
from django.db import migrations

TABLE_COMMENTS = {
    "users": "账号表：学校/市州/组委会/管理员/省级各级登录账号",
    "personal_access_tokens": "API 访问令牌表（沿用 Laravel Sanctum 结构，只存令牌摘要）",
    "report": "节目报名表：各级账号提交的展演节目申报主表",
    "report_draft": "报名草稿表：服务端保存的未完成报名表单快照，带版本号做乐观锁",
    "person": "人员表：队员、指挥、伴奏、指导教师等报名涉及的自然人",
    "report_person": "报名人员关联表：把人员挂到具体报名上，并记录其身份与类别",
    "logs": "操作日志表：记录账号的登录、增删改、导出等行为",
    "files": "文件表：上传文件的元信息与访问地址",
    "scan_files": "扫描件表：按 (账号, 类别) 归档的扫描材料及审核状态",
    "recommend": "推荐材料表：账号上传的推荐函等材料及审核状态",
    "live_report": "现场报名表：展演现场提交的节目信息主表",
    "crew": "现场随行队员表：挂在现场报名下的队员名单",
    "leader": "现场领队表：挂在现场报名下的领队/联系人名单",
    "draw": "抽签表：按类别记录各节目的抽签出场顺序",
    "ticket": "票务场次表：展演各场次的时间、地点与放票数量",
    "ticket_subscribe": "抢票记录表：观众预约场次的提交记录",
    "students": "学生表：单独报送的学生名单",
    "statistics": "统计表：账号与统计文件的关联记录",
    "password_resets": "密码重置表（沿用 Laravel 结构，当前业务未使用）",
}

COLUMN_COMMENTS = {
    "users": {
        "id": "主键",
        "username": "登录用户名，全表唯一",
        "password": "密码哈希（bcrypt）",
        "nickname": "账号显示名称，通常为单位名",
        "description": "账号备注说明，最长 1000 字",
        "tel": "联系电话",
        "leader": "负责人姓名",
        "type": "账号类型：0=学校 1=市州 2=组委会 3=管理员 4=省级",
        "parent_id": "上级账号 id，用于市州与下属学校的层级关系，可为空",
        "last_login": "最近登录时间",
        "deleted_at": "软删除时间，非空表示已删除",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "personal_access_tokens": {
        "id": "主键，同时是 bearer 令牌前半段",
        "tokenable_type": "令牌归属的模型类名，固定为 App\\\\Models\\\\User",
        "tokenable_id": "令牌归属的账号 id",
        "name": "令牌名称",
        "token": "令牌密文的 SHA-256 摘要，全表唯一（不存明文）",
        "abilities": "令牌权限范围，JSON 字符串，默认 [\"*\"]",
        "last_used_at": "最近使用时间，用于判定令牌过期",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "report": {
        "id": "主键",
        "user_id": "提交该报名的账号 id",
        "choir_name": "乐团名称",
        "name": "节目名称",
        "name1": "第二个节目名称，可为空",
        "school_name": "学校名称",
        "desc": "节目简介，最长 1000 字",
        "group": "节目组别：小学组 / 中学组 / 大学组",
        "establishment": "乐团编制：管乐团 / 铜管乐团",
        "establishment_name": "编制补充说明",
        "contact_name": "联系人姓名",
        "contact_phone": "联系人电话",
        "contact_way": "其他联系方式",
        "time_length": "节目时长，单位分钟",
        "spectrum": "曲谱文件 id，指向 files 表",
        "file": "附件文件 id，指向 files 表",
        "dinner_reservation": "用餐预订，JSON 字符串存餐次列表",
        "status": "审核状态：-1=已驳回 0=待审核 1=已通过",
        "remark": "审核意见或备注",
        "deleted_at": "软删除时间，非空表示已删除",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "report_draft": {
        "id": "主键",
        "user_id": "草稿归属的账号 id",
        "scope": "草稿业务线，取值同账号类型：0=学校 1=市州 4=省级",
        "report_id": "被编辑的报名 id；为空表示新增报名的草稿",
        "payload": "表单快照，JSON 字符串",
        "schema_version": "表单结构版本号，用于兼容旧草稿",
        "version": "草稿版本号，前端提交时做乐观锁比对",
        "state": "草稿状态：0=编辑中 1=已提交",
        "created_at": "创建时间",
        "updated_at": "更新时间",
        "submitted_at": "提交时间，未提交为空",
    },
    "person": {
        "id": "主键",
        "name": "姓名",
        "user_id": "录入该人员的账号 id",
        "card": "身份证号，全表唯一",
        "age": "年龄",
        "school": "所在学校",
        "phone": "联系电话",
        "gender": "性别",
        "major": "专业",
        "head": "照片地址",
        "instrument": "所奏乐器",
        "other": "其他乐器或补充说明",
        "remark": "备注",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "report_person": {
        "id": "主键",
        "report_id": "所属报名 id，指向 report 表",
        "person_id": "人员 id，指向 person 表",
        "position": "人员身份：0=正式队员 1=预备队员 2=指挥 3=伴奏 4=指导教师",
        "type": "人员类别：0=学生 1=教师",
        "deleted_at": "软删除时间，非空表示已删除",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "logs": {
        "id": "主键",
        "user_id": "操作人账号 id",
        "type": "操作类型：1=修改 2=新增 3=删除 4=登录 5=退出或异常 6=导出",
        "content": "操作描述文本",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "files": {
        "id": "主键",
        "user_id": "上传者账号 id",
        "filename": "原始文件名",
        "type": "文件 MIME 类型或业务类型",
        "size": "文件大小，单位字节",
        "url": "文件访问地址",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "scan_files": {
        "id": "主键",
        "user_id": "上传者账号 id",
        "type": "扫描件类别，与 (user_id, type) 共同构成业务唯一键",
        "files": "文件列表，JSON 字符串",
        "status": "审核状态：-1=已驳回 0=待审核 1=已通过",
        "remark": "审核意见或备注",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "recommend": {
        "id": "主键",
        "user_id": "上传者账号 id",
        "file": "推荐材料文件列表，JSON 字符串",
        "status": "审核状态：-1=已驳回 0=待审核 1=已通过",
        "remark": "审核意见或备注",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "live_report": {
        "id": "主键",
        "user_id": "提交该现场报名的账号 id",
        "report_id": "对应的节目报名 id，指向 report 表",
        "name": "节目名称",
        "choir_name": "乐团名称",
        "district_or_school_name": "所属区县或学校名称",
        "group": "节目组别：小学组 / 中学组 / 大学组",
        "contact_name": "联系人姓名",
        "contact_phone": "联系人电话",
        "files": "附件列表，JSON 字符串",
        "status": "审核状态：-1=已驳回 0=待审核 1=已通过",
        "remark": "审核意见或备注",
        "is_other_show": "是否参加其他展演环节",
        "other_show_message": "其他展演环节的说明",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "crew": {
        "id": "主键",
        "live_report_id": "所属现场报名 id，指向 live_report 表",
        "name": "姓名",
        "card": "身份证号",
        "age": "年龄",
        "school": "所在学校",
        "phone": "联系电话",
        "gender": "性别：0=男 1=女",
        "major": "专业",
        "other": "其他补充说明",
        "musical_instruments": "所奏乐器",
        "arrival_time": "到达日期",
        "departure_time": "离开日期",
        "position": "人员身份：0=正式队员 1=预备队员 2=指挥 3=伴奏 4=指导教师",
        "type": "人员类别：0=学生 1=教师",
        "head": "照片地址",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "leader": {
        "id": "主键",
        "live_report_id": "所属现场报名 id，指向 live_report 表",
        "name": "姓名",
        "gender": "性别：0=男 1=女",
        "linkman": "是否为联系人：0=否 1=是",
        "age": "年龄",
        "unit": "所在单位",
        "card": "身份证号",
        "phone": "联系电话",
        "arrival_time": "到达日期",
        "departure_time": "离开日期",
        "head": "照片地址",
        "remark": "备注",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "draw": {
        "id": "主键",
        "name": "参加抽签的乐团或节目名称",
        "type": "抽签类别：0=中小学组 1=大学组 2=中小学教师组 3=高校教师组",
        "index": "抽签得到的出场顺序号",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "ticket": {
        "id": "主键",
        "type_name": "场次日期，如 11月15日",
        "ticket_session": "场次内容描述",
        "time": "时段：上午 / 下午",
        "week": "星期，当前未使用",
        "concrete": "具体起止时间，如 13:30-18:00",
        "site": "演出场馆",
        "local": "场馆补充位置说明",
        "number": "该场次放票数量",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "ticket_subscribe": {
        "id": "主键",
        "ticket_id": "预约的场次 id，指向 ticket 表",
        "name": "观众姓名",
        "card": "观众身份证号",
        "phone": "观众手机号",
        "ip": "提交时的客户端 IP",
        "code": "取票码或验证码",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "students": {
        "id": "主键",
        "name": "姓名",
        "user_id": "录入该学生的账号 id",
        "gender": "性别：0=男 1=女",
        "age": "年龄",
        "nation": "民族",
        "grade": "年级",
        "major": "专业",
        "phone": "联系电话",
        "card": "身份证号",
        "batch": "报送批次",
        "head": "照片地址",
        "status": "审核状态：-1=已驳回 0=待审核 1=已通过",
        "remark": "审核意见或备注",
        "arrival_time": "到达日期",
        "departure_time": "离开日期",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "statistics": {
        "id": "主键",
        "user_id": "关联账号 id",
        "file_id": "关联统计文件 id，指向 files 表",
        "deleted_at": "软删除时间，非空表示已删除",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "password_resets": {
        "id": "主键",
        "email": "申请重置的邮箱",
        "token": "重置令牌",
        "created_at": "令牌签发时间",
    },
}


def _literal(text):
    return "'" + text.replace("'", "''") + "'"


def apply_comments(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema()"
        )
        present = {}
        for table, column in cursor.fetchall():
            present.setdefault(table, set()).add(column)

        for table, comment in TABLE_COMMENTS.items():
            if table not in present:
                continue
            cursor.execute("COMMENT ON TABLE %s IS %s" % (quote(table), _literal(comment)))
            for column, column_comment in COLUMN_COMMENTS.get(table, {}).items():
                if column not in present[table]:
                    continue
                cursor.execute("COMMENT ON COLUMN %s.%s IS %s" % (
                    quote(table), quote(column), _literal(column_comment)))


def drop_comments(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema()"
        )
        present = {}
        for table, column in cursor.fetchall():
            present.setdefault(table, set()).add(column)

        for table in TABLE_COMMENTS:
            if table not in present:
                continue
            cursor.execute("COMMENT ON TABLE %s IS NULL" % quote(table))
            for column in COLUMN_COMMENTS.get(table, {}):
                if column not in present[table]:
                    continue
                cursor.execute("COMMENT ON COLUMN %s.%s IS NULL" % (quote(table), quote(column)))


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_reportdraft_unique_editing"),
    ]

    operations = [
        migrations.RunPython(apply_comments, drop_comments),
    ]
