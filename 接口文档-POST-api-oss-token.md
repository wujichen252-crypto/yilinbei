# 接口文档：获取 OSS 直传凭证（`POST /api/oss/token`）

> **给前端**：这是「前端直传阿里云 OSS」方案（方案 B：STS）的凭证签发接口。
> **后端已实现且凭证已配置**：大文件/视频走本接口拿 STS 凭证直传；小文件走
> `POST /api/oss/upload` 由后端代理上传（见第 6 节），**已经实测可用**。
> 本接口（视频直传）目前还差运维在 RAM 控制台创建角色（ARN），创建前会返回
> `code: 1, msg: "阿里云OSS服务未配置"`。

**上传通道怎么选**：

| 文件 | 通道 |
| --- | --- |
| 视频 `video`（最大 700MB） | 本文：签 STS 凭证 → 前端 `multipartUpload` 直传 |
| 头像 / 集体照 / 曲谱 / 文件 | `POST /api/oss/upload` 后端代理上传（multipart 交文件即可，无需 SDK） |

---

## 1. 接口定义

| 项 | 值 |
| --- | --- |
| 路径 | `POST /api/oss/token` |
| 鉴权 | 需要，`Authorization: Bearer <token>`（与其它业务接口一致） |
| Content-Type | `application/json` |
| 成功 HTTP 状态 | 200（业务错误也返回 200，靠 `code` 区分） |
| 鉴权失败 | 401 |

## 2. 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `biz` | string | 是 | 业务类型，见下表 |
| `filename` | string | 是 | 原始文件名（只用扩展名，用于生成 key） |
| `contentType` | string | 是 | MIME 类型，必须在 `biz` 的允许类型内 |
| `fileSize` | number | 是 | 字节数，后端据此校验上限（前端校验可绕过，以后端为准） |

**`biz` 取值约定**：

| biz | 业务 | 大小上限 | 允许 contentType |
| --- | --- | --- | --- |
| `video` | 展示视频 | 700 MB | `video/mp4`, `video/quicktime` |
| `image` | 头像 | 1 MB | `image/jpeg`, `image/png` |
| `photo` | 乐团集体照 | 20 MB | `image/jpeg`, `image/tiff` |
| `spectrum` | 曲谱 | 20 MB | `application/pdf` |
| `doc` | 推荐表 / 审核图 | 20 MB | `application/pdf` |

> 注意：`.tif / .tiff` 文件部分浏览器给出的 `file.type` 为空，请按扩展名兜底映射成
> `image/tiff` 再传，否则会命中「不支持的文件类型」。

```json
{
  "biz": "video",
  "filename": "演出视频.mp4",
  "contentType": "video/mp4",
  "fileSize": 734003200
}
```

## 3. 成功响应

字段名已与后端冻结，照此消费即可：

```json
{
  "code": 0,
  "msg": "",
  "data": {
    "accessKeyId":     "STS.xxxxx",
    "accessKeySecret": "xxxxx",
    "securityToken":   "CAIS.....",
    "expiration":      "2026-09-22T01:00:00Z",
    "region":          "oss-cn-chengdu",
    "bucket":          "yilinbei-oss",
    "endpoint":        "oss-cn-chengdu.aliyuncs.com",
    "host":            "https://yilinbei-oss.oss-cn-chengdu.aliyuncs.com",
    "key":             "video/20260922/9f2c1a8b4e6d4f0a8c3b2e1d7a5c8f30.mp4"
  }
}
```

| 字段 | 用途 |
| --- | --- |
| `accessKeyId` / `accessKeySecret` / `securityToken` | STS 临时凭证三件套（有效期见 `expiration`，默认 3600 秒），只放 `ali-oss` 的 SDK 配置里，**不要**写日志 / localStorage |
| `expiration` | ISO 时间，发起上传前若已过期就重新签一次 |
| `region` / `bucket` / `endpoint` / `host` | SDK 初始化参数；`host` 是最终文件访问域名 |
| `key` | **后端生成的对象路径**（`{biz}/{日期}/{uuid}.{ext}`），全局唯一。前端**直接使用，不要改写、不要自己拼 key**——旧代码里的 `rename()` 逻辑可以删掉了 |

## 4. 失败响应

统一 `HTTP 200 + code: 1`（未带 token / token 失效为 401）：

| msg | 触发条件 |
| --- | --- |
| `阿里云OSS服务未配置` | 运维尚未配置凭证（联调前期会见到，可回落七牛） |
| `未知的业务类型` | `biz` 不在白名单 |
| `视频大小不能超过700MB` / `图片大小不能超过1MB` / `照片大小不能超过20MB` / `曲谱大小不能超过20MB` / `文件大小不能超过20MB` | `fileSize` 超上限或 ≤0 |
| `fileSize不合法` | `fileSize` 不是数字 |
| `不支持的文件类型` | `contentType` 不在 `biz` 允许类型内 |
| `获取上传凭证失败，请稍后重试` | 阿里云 STS 侧异常，可重试 |

```json
{ "code": 1, "msg": "视频大小不能超过700MB", "data": null }
```

## 5. 前端使用示例（ali-oss）

```js
import OSS from "ali-oss"

async function uploadToOss(file, biz) {
  // 1. 每次上传先签一个临时凭证（每个文件签一次，简单且安全）
  const res = await request.post("/api/oss/token", {
    biz,
    filename: file.name,
    contentType: file.type,   // tiff 等记得按扩展名兜底
    fileSize: file.size,
  })
  if (res.data.code !== 0) throw new Error(res.data.msg)
  const c = res.data.data

  // 2. 初始化客户端（凭证是 1 小时短期凭证，别缓存到全局长期复用）
  const client = new OSS({
    region: c.region,                 // "oss-cn-chengdu"
    accessKeyId: c.accessKeyId,
    accessKeySecret: c.accessKeySecret,
    stsToken: c.securityToken,
    bucket: c.bucket,
  })

  // 3. 上传：小文件用 put；大视频用 multipartUpload（分片 + 断点续传）
  await client.multipartUpload(c.key, file, {
    partSize: 5 * 1024 * 1024,
    mime: file.type,
    progress: (p, checkpoint) => {
      // 可把 checkpoint 存下来，断网后用 multipartUpload(key, file, {checkpoint}) 续传
    },
  })

  // 4. 拼最终 URL，走现有 /api/file/create 入库（该接口无任何改动）
  const url = `${c.host}/${c.key}`
  await request.post("/api/file/create", {
    filename: file.name,
    type: biz,
    size: file.size,
    url,
  })
  return url
}
```

要点：

- `key` 只用后端返回的，前端不再参与命名 → 之前「同名文件互相覆盖」的缺陷由后端 key 唯一性根治。
- 一个凭证在有效期内的策略只允许写 `{biz}/{日期}/` 这个前缀，别的路径写不进去；也不要尝试拿它上传非当次的业务文件。
- 视频最大 700MB，务必走 `multipartUpload`（`put` 会一次进内存且受单请求超时限制）。
- 凭证过期后重新请求本接口换新凭证即可，旧凭证自动失效，无需注销。

## 6. 小文件后端代理上传：`POST /api/oss/upload`

头像 / 集体照 / 曲谱 / 文件（即除 `video` 外的所有 biz）**不需要 ali-oss SDK**，
用 multipart 表单把文件直接交给后端，后端校验后上传 OSS 并返回最终 URL。

**请求**（`multipart/form-data`，鉴权同其它接口）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `biz` | string | `image` / `photo` / `spectrum` / `doc`（传 `video` 会被拒并提示走直传） |
| `file` | file | 文件本体；后端按**真实大小**校验上限，按**扩展名**校验类型（浏览器 MIME 对 tiff 等常为空，不可靠） |

扩展名白名单：`image` → jpg/jpeg/png；`photo` → jpg/jpeg/tif/tiff；`spectrum` → pdf；`doc` → pdf。
大小上限与本文第 2 节的 `biz` 表一致（image 1MB，photo/spectrum/doc 20MB）。

**成功响应**：

```json
{
  "code": 0,
  "msg": "",
  "data": {
    "url":      "https://ylbxt.oss-cn-chengdu.aliyuncs.com/image/20260922/9f2c....png",
    "key":      "image/20260922/9f2c....png",
    "filename": "头像.png",
    "size":     10240
  }
}
```

`key` 同样由后端生成（`{biz}/{日期}/{uuid}.{ext}`），前端拿到 `url` 后照旧
`POST /api/file/create` 入库。

**失败响应**（`HTTP 200 + code:1`，401 同前）：

| msg | 触发条件 |
| --- | --- |
| `未知的业务类型` | `biz` 不在白名单 |
| `视频请使用前端直传：POST /api/oss/token` | `biz` 传了 `video` |
| `图片大小不能超过1MB` / `照片大小不能超过20MB` / … | 文件超限（按真实文件大小） |
| `不支持的文件类型` | 扩展名不在白名单 |
| `缺少文件` | 没传 `file` 字段 |
| `上传失败，请稍后重试` | OSS 侧异常，可重试 |
| `阿里云OSS服务未配置` | 凭证被移除时才会出现（当前已配置） |

**前端示例**：

```js
async function uploadSmall(file, biz) {
  const form = new FormData()
  form.append("biz", biz)
  form.append("file", file)
  const res = await request.post("/api/oss/upload", form)
  if (res.data.code !== 0) throw new Error(res.data.msg)
  const url = res.data.data.url

  await request.post("/api/file/create", {
    filename: file.name, type: biz, size: file.size, url,
  })
  return url
}
```

> ⚠️ 若 axios 封装里强制了 `Content-Type: application/json`，对 FormData 请求要放行：
> 不要手动设置 Content-Type，让浏览器自动带 multipart boundary，否则后端收不到文件。

## 7. 自测（1 分钟）

```bash
# 小文件代理上传（当前已可用）
curl -i -X POST https://<后端地址>/api/oss/upload \
  -H "Authorization: Bearer <token>" \
  -F "biz=image" \
  -F "file=@a.png;type=image/png"

# 视频直传凭证（待 RAM 角色配置）
curl -i -X POST https://<后端地址>/api/oss/token \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"biz":"video","filename":"a.mp4","contentType":"video/mp4","fileSize":734003200}'
```

- 代理上传：`code:0` + 第 6 节字段（已实测通过）。
- 直传凭证：当前 `code:1, msg:"阿里云OSS服务未配置"`；ARN 配好后返回 `code:0` + 第 3 节字段。

## 8. 当前状态

- [x] `POST /api/oss/upload` 小文件代理上传：**已配置真实凭证并实测通过**（上传→对象可见→清理）
- [x] `POST /api/oss/token` 视频直传：代码就绪，服务端校验 / key 生成 / STS 策略收敛完成
- [x] CORS 已放行（含 `ngrok-skip-browser-warning` 头）
- [ ] **唯一阻塞**：RAM 控制台创建「允许 OSS 上传」的角色并把 ARN 填进后端 `ALIYUN_OSS_STS_ROLE_ARN`，另需为 bucket 配 CORS 规则（浏览器直传必须）
- [ ] 历史文件仍走七牛，新旧并存
