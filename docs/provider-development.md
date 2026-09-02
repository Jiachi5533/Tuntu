# Tuntu Provider 开发指南

## 1. 边界

Provider 只把外部来源转换成 Tuntu 领域对象，不负责规则、选择、下载或数据库事务。v0.1 支持两类：

- Discovery Provider：`collect(scope, run_id=...) -> list[ContentItem]`
- Candidate Provider：`search(item, run_id=...) -> list[Candidate]`

接口在 1.0 前属于实验性。v0.1 不支持运行时安装插件；新增内置 Provider 需要代码、Fixture 和发布审核。

## 2. 必要行为

- 提供稳定 `name` 和 `kind`。
- 内置公网 Provider 默认只访问无需登录、Cookie、验证码或浏览器自动化的公开来源。`javdb_ranking` / `javdb_detail` 是明确例外：可使用部署者主动配置的会员 Cookie 和 User-Agent，但不得读取浏览器存储、自动登录或处理验证码。`authorized_json_api` 是运维方自有/授权 API 边界，只支持可配置 Bearer Token。
- 所有 HTTP 使用 `ProviderHttpClient`，继承超时、重试、退避、限速、响应上限和缓存。
- Discovery Provider 负责来源语义的标识规范化。
- Candidate Provider 只返回含 BitTorrent v1 BTIH 的 magnet，并保存来源证据。
- 正常空结果返回空列表；结构变化、访问挑战和无效响应抛出带稳定错误码的 `ProviderError`。
- 不保存或记录完整原始响应、秘密 URL 参数、内容 Cookie 或 Authorization。

## 3. 测试

每个 Provider 至少需要离线 Fixture 覆盖：正常解析、空结果、结构变化、无效候选和相同 BTIH 合并。Fixture 必须使用虚构标识和散列，不提交真实成人 magnet 或秘密。

Live probe 只用于低频人工验证，输出状态、数量和字段形状，不打印标题、番号、magnet 或响应正文：

```bash
.venv/bin/python scripts/probe_sources.py --query TEST-001
```

站点当前可访问不等于接口稳定；发布条件以 Fixture、故障隔离和受控 live probe 共同判断。

## 4. JavDB 网页 Provider

JavDB 榜单通过 `/rankings/movies` 解析；候选源优先复用 `ContentItem.metadata.javdb_detail_path`，没有详情路径时先请求 `/search?q=<normalized_key>&f=all`，只接受规范化标识完全匹配的结果，再解析详情页 `#magnets-content`。搜索为空是正常结果；登录页报 `authentication_required`，访问挑战报 `access_challenge`，DOM 变化报 `structure_changed`。

Cookie 属于秘密设置，API 只返回 `javdb_cookie_configured`。Fixture 必须使用虚构会话值和磁力；Live probe 不打印标识、标题、Cookie、详情 URL 或 magnet。

## 5. 自有 / 授权 JSON API

在设置页填写 API 地址和可选 Bearer Token 后，Tuntu 会为每个内容发送：

```json
{
  "namespace": "jav",
  "key": "ABC-001",
  "raw_key": "ABC-1",
  "title": "可选标题"
}
```

服务应返回：

```json
{
  "results": [
    {
      "magnet_uri": "magnet:?xt=urn:btih:0000000000000000000000000000000000000000",
      "title": "候选标题",
      "size_mb": 1024,
      "seeders": 3,
      "chinese_subtitles": "unknown",
      "uncensored": "unknown",
      "uhd": "unknown"
    }
  ]
}
```

后三个字段可省略，合法值为 `yes`、`no`、`unknown`。正常无结果返回 `{"results": []}`。Token 只进入 `Authorization: Bearer ...` 请求头，不进入页面响应、日志或运行证据。
