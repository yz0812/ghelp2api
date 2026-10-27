# GHelper Clash Web

将 GHelper 浏览器扩展的 HTTPS 代理节点转换为可自动更新的 Clash / mihomo 订阅。基于现有链路说明实现，服务运行时不依赖浏览器或 PowerShell。

支持个人管理员登录、多 token 导入、账号资料、完整 PAC 节点提取、连通性测速、独立 / 聚合订阅、Windows Chrome / Edge token 提取、Docker 部署与 GHCR 自动发布。

## 本地启动

需要 Python 3.12–3.14、Node.js 24 和 curl。Windows 10/11 自带 curl；Linux 安装 curl 与 CA 证书。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock
.\.venv\Scripts\python -m app.cli init
npm --prefix frontend ci
npm --prefix frontend run build
.\.venv\Scripts\python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Linux/macOS 使用 `.venv/bin/python` 替换上面的 Python 路径。访问 **http://localhost:8000**，用户名默认为 `admin`，密码在初始化生成的 `.env` 中。初始化命令不会覆盖已有配置，也不会在日志中输出密码。

开发前端时运行 `npm --prefix frontend run dev`，Vite 会将 `/api` 和 `/sub` 转发给本机 8000 端口。后端需独立运行。

### 使用流程

1. 添加账号：每行一个 32 位 token，也可使用 `名称,token`，或导入本地提取脚本的 JSON。
2. 点击“刷新全部”，在账号卡片查看账号资料与节点获取结果。
3. 在节点列表选择账号、入口或节点，按需启动测速。
4. 在账号卡片生成独立订阅，或在订阅管理勾选多个账号创建聚合订阅。
5. 复制 Clash 地址至客户端的远程订阅；建议客户端每 30–60 分钟更新。

节点的延迟是**Web 服务所在网络**经该代理访问 HTTPS 204 检测地址的请求耗时，不是带宽，也不是你的 Clash 客户端本机延迟。国内入口代号不代表海外出口地区。

## Docker Compose

先使用上面的初始化命令生成 `.env`，然后：

```sh
docker compose up -d --build
docker compose logs -f
```

默认只绑定 `127.0.0.1:8000`，数据保存在 `ghelper-data` 命名卷中。容器运行用户 UID 为 `10001`，若改成宿主机目录挂载，需要让该用户拥有写权限。

远程部署时，在 `.env` 中设置 `PUBLIC_BASE_URL=https://你的域名`、`COOKIE_SECURE=true`，并用反向代理转发到 8000 端口；本地 HTTP 使用 `COOKIE_SECURE=false`。如果直接对外监听，可设置 `BIND_ADDRESS=0.0.0.0`。`PUBLIC_BASE_URL` 是生成订阅链接使用的地址，不能继续保留 localhost。

使用 GHCR 镜像时，设置 `GHELPER_IMAGE=ghcr.io/<owner>/<repo>:latest`：

```sh
docker compose pull
docker compose up -d --no-build
```

需要回滚时，将镜像标签改成前一个版本后重新启动。数据库升级由 Alembic 在启动时执行；版本升级前备份数据卷及 `.env`。

### 环境变量

| 变量 | 说明 |
|---|---|
| `ADMIN_USERNAME` | 管理员账号，默认 `admin` |
| `ADMIN_PASSWORD` | 至少 12 位，初始化随机生成 |
| `ADMIN_API_KEY` | 至少 32 位，供程序调用管理 API |
| `ENCRYPTION_KEY` | Fernet 加密密钥，必须与数据库一起备份，禁止随意更换 |
| `DATA_DIR` | 本地默认 `./data`；容器固定 `/data` |
| `PUBLIC_BASE_URL` | 实际对外访问地址，用于生成订阅 URL |
| `COOKIE_SECURE` | HTTPS 部署设为 `true` |
| `API_HOSTS` | 逗号分隔的上游域名，不含协议和路径；默认沿用文档四个域名 |
| `EXTENSION_VERSION` | 上游请求版本，默认 `2.9.3` |
| `SCHEDULER_ENABLED` | 默认 `true`，测试环境可关闭自动获取 |

Compose 当前透传基础部署参数。调整上游域名或扩展版本时，在 Compose 的 `environment` 中增加对应变量。

必须使用 **单 worker、单实例**；刷新协调和测速并发控制位于进程内。无需 Redis 或额外数据库。`/healthz` 检查进程，`/readyz` 检查数据库，两者不会调用 GHelper。

## 直接通过接口获取配置

认证使用 `Authorization: Bearer <ADMIN_API_KEY>`。网页登录使用 Cookie + CSRF；程序请求使用 Bearer 时不需要 CSRF。

```sh
# 批量添加 token；令牌仅放在请求体中
curl -X POST http://localhost:8000/api/v1/accounts/import \
  -H "Authorization: Bearer <ADMIN_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"accounts":[{"name":"账号 A","token":"<32位token>"},{"name":"账号 B","token":"<32位token>"}]}'

# 提交刷新任务，返回 job_id
curl -X POST http://localhost:8000/api/v1/accounts/all/refresh \
  -H "Authorization: Bearer <ADMIN_API_KEY>"

# 查询进度
curl http://localhost:8000/api/v1/jobs/<job_id> \
  -H "Authorization: Bearer <ADMIN_API_KEY>"

# 直接获取完整 Clash 配置；format=proxies 可只获取节点
curl "http://localhost:8000/api/v1/config/clash?account_ids=<账号ID1>,<账号ID2>&format=full" \
  -H "Authorization: Bearer <ADMIN_API_KEY>" -o ghelper-clash.yaml

# 创建永久订阅地址
curl -X POST http://localhost:8000/api/v1/subscriptions \
  -H "Authorization: Bearer <ADMIN_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"name":"聚合订阅","account_ids":["<账号ID1>","<账号ID2>"],"regions":[],"exclude_failed":false}'
```

最后一个请求返回 `clash_url` 和 `proxies_url`。订阅地址不需要管理登录，使用独立随机密钥授权；轮换密钥、删除或停用订阅后，旧地址失效。

```yaml
# 将 proxies_url 用于已有 mihomo 配置
proxy-providers:
  ghelper:
    type: http
    url: "https://你的域名/sub/<订阅密钥>/proxies.yaml"
    path: ./providers/ghelper.yaml
    interval: 1800
    health-check:
      enable: true
      url: https://www.gstatic.com/generate_204
      interval: 300
proxy-groups:
  - name: GHelper
    type: select
    use: [ghelper]
```

管理接口统一在 `/api/v1` 下；登录后可访问 `/api/docs` 或 `/api/v1/openapi.json` 查看完整接口。Swagger 静态资源由 FastAPI 默认 CDN 提供，离线时仍可直接读取 OpenAPI JSON。Swagger 中可点击 Authorize 填入 ADMIN_API_KEY 调用写操作，也可使用上述 curl 示例。

## 获取策略与信息口径

- 默认每 30 分钟询问上游；页面可调整为 5–30 分钟。同一账号的请求串行执行，重复刷新合并。
- `pk` 改变才表示发现新会话。页面分开展示会话首次发现时间与最近成功获取时间；文档中的约 180 分钟不是精确有效期保证。
- 上游失败时，只允许输出最近 60 分钟内成功确认的快照；账号部分失败时仍输出其他账号，全部没有节点时返回 HTTP 503，不输出空配置。
- 上游 `set_token` 会自动更新加密凭证；`clear_token` 或认证失败会撤下节点并要求重新导入。浏览器本地存储不随服务端更新。
- 默认导出所有当前节点，包括未测速节点；勾选过滤后，仅排除最近 30 分钟测速失败的节点。
- 账号资料通过插件对应接口读取。邮箱、会员日期、浏览器流量仅在上游提供时展示；不把缺失额度当成 0，不混用浏览器与客户端流量口径。
- 解析降级时显示“仅解析主节点”。上游 PAC 命名格式变更时可能需要调整解析器。
- 原方案节点使用 `skip-cert-verify: true`；仅节点代理证书采用此兼容设置，上游 API 和测速目标仍验证证书。
- 服务不执行 PAC JavaScript，不执行上游下发的任意测速 URL；本地 curl 测速拒绝私有地址并固定解析结果。

## Windows Chrome / Edge token 提取

脚本独立运行，不需要启动 Web 服务，不会自动上传或修改浏览器数据。

```powershell
.\.venv\Scripts\python -m pip install -r requirements-local.txt

# 自动发现 Chrome 和 Edge 的多个 Profile；默认只显示脱敏 token
.\.venv\Scripts\python scripts\extract_browser_tokens.py

# 导出完整 token，随后在管理台导入；拒绝覆盖已有输出文件
.\.venv\Scripts\python scripts\extract_browser_tokens.py --output tokens-export.json

# 显式显示完整值
.\.venv\Scripts\python scripts\extract_browser_tokens.py --browser edge --profile "Profile 1" --show-secrets

# 自定义用户数据路径 / 扩展 ID
.\.venv\Scripts\python scripts\extract_browser_tokens.py --browser chrome --user-data-dir "D:\ChromeProfile" --extension-id cieikaeocafmceoapfogpffaalkncpkc
```

使用共享读取复制临时快照，根据 CURRENT / MANIFEST 选取有效表文件与 WAL，通过记录序列号和删除标记选择当前 token，验证 CRC32C，支持 Snappy 压缩表。快照持续变化或损坏时报告错误，不输出历史凭证。临时文件会在结束时清理。

解析库固定到上游提交，避免 PyPI 旧版本固定 `python-snappy==0.6.1` 带来的 Windows 编译问题。`zstd` 依赖在部分 Python/Windows 组合上需要 C++ Build Tools；当前 Python 3.14 环境已完成依赖安装验证。退出码：0 成功，1 未找到 token，2 提取或导出失败。

## 测试与 GitHub Actions

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check app scripts tests migrations
npm --prefix frontend run build

# 浏览器端到端测试：使用临时数据库与合成账号，不访问真实上游
$env:GHELPER_PYTHON = (Resolve-Path .venv\Scripts\python.exe).Path
$env:PLAYWRIGHT_CHROME_CHANNEL = 'chrome'
npm --prefix frontend run test:e2e
```

Linux CI 使用 Playwright Chromium。执行 `python scripts/validate_mihomo.py --download` 可下载固定版本 v1.19.33 的官方 mihomo 并校验生成配置（首次运行还需下载 GeoIP 数据）；该检查已接入 CI。测试覆盖 token 轮换与清除、缓存有效期、配置生成、鉴权、订阅撤销、LevelDB 日志/表文件、浏览器流程和响应式布局。

GitHub Actions 工作流包含 Windows/Linux 的 Python 3.12/3.14 测试、前端构建与浏览器测试、镜像启动与数据卷持久化测试，然后构建 amd64/arm64 镜像。PR 仅构建，主分支和 `v*` 标签自动发布，支持手动触发。GitHub Actions 和上游解析库使用固定提交。

推送项目到 GitHub 后启用 Actions，镜像名称自动取 `ghcr.io/<owner>/<repo>`，不需要手填 Docker Hub 凭据。如果仓库组织策略限制 Packages 写入，需要管理员允许工作流使用 `packages: write`。GHCR 私有包拉取需要认证，公开使用可在包设置中调整可见性。

不要将 `.env`、数据库、token 导出、现有实际节点 YAML 或 `.claude.json` 提交到仓库。项目已配置 Git 忽略及 Docker 白名单构建上下文。反向代理访问日志也应隐藏 `/sub/` 路径中的密钥。
