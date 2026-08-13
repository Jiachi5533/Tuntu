# Tuntu

Tuntu（吞吐）是一个开源、可扩展的 BitTorrent 自动化与下载路由服务。
它从榜单、RSS、搜索、订阅、手动输入或 PT 站等来源“吞入”内容与发布候选，
完成标准化、去重、过滤和排序后，再把任务“吐出”给合适的下载客户端。

项目最初用于 JAV 热榜自动订阅并通过 CloudDrive2 下载到 115 网盘，
但核心不绑定 JAV、榜单或某一种下载器。社区可以添加电影热榜、剧集订阅、
通用 RSS、公开 DHT 索引、PT 站，以及其他获得授权的数据源。

## 工作方式

```text
榜单 / RSS / 搜索 / 订阅 / 手动输入
                  ↓
             内容标准化
                  ↓
       公共 BT / PT / 自建索引
                  ↓
     精确匹配、去重、规则与排序
                  ↓
        下载路由（按类型和标签）
          ↙                    ↘
CloudDrive2 → 115        qBittorrent → 本地
```

公开 BT 通常可以使用 magnet。PT 发布通常需要带私有 tracker 或 passkey 的
`.torrent` 文件，因此 Tuntu 的候选模型同时支持 `magnet`、`torrent` 和普通 URL，
不会把所有来源错误地压成同一种链接。

## 设计目标

- 可插拔的发现源、候选源、规则和下载客户端。
- 根据传输类型、来源标签和用户规则选择下载路径。
- 使用 BTIH 等稳定身份进行跨来源去重。
- 可解释过滤：每个被拒绝的候选都保留原因。
- 扫描发现与真正提交下载明确分离。
- 记录发现、接受、提交、完成、跳过、失败和重试状态。
- 核心不内置 PT 做种、分享率或特定内容类别假设。

## 当前状态

Tuntu 处于 pre-alpha 阶段。仓库目前包含内容无关的核心模型、插件协议、
去重与过滤流水线、下载路由、示例配置和测试。下一步会迁入已经验证的 JAV
数据源，并实现 SQLite 状态库与 CloudDrive2 下载客户端。

## 开发

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

扩展模型见[架构文档](docs/architecture.md)和
[`config.example.yaml`](config.example.yaml)。

## 合规使用

只配置你有权访问的数据源和下载内容。Provider 必须遵守适用的服务条款、
速率限制与访问控制。Tuntu 不绕过登录、年龄验证、付费墙或反机器人挑战。

## License

MIT

