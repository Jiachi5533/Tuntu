# M1 外部依赖能力矩阵

- 探测日期：2026-08-13
- 状态：已完成
- 原则：只访问公开端点或现有登录页面；不输出标题、magnet、账号、Token 或原始响应

本文件中的地址、目录和版本只记录本次开发环境实测，不是 Tuntu 默认值。产品必须从设置页/SQLite或环境变量读取 endpoint、认证、TLS、根目录、Profile 子目录和轮询策略。

## 1. CloudDrive2

### 实例与接口

| 项目 | 结果 | 结论 |
|---|---|---|
| Web/gRPC 地址 | 已脱敏的局域网主机与自定义端口 | 同一端口提供 Web UI 与 gRPC；实际地址不进入公开仓库 |
| 实例版本 | CloudDrive2 1.0.14，CloudAPI 1.0.14，WebUI 3.0.14 | 与当前官方 proto 1.0.14 一致 |
| 公共 gRPC | `GetSystemInfo` 调用成功 | gRPC 可达 |
| 登录/就绪 | 已登录、`SystemReady=true`、无系统错误 | 可以继续授权探测 |
| REST/OpenAPI | 常见 `/api`、`/openapi.json`、`/docs` 均不存在 | Tuntu 应使用官方 gRPC，不应猜测私有 REST |
| API Token | 已创建仅限专用测试根目录的最小权限 Token；M1 初始权限允许列出文件、查看属性、查看运行时信息、添加和列出离线下载，RC1 复验时补充创建文件夹 | Bearer Token 鉴权可用；Token 值未写入仓库或本文档。RC1 只增加当前实现必需的 `CreateFolder` 最小权限 |
| Token 路径语义 | Token 的宿主根目录在 gRPC 中映射为 `/`；再次传宿主绝对路径会得到 `NOT_FOUND` | 下载器必须使用 Token 视角的虚拟路径，不能把宿主根目录重复拼接到请求路径 |
| 授权读取 | `GetRuntimeInfo` 和空测试根目录的 `GetSubFiles("/")` 成功 | 连接、鉴权、最小目录读取权限均通过 |

### 离线下载能力

官方 proto 已提供：

- `AddOfflineFiles(AddOfflineFileRequest)`：接收 `urls`、`toFolder`、`checkFolderAfterSecs`。
- `ListOfflineFilesByPath(FileRequest)`：按目录列出离线任务。
- `ListAllOfflineFiles(OfflineFileListAllRequest)`：按云盘和路径分页列出任务。
- `OfflineFile`：包含 `infoHash`、`status`、`size`、`percendDone`、`peers`、`fileId`。
- `OfflineFileStatus`：`INIT`、`DOWNLOADING`、`FINISHED`、`ERROR`、`UNKNOWN`。

这意味着 CD2 的公开 API 在协议层面能够支持 Tuntu 的 `submitted/downloading/completed/failed` 映射。`AddOfflineFiles` 只返回 `FileOperationResult`，没有独立任务 ID，因此实现时应使用规范化 BTIH + 目标路径关联 `OfflineFile.infoHash`，不能依赖提交响应生成外部 ID。

### 2026-08-13 实机闭环

使用 WebTorrent 公布的合法开放影片测试 magnet，在专用空目录完成了真实 gRPC 联调：

| 行为 | 实测结果 | 适配结论 |
|---|---|---|
| 首次提交 | `AddOfflineFiles` 返回 `success=true`，不返回外部任务 ID或结果路径 | 本地任务先记录 `submitted`；使用 BTIH、目标目录和提交时间关联后续证据 |
| 文件可见 | 提交后根目录出现 1 个结果目录，目录内 3 个非空文件，总大小 276,445,467 字节 | `GetSubFiles` 可作为完成检测的数据面 |
| 稳定复查 | 间隔 10 秒再次强制刷新，目录项、文件数和总大小保持一致 | M1 已证明“至少两次观察稳定”路径可行；生产间隔必须使用配置值，不能写死 10 秒 |
| 重复提交 | gRPC 返回 `INTERNAL`，底层 115 错误码为 `10008`（任务已存在） | 适配器必须把该组合归一化为“外部已存在/需要反查”，不能当成普通未知异常或切换 BTIH |
| 已存在的另一测试任务 | 首次尝试返回同一 `10008` | 115 的重复判断不是 Tuntu 目录内的简单文件名判断 |
| `ListOfflineFilesByPath("/")` | 8 秒、30 秒和 60 秒超时均未返回 | 当前 115 实例不能把该 RPC 作为唯一完成信号；保留为可选信号并设置独立超时 |

本实例选择 **方案 B：提交响应 + 目标目录基线差异 + 文件集合和总大小稳定检测**。实现时至少保存提交前目录基线，并要求两次成功观察间文件集合、文件数和总大小均稳定；如果任务 API 可用，可作为更强的补充证据。一次目录出现不等于完成。

M6 为解决同一 Run 多任务共享目录的归属歧义，加入 `Profile 目录 / 完整 BTIH` 隔离目录和官方 `CreateFolder`。RC1 已使用补充最小权限后的受限 Token 完成连接测试和当前实现闭环。

### RC1 当前实现复验

2026-08-13 使用同一专用测试根和受限 Token 完成发布候选复验，全程不记录内网地址、Token、完整磁力或宿主路径：

| 行为 | 实测结果 |
|---|---|
| 连接与目录权限 | 当前凭据虚拟根为 `/`；连接测试成功创建并读取独立测试目录，`CreateFolder` 权限通过 |
| 磁力提交 | WebTorrent 合法开放影片被 CD2 接受；Tuntu 保存 tracker、webseed 与来源提示，并创建“Profile / 完整 BTIH”隔离目录 |
| 进度与完成 | 文件树先产生变化，任务进入 `downloading`；连续两次快照一致后自动进入 `completed` |
| 115 文件结果 | 隔离目录内实见 6 个文件、约 210.6 MB；完成事件来源为系统证据，未使用人工确认 |

这次复验补齐了 M1 当时尚未覆盖的创建目录权限和 M6 后目录布局，AC-09 已闭环。

### 留给 M5 的实现验证

- 用 Mock gRPC 覆盖明确拒绝、Deadline、传输失败和响应结果不明。
- 调研 `ListAllOfflineFiles` 在不同云盘及 Token 权限下是否比按路径接口稳定，但不得让它成为唯一完成依据。
- 把提交前基线、两次稳定观察和同目录并发歧义写成自动化测试；发生歧义时进入 `attention_required`，不能猜测完成。
- 使用配置的轮询间隔和超时阈值，不复用本次探针的 10 秒观察间隔。

### 官方依据

- [CloudDrive2 gRPC API 开发者指南](https://www.clouddrive2.com/api/CloudDrive2_gRPC_API_Guide.html)
- [CloudDrive2 官方 proto](https://www.clouddrive2.com/api/clouddrive.proto)
- [WebTorrent 官方测试种子](https://webtorrent.io/torrents/big-buck-bunny.torrent)
- [Blender：Big Buck Bunny 的 Creative Commons 许可说明](https://video.blender.org/w/pAQiVCgv2CsLg79KKXUoMw)

## 2. 数据源

探针命令只输出 HTTP 状态、响应大小、条目数量和字段名，不输出查询值、标题、番号或 magnet：

```bash
PYTHONPATH=src:. python3 scripts/probe_sources.py --query '<手工提供的内容标识>'
```

### 2026-08-13 实测

| 来源 | HTTP/类型 | 脱敏结构结果 | M1 结论 |
|---|---|---|---|
| JavDB 周榜（M4 复核） | 200 / HTML | 使用真实参数 `p=weekly` 获得 49 个唯一详情链接，无挑战页标记 | 可作为榜单源；M1 旧参数实际落到日榜，已修正 |
| JavDB 首个榜单详情 | 200 / HTML | 4 个候选行，8 个 magnet URI 标记 | 可作为候选源；需 Fixture 验证一行多链接语义 |
| JavDatabase Top Movies Feed | 200 / RSS | 10 个条目，29 个唯一番号形状 | 可作为周榜源 |
| Sukebei RSS | 200 / XML | 查询命中 4 条，均有 infoHash 和 seeders 字段 | 可作为候选源 |
| Knaben API v1 | 200 / JSON | 查询命中 4 条；含 hash、magnetUrl、bytes、seeders 等字段 | 可作为候选源 |
| Bitsearch API v1 | 200 / JSON | API 成功但本次查询 0 条；免费额度响应正常 | 接口可用、当前覆盖较弱，作为可选源 |

### 来源门槛结论

- 榜单源最低门槛“至少一个”已满足：JavDB、JavDatabase 均可访问。
- 候选源最低门槛“至少两个”已满足：JavDB 详情、Sukebei、Knaben 均返回候选结构。
- Bitsearch 不计入当前最低门槛，避免把“API 可访问”误写成“对目标内容有稳定覆盖”。
- 所有来源目前都无需登录、Cookie、验证码或浏览器自动化。

### 官方/来源依据

- [JavDatabase Top JAV Movies](https://www.javdatabase.com/category/top-jav-movies/)
- [Knaben API v1](https://knaben.org/api/v1/)
- [Bitsearch API](https://bitsearch.to/api)

JavDB 和 Sukebei 的探测直接针对来源页面/RSS；它们没有单独的公开 API 文档，因此后续实现必须依赖最小 Fixture、健康监测和结构变化失败测试。

> 后续更正：M1 初版 JavDB 探针使用 `period=weekly`，虽然返回 200，但页面实际选中日榜。M4 已改用并复核 `p=weekly`；本表数量更新为修正后的周榜结果。详见 `docs/M4-provider-matrix.md`。

## 3. 当前 M1 结论

- **来源可行性：通过。** 已有两个榜单路径和三个候选路径可进入 M4 设计。
- **CD2 协议可行性：通过。** 官方 gRPC 提供提交、任务查询和文件树读取能力。
- **CD2 实机闭环：通过（方案 B）。** 最小权限 Token 已完成真实提交、文件稳定验证和重复提交行为验证；任务列表 RPC 在当前 115 实例持续超时，已明确降级策略。

M1 硬门槛已满足，可以进入 M2；M5 仍须把上述边界写入 Mock、集成和手工 live 测试。
