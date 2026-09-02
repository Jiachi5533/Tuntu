# M6 Run、调度与恢复矩阵

- 完成日期：2026-08-13
- 状态：已完成
- 默认测试：离线 Provider、Mock CD2、内存调度后端和临时 SQLite

## 1. Run 编排

Run 从 Profile 创建不可变配置快照，依次执行榜单合并、Top N 截断、候选查询、BTIH 合并、规则评估、稳定排序、去重和可选提交。每个内容持久化排名、候选证据、Evaluation 和最终结果；Run 在提交阶段结束后立即终止，不等待外部下载。

| 场景 | Run 结果 |
|---|---|
| 全部来源正常，包括空榜单、无候选或全部过滤 | `success` |
| 任一来源、条目或提交失败，但榜单仍可处理 | `partial` |
| 所有榜单源失败或配置/初始化失败 | `failed` |
| dry-run / 强制预演 | 完整保存发现与评估结果，CD2 调用数为 0 |
| 自动模式 | 每个内容只提交排序第一的合格候选 |
| 第一候选被明确同步拒绝 | 保存失败任务后尝试下一合格候选，Run 为 `partial` |
| 结果未知、外部已存在或接受后失败 | 不切换 BTIH，Run 为 `partial` |
| 跨 Profile 内容/BTIH 重复 | 记录去重，Run 可正常 `success` |

公网 HTTP 的 Run 内去重和短 TTL 跨 Run 缓存继续复用；并发请求由客户端锁保护，避免相邻 Run 同时击穿缓存或绕过来源速率限制。来源健康使用 SQLite 原子 UPSERT。

## 2. 批量下载目录归属

共享 Profile 目录时，第一个任务尚未出现文件之前无法安全释放 M5 的目录锁，直接释放又可能让一个任务的文件把另一个任务误判完成。因此每个任务使用：

```text
用户配置的 CD2 根目录 / Profile 子目录 / 完整 BTIH
```

目录仍位于用户配置边界内，完整 BTIH 是内容中立且无路径转义风险的稳定身份。CD2 客户端使用官方 `CreateFolder` 逐级创建缺失目录，并处理并发创建后刷新可见的情况。Token 必须具备创建文件夹权限；不具备时任务明确失败，不回退到共享目录猜测。

## 3. 每日调度与并发

- Profile 保存启用状态和每日 `HH:MM[:SS]` 时间；归档、禁用、无时间或非法时间的 Profile 不进入计划。
- APScheduler 固定在稳定的 3.x，使用进程内 JobStore；SQLite Profile 是计划事实来源。
- 启动、Profile 变更和时区变更时重建未来 CronTrigger，不执行一次“补偿运行”。停机期间错过的计划不补跑。
- 每个 Profile Job 使用 `max_instances=1`，RunService 另有进程锁；SQLite 对同 Profile 的 `running` Run 建立部分唯一索引。
- 不同 Profile 可并行，但共享可配置的全局并发上限。
- 重叠触发、全局上限和非法计划都会写脱敏审计事件。

## 4. 下载轮询与进程恢复

轮询 Job 按配置间隔从 SQLite 重新读取所有未结束任务：`submitting`、`submitted`、`downloading` 和 `attention_required`。单个任务轮询异常只写通用错误审计，不中断其他任务，也不把异常正文写入审计。

应用启动时：

1. 把上次进程遗留的 `running` Run 结束为 `failed/process_interrupted`；
2. 从当前 Profile 重建未来每日计划；
3. 后续轮询自动接续数据库中的未结束 DownloadTask。

数据库从 M5 升级时也先结束遗留 `running` Run，再建立单 Profile 运行唯一索引，避免升级失败或伪造仍在运行的状态。

## 5. 验证结果

- 109 项离线测试通过，另有 31 项参数化子测试。
- 覆盖 dry-run、Top N/Top 1、多内容批量提交、明确拒绝回退、结果未知、外部重复、来源 partial/failed、跨 Profile 去重、同 Profile 互斥、不同 Profile 并行、缓存并发、计划同步、时区重建、无补跑策略、迁移和下载恢复。
- M6 未访问成人站点，也未向真实 CD2 创建新任务。

M7 将把这些服务接入首次设置、秘密覆盖、同源 REST API 和管理员认证。
