# M7 认证、设置、API 与安全验收矩阵

- 状态：已完成（2026-08-13）

| 能力 | 实现 | 自动化证据 |
|---|---|---|
| 一次性 Setup Token | 仅保存 SHA-256 哈希，短期有效，消费后失效 | `test_auth.py`、`test_api.py` |
| 管理员密码 | Argon2id，12–256 位 | `test_auth.py` |
| 会话 | 随机令牌哈希保存，7 天滑动过期，可撤销 | `test_auth.py` |
| 密码重置 | CLI 只生成新 Setup Token；重置后撤销旧会话 | `test_cli.py`、`test_auth.py` |
| 写请求保护 | 同源 Origin 或自定义 CSRF Header；无 CORS | `test_api.py` |
| Cookie | HttpOnly、SameSite=Lax，可配置 Secure | `test_api.py` |
| 设置优先级 | SQLite 事实来源，显式环境字段覆盖 | `test_settings.py` |
| 秘密遮蔽 | GET/API/OpenAPI 不含秘密值；日志过滤 | `test_settings.py`、`test_api.py`、`test_logging_setup.py` |
| Profile API | 创建、编辑、归档、恢复、运行、分页 | `test_profiles.py`、`test_api.py` |
| Run/Download/Source API | 分页、详情、事件、重试、人工完成、健康测试 | Repository 与 API 测试 |
| 手工 API | 番号候选、magnet 预览、全局重复和强制确认 | `test_manual.py` |
| CSV | UTF-8 BOM、约定字段、公式前缀转义 | `test_api.py` |
| 健康检查 | `/health`、数据库 `/ready`、运行时状态 | `test_api.py` |

最终发布候选全量回归与结果以 `docs/M9-release-checklist.md` 为准。
