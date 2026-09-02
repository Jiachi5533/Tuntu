# Tuntu v0.1 运维指南

## 1. 部署边界

官方部署形态是单实例、单进程 Docker。API、Jinja 页面、APScheduler 和下载轮询器运行在同一进程，SQLite 位于 `/data/tuntu.db`。不要启动多个副本或多个 worker 共享同一数据库。

Compose 的 `TZ` 控制容器系统时间显示。Tuntu 的调度时区默认由数据库设置页管理；只有明确传入 `TUNTU_TIMEZONE` 时才会形成环境覆盖，避免 Compose 默认值遮盖页面保存值。

容器使用 UID/GID `10001:10001`、丢弃 Linux capabilities，并以只读根文件系统运行。使用宿主机 bind mount 时，先确保目录由该 UID/GID 可写；Compose 默认命名卷无需额外处理。

## 2. 首次启动

```bash
docker compose up -d --build
docker compose logs tuntu
```

日志会输出一个 30 分钟有效的一次性 Setup Token。打开 `http://主机:8000` 创建唯一管理员。Token 使用后立即失效；容器重启不会重复打印仍有效 Token。

忘记密码时：

```bash
docker compose exec tuntu tuntu reset-setup-token
```

命令只生成新的短期 Setup Token，不生成或输出密码。使用它重置密码后，所有旧会话立即失效。

## 3. CloudDrive2

推荐在 CloudDrive2 中创建受限 API Token，并至少授予：

- 服务健康/版本读取；
- 目标目录列表与属性读取；
- 创建文件夹；
- 添加离线任务；
- 查询离线任务。

Tuntu 不会创建 Token 或扩大权限。连接测试会验证认证、测试目录创建和目录读取；“添加离线任务”仍需使用你有权下载的合法公开测试 magnet 完成最终验收。

Endpoint 必须包含协议、主机和端口，例如 `grpc://clouddrive2:19798` 或 `grpcs://nas.example:19798`。全局根目录是当前凭据通过 API 看到的路径；若受限 Token 已将目标目录映射为 `/`，根目录就填写 `/`，不要再次拼接宿主绝对路径。

每个任务最终保存到：

```text
全局根目录 / Profile 子目录 / 完整 BTIH
```

完整 BTIH 子目录用于隔离批量任务和可靠归属完成证据，不是用户路径模板。

## 4. Provider 代理

“Provider 出站代理”支持 HTTP 和 SOCKS5，只传给公开互联网 Provider。CloudDrive2 gRPC 不继承该设置，因此局域网下载器不会被 Tuntu 的 Provider 代理转发。

各来源可能因地区、站点变更、限流或访问挑战临时失效。一个来源失败不会丢弃其他来源结果；连续失败只记录健康状态，不会静默禁用。

## 5. 状态排查

- `submitted`：CD2 已接受，不代表下载完成。
- `downloading`：任务 API 或文件变化提供了可靠进展证据。
- `completed`：任务 API 已完成，或新增/变化文件经过至少两次稳定观察。
- `failed`：CD2 明确拒绝或任务明确失败。
- `attention_required`：超过阈值仍无法可靠判断，需要人工检查。

人工完成必须在页面二次确认，事件会标为“人工操作”。强制重提会创建新代次并关联旧任务；进行中的任务如果仍占用同一隔离目录，系统会优先拒绝不安全的并发重提。

## 6. 健康与日志

- `/health`：进程存活。
- `/ready`：数据库可用，并返回 CD2 运行时是否已配置。
- `/data/logs/tuntu.log`：按天轮转，保留最多 30 份。

日志会脱敏 Authorization、Cookie、密码和 API Token。Setup Token 是首次初始化的必要例外，只在生成时显示一次。

## 7. 备份与恢复

升级迁移前，Tuntu 自动使用 SQLite backup API 生成一致备份到 `/data/backups`，最多保留 3 份。完整人工备份应停止容器并复制整个 `/data`。

```bash
docker compose stop tuntu
docker run --rm \
  -v tuntu_tuntu-data:/data:ro \
  -v "$PWD":/backup \
  alpine tar -C /data -czf /backup/tuntu-data.tar.gz .
docker compose start tuntu
```

恢复时先停止 Tuntu，将备份解压到一个空数据卷，再启动并检查 `/ready`。不要将新版本数据库直接交给旧版本程序；v0.1 不支持数据库降级。

## 8. 升级

1. 备份 `/data`。
2. 拉取或构建目标镜像。
3. 重建单个 Tuntu 容器。
4. 检查 `/ready`、迁移日志、订阅计划和最近下载状态。

迁移在数据库同目录临时副本上执行，验证后原子替换。迁移失败时应用拒绝启动，原数据库保持不变。

## 9. HTTPS 与公网

目标环境是可信局域网，但仍建议通过 Caddy、Traefik 或 Nginx 提供 HTTPS，并设置 `TUNTU_COOKIE_SECURE=true`。不要直接暴露到公网；v0.1 没有多因素认证、IP 白名单或公网 SaaS 防护承诺。
