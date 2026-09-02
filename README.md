# Tuntu（吞吐）

> **开发已暂停（2026-09-02）**，保留最后一个 `0.1.0` 开发快照供参考。暂停前全量回归为 198 项测试、53 项子测试通过；暂不承诺持续维护或来源可用性。仓库不包含运行数据库、账号凭据、Cookie 或下载历史。

核心代码、离线验收、真实 CloudDrive2 下载闭环，以及 Linux `amd64`/`arm64` 双架构容器验收均已完成。网页源仍受会话有效期、限流和页面结构变化影响；部分非标准番号的搜索匹配尚待增强。

Tuntu 是开源、自托管的 BitTorrent 自动化控制面。它从榜单或手工输入中发现内容，聚合公开 magnet，完成规范化、去重、规则筛选和稳定排序，再将选中的任务提交给 CloudDrive2。

项目最初解决 JAV 热榜自动下载，但核心使用内容中立的 `ContentItem`、`Candidate` 和 `DownloadTask`；榜单来源、候选来源、规则、路径和运行环境都可配置，不把成人内容或开发者 NAS 写死在核心管线中。

```text
榜单 / 手工标识 / 直接 magnet
              ↓
     规范化、证据合并与规则
              ↓
      全局去重与候选选择
              ↓
       CloudDrive2 → 云盘
              ↓
       状态、事件与历史记录
```

## v0.1 能力

- JavDB 网页榜单与番号/详情磁力刮削、JavDatabase 榜单，以及 Sukebei、Knaben、Bitsearch 候选来源。
- 独立热榜页持久展示完整榜单和封面，支持“无图 / 模糊 / 正常”三档隐私模式。
- 人物、系列和关键词关注清单：批量导入元数据、绑定下载配置、每日处理待处理条目，并记录真实下载状态。
- 可配置的自有/授权 JSON 候选 API，支持 Bearer Token，不把部署者的数据源地址写死在项目中。
- 中文字幕、无码、UHD、体积、做种数和关键词规则，保留每条接受或拒绝原因。
- 新订阅默认预演；每个内容只选一个合格候选。
- 同一 CloudDrive2 实例内按内容身份和 BTIH 全局去重。
- 手工番号查询、直接 magnet 预览与二次确认强制重提。
- 关注清单批量入口禁止 magnet/torrent；自动提交和单项下载都要求用户确认只处理有权获取的内容。
- 严格区分 `submitted`、`downloading`、`completed`、`failed` 和 `attention_required`。
- SQLite 持久保存配置、证据、运行、下载事件和审计记录。
- 每日调度、重启恢复、来源故障隔离和 CSV 历史导出。
- 单管理员、一次性 Setup Token、Argon2id 密码和可撤销会话。
- 简体中文响应式管理后台和同源 `/api/v1`。
- 非 root Docker / Compose 部署，支持 `amd64` 与 `arm64` 构建。

## 快速开始

```bash
docker compose up -d --build
docker compose logs tuntu
```

首次启动日志只会生成一次短期 Setup Token。打开 `http://你的主机:8000`，使用该 Token 创建管理员，然后在“系统设置”中配置 CloudDrive2。

忘记密码时生成新的 Setup Token：

```bash
docker compose exec tuntu tuntu reset-setup-token
```

设置页支持 CloudDrive2 endpoint、API Token 或账号密码、TLS/CA、全局根目录、测试目录、轮询策略、Provider 出站代理、JavDB 会员 Cookie/User-Agent、内置来源地址覆盖和热榜封面模式。JavDB Cookie 是可选秘密，只用于部署者配置的 JavDB 地址，不会通过 API 或页面回显。新建订阅默认关闭“自动提交”，建议先运行预演并核对规则原因。

更完整的权限、备份、恢复、升级和故障处理见 [运维指南](docs/operations.md)。

## 配置原则

- SQLite 是运行配置的事实来源。
- `TUNTU_*` 环境变量用于启动参数或显式覆盖；未设置的环境字段不会遮盖数据库值。
- CD2 endpoint、端口、凭据、根目录和 Profile 子目录不含任何作者环境默认值。
- 环境变量注入的字段会在设置页标记为“环境覆盖”，页面保存值不会取代覆盖值。
- `/data` 包含数据库、迁移前备份和轮转日志。拥有该数据卷读取权限的人仍可读取其中秘密。

完整示例见 [.env.example](.env.example)。

## 开发与测试

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

本地启动：

```bash
TUNTU_DATA_DIR=./data .venv/bin/tuntu serve
```

Provider 扩展约定见 [Provider 开发指南](docs/provider-development.md)。v0.1 的 Provider 接口在 1.0 前仍可能调整，不支持运行时安装第三方插件。

## 安全与使用边界

Tuntu 面向可信局域网单实例部署。建议通过反向代理启用 HTTPS，不要将应用或 CloudDrive2 直接暴露到公网。它不实现 BitTorrent 协议，不自动登录或提取浏览器 Cookie，不绕过验证码、付费墙或反机器人挑战，也不会删除 CD2 任务或云盘文件。JavDB 网页源只复用部署者主动提供的有效会话，失效时明确要求更新。

只配置你有权访问的数据源，只下载你有权获取的内容。公开仓库 Fixture 使用虚构数据；CD2 实机验收只应使用合法公开测试资源。

“关注清单”批量导入不会接受下载链接。它可以绑定一个下载配置，每天把待处理标识交给可按标识查询的候选源，经同一套规则、选择和全局去重后预演或提交。启用自动提交或使用单项 magnet 时都必须确认只处理有权获取的内容；Tuntu 不会仅根据人物名称自动抓取或提交未经授权的资源。

v0.1 不包含 qBittorrent、PT、`.torrent`、p115 直连、通用可视化来源编辑器、多用户、通知和详情级媒体刮削。

## 文档

- [产品需求](docs/PRD-v0.1.md)
- [开发计划](docs/PLAN-v0.1.md)
- [架构说明](docs/architecture.md)
- [运维指南](docs/operations.md)
- [安全策略](SECURITY.md)

## License

MIT
