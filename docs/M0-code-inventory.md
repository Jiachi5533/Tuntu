# M0 pre-alpha 代码盘点

- 盘点日期：2026-08-13
- 对照范围：`docs/PRD-v0.1.md`
- 结论：现有代码是可运行的领域草图，不是 v0.1 实现基线

## 当前基线

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

盘点当时共有 3 个测试，均通过。它们验证了榜单/候选合并、可解释规则和早期路由概念。

M2 完成后共有 19 个测试：旧 qB/private torrent 路由测试已删除，新增内容身份、BTIH、来源证据、三态规则、未知值、稳定排序、Top 1、`no_candidate` 与 `filtered` 测试。本文件其余表格保留为当时的改造依据。

## 文件盘点

| 文件 | 可保留思路 | v0.1 必须调整 |
|---|---|---|
| `src/tuntu/models.py` | 内容、候选、评估、收据的分离 | 增加命名空间、原始/规范化标识、证据和三态；体积/做种数改为可选；移出 torrent/url 首版行为 |
| `src/tuntu/contracts.py` | `DiscoverySource`、`CandidateSource`、`Rule`、`DownloadClient` 边界 | 接口改为异步能力、错误分类、健康检查和完成验证；不做动态插件加载 |
| `src/tuntu/pipeline.py` | 多来源合并和规则原因 | 按 ContentItem 分组候选；只选 Top 1；隔离来源失败；移除“提交所有合格候选”行为 |
| `src/tuntu/rules.py` | 小型可组合规则 | 实现三态真值表、未知值语义和中文可读原因 |
| `src/tuntu/routing.py` | 下载客户端与领域管线分离 | v0.1 只有固定 CD2 目标，不实现复杂 Route 列表 |
| `tests/test_pipeline.py` | 离线、快速、行为导向 | 用 v0.1 的 magnet/Top 1/三态/去重测试替换 qB/private torrent 场景 |
| `pyproject.toml` | 最小 Python 包基线 | 按里程碑逐步增加运行与测试依赖，不一次性堆入全部框架 |

## 已发现的范围冲突

- `TransferKind` 当前包含 `torrent` 和 `url`。
- 现有路由测试把 private torrent 提交到 qBittorrent。
- `Pipeline.submit()` 当前会提交所有合格 Candidate，而非每个 ContentItem 的 Top 1。
- `size_mb=0` 和 `seeders=0` 把未知值与真实零值混为一谈。
- Pipeline 当前没有来源失败隔离、配置快照、持久化或跨 Profile 全局幂等。

这些冲突在 M2/M3/M5 对应测试建立后修正；M0 不直接重构核心，避免在 CD2 与来源可行性结论出来前做无依据的大改。

## M0 已对齐的公开边界

- README 只把 CloudDrive2 → 115 列为 v0.1 下载路径。
- 架构说明明确 Run 与 DownloadTask、内容身份和完成语义。
- 删除会误导用户的动态插件/Cron/qB YAML 示例，改为 `.env.example` 启动与秘密覆盖示例。
- qBittorrent、PT、`.torrent`、通用来源和复杂路由只出现在明确的未来/非目标说明中。

## 下一步约束

- M1 已只做 CD2 与公开来源探针，没有借机实现 Web UI。
- M2 已先写失败测试，再删除 `TransferKind`、复杂 Route 和提交所有候选的旧行为。
- 任何新的首版行为都必须能映射到 PRD 验收场景。
