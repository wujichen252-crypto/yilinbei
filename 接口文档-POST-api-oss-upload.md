# 接口文档：小文件后端代理上传（`POST /api/oss/upload`）

> **给前端**：头像 / 集体照 / 曲谱 / 推荐表等小文件**不需要 ali-oss SDK**，用 multipart
> 表单把文件交给本接口，后端校验后上传 OSS 并返回最终 URL。**当前已配置真实凭证并实测可用。**
> 大文件（视频，最大 700MB）不走这里：请用「前端直传」方案，见
> [接口文档-POST-api-oss-token.md](接口文档-POST-api-oss-token.md)。

---

## 1. 接口定义

| 项 | 值 |
| --- | --- |
| 路径 | `POST /api/oss/upload` |
| 鉴权 | 需要，`Authorization: Bearer <token>`（与其它业务接口一致） |
| Content-Type | `multipart/form-data`（FormData，**不要**手动设为 application/json） |
| 成功 HTTP 状态 | 200（业务错误也返回 200，靠 `code` 区分） |
| 鉴权失败 | 401 |

## 2. 请求字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `biz` | string | 是 | 业务类型：`image` / `photo` / `spectrum` / `doc` |
| `file` | file | 是 | 文件本体 |

后端按**真实文件大小**校验上限（前端传不了假数字），按**文件扩展名**校验类型
（浏览器对 tiff 等类型的 `file.type` 常为空，所以不用 MIME 判断）：

| biz | 业务 | 大小上限 | 允许扩展名 |
| --- | --- | --- | --- |
| `image` | 头像 | 1 MB | `.jpg` `.jpeg` `.png` |
| `photo` | 乐团集体照 | 20 MB | `.jpg` `.jpeg` `.tif` `.tiff` |
| `spectrum` | 曲谱 | 20 MB | `.pdf` |
| `doc` | 推荐表 / 审核图 | 20 MB | `.pdf` |
| `video` | —— | —— | **会被拒绝**，返回提示走 `POST /api/oss/token` 前端直传 |

## 3. 成功响应

```json
{
  "code": 0,
  "msg": "",
  "data": {
    "url":      "https://ylbxt.oss-cn-chengdu.aliyuncs.com/image/20260922/9f2c1a8b....png",
    "key":      "image/20260922/9f2c1a8b....png",
    "filename": "头像.png",
    "size":     10240
  }
}
```

| 字段 | 说明 |
| --- | --- |
| `url` | 文件最终访问地址，**照旧 `POST /api/file/create` 入库**（该接口无任何改动） |
| `key` | OSS 对象路径，由后端生成 `{biz}/{日期}/{uuid}.{ext}`，全局唯一——前端不参与命名，旧的 `rename()` 逻辑可删 |
| `filename` / `size` | 原文件名与字节数（回显用） |

## 4. 失败响应

统一 `HTTP 200 + code: 1`（未带 token / token 失效为 401）：

| msg | 触发条件 |
| --- | --- |
| `未知的业务类型` | `biz` 不在白名单 |
| `视频请使用前端直传：POST /api/oss/token` | `biz` 传了 `video` |
| `图片大小不能超过1MB` / `照片大小不能超过20MB` / `曲谱大小不能超过20MB` / `文件大小不能超过20MB` | 文件超上限 |
| `不支持的文件类型` | 扩展名不在白名单 |
| `缺少文件` | 没传 `file` 字段 |
| `上传失败，请稍后重试` | OSS 侧异常，可重试 |

```json
{ "code": 1, "msg": "图片大小不能超过1MB", "data": null }
```

## 5. 前端示例

```js
async function uploadSmall(file, biz) {
  const form = new FormData()
  form.append("biz", biz)
  form.append("file", file)
  const res = await request.post("/api/oss/upload", form)
  if (res.data.code !== 0) throw new Error(res.data.msg)
  const url = res.data.data.url

  // 元数据入库（沿用现有接口）
  await request.post("/api/file/create", {
    filename: file.name, type: biz, size: file.size, url,
  })
  return url
}
```

> ⚠️ 若 axios 封装强制了 `Content-Type: application/json`，对 FormData 请求要放行：
> 不要手动设置 Content-Type，让浏览器自动带 multipart boundary，否则后端收不到文件。

## 6. 自测（1 分钟）

```bash
curl -i -X POST https://<后端地址>/api/oss/upload \
  -H "Authorization: Bearer <token>" \
  -F "biz=image" \
  -F "file=@a.png;type=image/png"
```

期望：`code: 0` + 第 3 节字段（后端已用真实凭证实测：上传 → 对象可见 → 清理）。

## 7. 关联

- 视频直传（STS 凭证）：[接口文档-POST-api-oss-token.md](接口文档-POST-api-oss-token.md)
- 元数据入库：`POST /api/file/create`（现有接口，未改动）
- 历史文件仍走七牛，新旧并存
