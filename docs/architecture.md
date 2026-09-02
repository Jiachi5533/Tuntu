# Tuntu v0.1 架构说明

## 1. 架构目标

Tuntu 是下载自动化控制面，不是 BitTorrent 协议引擎。v0.1 只实现“公开内容发现与 magnet 清洗 → CloudDrive2 → 115”的单一路径，同时保持领域模型对内容类型中立。

```text
DiscoveryProvider ─→ ContentItem ─→ CandidateProvider ─→ CandidateEvidence
                           │                                  │
                           └────── normalize / merge ─────────┘
                                              ↓
                                    RuleSet → Evaluation
                                              ↓
                                  Selector → global dedupe
                                              ↓
                                CloudDrive2 DownloadClient
                                              ↓
                                   asynchronous status sync
```

## 2. 运行边界

v0.1 是一个单实例、单进程应用：

- FastAPI 提供同源 `/api/v1` 和 Jinja 管理后台。
- APScheduler 在进程内触发每日 Run 和下载状态轮询。
- SQLite 是配置、运行与历史记录的事实来源。
- Provider 通过有超时、有限重试的 HTTP 客户端访问；CloudDrive2 使用官方 gRPC，并为任务查询和文件树查询设置独立 Deadline。
- `/data` 保存数据库、迁移前备份和轮转日志。

不使用 Redis、Celery、外部数据库、独立前端应用或多个 Web worker。

每日计划使用 APScheduler 3.x 的内存 JobStore，但计划定义来自 SQLite Profile。启动和设置变更时从当前启用 Profile 重建未来计划，因此 JobStore 不承担事实来源，也不会补跑停机期间错过的计划。同一 Profile 同时由进程锁和 SQLite 部分唯一索引保护；不同 Profile 受可配置全局并发上限约束。

所有部署相关值都来自设置页/SQLite或环境变量覆盖。CD2 endpoint、认证方式、秘密、TLS、根目录、测试子目录、RPC Deadline、离线任务查询 Deadline、轮询间隔、关注阈值、提交后目录检查延迟、稳定观察次数以及文件树深度/条目上限不得作为环境常量写入领域层；Profile 只保存相对于全局根目录的可配置子目录。使用受限 API Token 时，宿主根目录会映射为 gRPC 虚拟根 `/`，适配器必须区分“设置展示路径”和“请求路径”，禁止重复拼接。

## 3. 核心边界

### 3.1 DiscoveryProvider

把榜单或手工输入转换为 `ContentItem`。Provider 负责来源特有的标识规范化，例如 JAV 番号的大小写、分隔符和补零；核心不做跨命名空间模糊猜测。

### 3.2 CandidateProvider

为一个 `ContentItem` 返回公开 magnet 和来源证据。v0.1 只接受 BitTorrent v1 `btih`，不接受 `.torrent` 或纯 BitTorrent v2 `btmh`。

公网 Provider 共用受控 HTTP 边界：有限重试和指数退避、最小请求间隔、响应大小限制、Run 内请求去重和短 TTL 缓存。JavDB 网页 Provider 可附带部署者配置的 Cookie、User-Agent、语言和同源 Referer；设置变更会重建 HTTP 客户端并清空缓存。正常空结果与失败分开记录，单个来源失败由 ProviderRunner 隔离并写入 `source_health`，不会自动禁用来源。

### 3.3 RuleSet 与 Selector

规则只负责接受或拒绝，并保存全部原因。Selector 对合格候选执行固定排序：已知做种数降序、已知体积升序、BTIH/来源稳定排序；每个内容只选择一个候选。

### 3.4 DownloadClient

负责健康检查、提交、状态查询和完成验证。v0.1 只有 CloudDrive2 实现，但接口不包含 JAV 语义，后续下载器可以独立实现。

### 3.5 Watchlist

人物、系列和关键词关注清单复用全局 `ContentItem` 身份。批量导入边界只接受作品标识、标题、封面、发行日期和出处等元数据，拒绝 magnet、torrent 和下载地址。关注清单可绑定一个 Profile，每日把仍为待处理的标识作为 `watchlist` Run 交给该 Profile 的查询型候选源、规则、Top N、目标目录和全局去重链路。预演不会提交；开启自动提交时必须留下使用权确认审计。单项授权 magnet 仍保留独立确认入口，所有真实状态都来自同一 `DownloadTask` 状态机。

## 4. 身份与证据

### ContentItem 身份

内容身份为 `namespace + normalized_key`。多个榜单命中同一身份时合并内容，但保留每个来源和排名证据；默认展示最优排名。

### Candidate 身份

候选身份为规范化 BitTorrent v1 BTIH。40 位十六进制和 32 位 Base32 统一为小写十六进制。magnet 的显示名称、Tracker 和参数顺序不参与身份；选中后提交给下载客户端的规范 URI 仍保留 `dn`、`tr`、`ws`、`xs` 和非 v1 `xt` 等传输提示，避免去重过程降低资源可达性。

### 属性证据

标题、体积、做种数、中文字幕、无码状态和 UHD 都来自来源证据。后三项使用 `yes/no/unknown`；体积和做种数也允许未知，不能用 `0` 冒充未知。属性证据冲突时保留全部 Evidence：三态和体积回到未知，做种数保留各来源值并取已知最大值用于当前排序。

## 5. Run 与 DownloadTask

`Run` 负责一次发现、清洗、评估、选择和提交。Run 保存不可变配置快照，完成提交阶段后即结束，不等待外部下载。预演是默认行为；自动模式只提交每个内容排序第一的合格候选，只有第一候选被 CD2 明确同步拒绝时才尝试下一候选。来源空结果是成功，局部来源/条目/提交失败为 `partial`，所有榜单源失败或初始化失败为 `failed`。

`DownloadTask` 独立跟踪 CloudDrive2：

```text
submitting → submitted → downloading → completed
     │            │             │
     └────────────┴─────────────┴──→ failed / attention_required
```

`completed` 必须有 CD2 任务 API，或提交前目录基线之后新增/变化的文件集合、文件数和总大小经过用户配置次数的成功观察保持稳定，且不得低于两次。任务 API 超时不能覆盖可用的文件树证据。为了让同一次 Top N Run 可以批量提交且仍能准确归属文件，每个任务使用 `Profile 目录 / 完整 BTIH` 作为内部隔离目录；CD2 客户端通过官方 `CreateFolder` 确保目录存在。人工确认完成是单独审计事件，不能伪装成自动完成。

未结束下载不属于 Run 生命周期。独立轮询器每次从 SQLite 读取 `submitting/submitted/downloading/attention_required`，逐项隔离错误；应用重启后无需恢复内存任务即可继续反查和完成判定。启动时遗留的 `running` Run 会以进程中断原因结束，不会被当作仍在执行。

## 6. 持久化与迁移

- SQLAlchemy ORM 只存在于 `tuntu.db`，领域模型不导入 ORM。
- Alembic 迁移创建 PRD 要求的核心表及关注清单扩展表；ORM 元数据与最新迁移有自动漂移测试。
- `namespace + normalized_key` 与 BTIH 由数据库唯一约束保护；自动下载用“客户端 + 内容 + 代次”和“客户端 + BTIH + 代次”双唯一约束跨 Profile 占位。
- Run 配置快照和 DownloadEvent 由 SQLite trigger 防止原地改写；Profile 只归档，不级联删除历史。
- 已有数据库升级前用 SQLite backup API 生成一致备份，只保留最近 3 份。迁移在同目录临时副本完成并验证后原子替换；失败保留原库，新程序遇到未知未来版本时拒绝降级。
- SQLite 锁使用短 `busy_timeout`，超过后转换成可识别错误，不静默重试写入或重复建任务。

## 7. 安全边界

- 新 Profile 默认 dry-run，显式开启后才自动提交。
- 提交前在 SQLite 中建立幂等占位，按 ContentItem 和 BTIH 跨 Profile 去重。
- 明确同步拒绝可以尝试下一候选；网络结果不明只重试原候选；提交后的失败不自动换 magnet。
- 除 JavDB 网页 Provider 外，内置公网 Provider 只支持无需登录、Cookie、验证码或浏览器自动化的来源；JavDB 只接受部署者主动保存的会话 Cookie，不读取浏览器存储、不自动提交登录或验证码。运维方自有/授权 JSON API 仅支持可配置 Bearer Token。
- 关注清单的批量元数据导入拒绝 magnet/torrent 字段与非 HTTP(S) 展示 URL；自动提交和逐项授权链接都需要显式使用权确认。
- 密码、Token、Cookie 和 Authorization 不得进入普通响应、日志或 Fixture。
- 目标目录必须位于用户配置的 CloudDrive2 根目录下；代码不得假设 `/115open`、容器主机名或固定端口。
- CD2 返回的文件路径必须仍位于请求目标目录内；越界结果作为配置/协议错误拒绝，不参与完成判定。

## 8. 扩展策略

Provider 与 DownloadClient 使用清晰的 Python 接口，但 v0.1 不实现运行时插件安装，也不承诺 1.0 前的第三方接口稳定性。

qBittorrent、PT、`.torrent`、p115 直连、通用 RSS/HTTP 编辑器和复杂路由是后续候选方向，不属于当前架构的实现范围。核心只保留必要接口边界，不为这些未来功能预建 UI、数据库或基础设施。

## 9. 相关决策

- [ADR-001：内容身份与全局去重](adr/001-content-identity-and-global-deduplication.md)
- [ADR-002：Run 与 DownloadTask 生命周期](adr/002-run-and-download-lifecycles.md)
- [ADR-003：CloudDrive2 完成语义](adr/003-clouddrive2-completion-semantics.md)
