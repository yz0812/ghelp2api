# GHelper → Clash 链路说明

> 适用对象：Chrome 扩展 GHelper（`ghelper.net`）v2.9.3
> 文档基于对扩展源码、Chrome 本地存储、官方 API 的实际抓取与验证
> 最后更新：2026-10-26

---

## 1. 结论速览

GHelper 不是 SS/VMess/Trojan 这类协议，它下发的是 **PAC 脚本**，脚本里写的是标准 **HTTPS 代理**（HTTP CONNECT over TLS）地址。因此这些节点可以被 Clash 直接使用，对应配置为 `type: http` + `tls: true`。

| 项目 | 结论 |
|---|---|
| 协议 | HTTPS 代理（CONNECT over TLS），非加密隧道协议 |
| 认证 | PAC 模式下**无认证**，靠域名中的 UUID 识别 |
| 单次会话节点数 | 29 个（PAC 里 `var proxy` 只启用 3 个，其余在 `rule_list` 分流规则里） |
| 节点有效期 | 约 180 分钟，随会话轮换 |
| 能否脱离 Chrome | 能，持有 token 即可直接调官方 API |
| Clash 兼容性 | 完全兼容（mihomo / Clash Meta / Clash Verge） |

---

## 2. 目标

把 GHelper 浏览器插件里的代理节点提取为 Clash 配置，使 Clash 能统一管理这些出口，而不是被限制在浏览器内。

---

## 3. 插件工作机制

### 3.1 扩展基本信息

| 字段 | 值 |
|---|---|
| 扩展 ID | `cieikaeocafmceoapfogpffaalkncpkc` |
| 名称 | GHelper |
| 版本 | 2.9.3 |
| 主页 | `https://ghelper.net` |
| 权限 | `management`、`proxy`、`alarms`、`storage` |
| 后台 | `sw.js`（Manifest V3 service worker） |
| 安装路径 | `%LOCALAPPDATA%\Google\Chrome\User Data\Default\Extensions\cieikaeocafmceoapfogpffaalkncpkc\2.9.3_0\` |

> 注意 `management` 权限：扩展启动时会遍历并**禁用其他所有带 `proxy` 权限的扩展**（`removeOtherApps()`），白名单里只有 IDM。这意味着它和别的代理类扩展不能共存。

### 3.2 完整请求链路

```
┌─────────────┐
│  GHelper    │  登录，拿到 token（32 位 hex）
│  服务端     │
└──────┬──────┘
       │ ① POST /api2/session   Header: token
       │    Body: {"tester":{}}
       ▼
┌─────────────────────────────────────────────┐
│  扩展 sw.js                                  │
│  asyncSession()                              │
│  每 180 分钟（chrome.alarms）触发一次         │
└──────┬──────────────────────────────────────┘
       │ ② 响应 JSON（约 40 KB）
       │    session.proxy.pac.value.pacScript.data  ← PAC 脚本
       │    session.proxy.pk                        ← 会话密钥
       ▼
┌─────────────────────────────────────────────┐
│  两条落地路径                                 │
│                                              │
│  a) chrome.proxy.settings.set()  → 生效       │
│  b) chrome.storage.local.set({proxy})         │
│     → 明文写入 leveldb                        │
└──────┬──────────────────────────────────────┘
       │
       ▼
  %LOCALAPPDATA%\Google\Chrome\User Data\Default\
  Local Extension Settings\cieikaeocafmceoapfogpffaalkncpkc\000003.log
      （leveldb 写入日志，明文，可共享读）
```

### 3.3 关键代码位置

| 函数 | 作用 |
|---|---|
| `asyncSession()` | 请求 `/session`，拿回 PAC |
| `setChromeSettings()` | 把 PAC 交付给 `chrome.proxy`，同时存 storage |
| `buildPacScript()` | 把 PAC 里的 `<cnips>` 占位符替换成本地 geoip 列表 |
| `makeTmpUrl()` | 把 API 域名里的 `*` 替换成随机 UUID，规避 DNS 污染 |
| `lookupDohHosts()` | DoH 查询 `v4.ghelper.net` 的 TXT 记录，获取备用 API 域名 |
| `removeOtherApps()` | 禁用其他带 proxy 权限的扩展 |

API 域名池：

```js
const DEFAULT_API = [
  "https://*.gheapi.com/api2",
  "https://*.apihuawei.com/api2",
  "https://*.plebvps.com/api2",
  "https://*.apikuaishou.com/api2"
];
const DOH_DOMAIN = "v4.ghelper.net";
```

---

## 4. 节点到底是什么

### 4.1 PAC 结构

服务端返回的 PAC 脚本开头就是节点定义：

```js
var proxy="HTTPS <uuid>-<code>-tp1cgt-w7lw.<region>.<cdn>:<port>;
           HTTPS ...;
           HTTPS ...";
```

- `var proxy` —— 供 `FindProxyForURL` 直接返回的**主力节点，只有 3 个**（1 主 2 备）
- `rule_list` —— 按域名分流的规则，每条规则的 `t` 字段里也可能带节点，**其余节点都在这里**

这就是"明明有很多节点，脚本却只抓到 3 个"的原因：只解析了 `var proxy`，没扫 `rule_list`。

### 4.2 节点域名格式

```
61d907af - tnn6o0 - tp1cgt - w7lw . fscm8 . cdnkuaishou.com : 15228
   │          │         │        │      │            │          │
   │          │         │        │      │            │          └─ 端口
   │          │         │        │      │            └─ 伪装成快手/华为云 CDN
   │          │         │        │      └─ 地区/入口代号
   │          │         │        └─ 固定串
   │          │         └─ 固定串
   │          └─ 会话代号（6 位，每次会话不同）
   └─ 节点标识（每次会话重新签发）
```

**提取节点的核心锚点就是这 6 位会话代号**：先用 `var proxy` 拿到代号，再用代号全文扫描 PAC，即可得到整池节点。

### 4.3 常见地区/入口代号

| 代号 | 说明 | 典型延迟 |
|---|---|---|
| `hkt` / `hk` | 香港 | 100–450 ms |
| `gzcm1` / `fscm8` | 广州、佛山移动（**国内接入入口，非出境**） | 200–1800 ms |
| `cm9` / `cisp` / `viphuawei` | 国内接入点 | 350–1700 ms |
| `los` | 洛杉矶 | ~1200 ms |
| `sj7` | 圣何塞 | ~2300 ms |
| `sea2` | 新加坡 | ~2100 ms |
| `de` / `fr` / `it` / `es` | 德国 / 法国 / 意大利 / 西班牙 | 900–3000 ms |
| `ru` / `ca` / `mx` / `au` / `in` | 俄罗斯 / 加拿大 / 墨西哥 / 澳大利亚 / 印度 | 1200–2300 ms |

> `gzcm1`、`fscm8` 这类不是海外出口，而是把你接入 GHelper 网络的国内中转点。延迟低是因为物理距离近。

### 4.4 协议与认证

**协议**：HTTPS 代理。Chrome 的 `chrome.proxy` API 只支持 HTTP / HTTPS / SOCKS4 / SOCKS5，所以绕过扩展直接连接是可行的。

**认证**：PAC 模式下**不需要用户名密码**。实测：

```bash
curl --proxy https://<节点域名>:<端口> --proxy-insecure https://www.gstatic.com/generate_204
# → HTTP 204
```

服务端返回的 `ghelper_proxy_auth.authCredentials`（用户名/密码）是给 webtable 模式用的，PAC 路径用不上。

---

## 5. 三个时间尺度

这是整套方案里最容易搞混的地方。

| 对象 | 周期 | 触发方 | 证据 |
|---|---|---|---|
| **token** | **无固定周期** | 登录写一次；之后仅当服务端响应带 `set_token` 才换、带 `clear_token` 才清 | Chrome 存储里 15 天只写入过 1 次（文件偏移 604） |
| **session（节点池）** | **180 分钟** | `chrome.alarms` 定时；手动断开重连也会立即触发 | 37 份 PAC 快照 |
| **pk（pac_key）** | 跟随 session | 每次会话刷新换一个 | 37 个不同值 |

### token 的写入逻辑

```js
// SecureLoader-DM3e3skq.js
async function Qm(e){
  if (e.set_token)   await Gm({token: e.set_token});                  // 服务端下发的
  if (e.clear_token) await chrome.storage.local.remove(["token"]);    // 服务端要求清除
  if (e.session)     await Xm(e.session);
}
```

客户端**从不主动刷新 token**。所以只要不退出登录、服务端不踢人，token 可以长期使用。

### 结论

- 抓节点**不需要每次重新登录**
- 180 分钟是**节点池**的寿命，与 token 无关
- 判断节点池是否已更新，看 `pk` 变没变最准

---

## 6. 两条提取路线

### 6.1 路线 A：读 Chrome 本地存储

**原理**：扩展把服务端下发的 PAC 原样存进 `chrome.storage.local`，Chrome 明文写入 leveldb。

**文件路径**：

```
%LOCALAPPDATA%\Google\Chrome\User Data\Default\Local Extension Settings\
    cieikaeocafmceoapfogpffaalkncpkc\000003.log
```

**读取方法**：

- 用 `FileShare.ReadWrite` 共享模式打开（Chrome 独占写但不阻止读），**不用关 Chrome**
- 按字节映射成 latin1 字符串，用正则扫描
- leveldb 是追加写，**文件末尾 = 最近一次写入**

**提取步骤**：

| 步骤 | 正则 | 作用 |
|---|---|---|
| 找快照 | `var proxy=\\"([^\\]*)\\"` | 定位所有历史 PAC |
| 取最新 | 按文件内偏移倒序 | 末尾即最新 |
| 定会话 | `-([a-z0-9]{6})-tp1cgt-w7lw` | 拿到 6 位会话代号 |
| 挖全池 | `HTTPS uuid-<code>-tp1cgt-w7lw.host:port` | 该代号下全部节点 |

**优点**：不需要网络，不消耗账号配额，几毫秒完成。
**缺点**：必须有 Chrome 运行且扩展曾连过；只能被动等新快照。

### 6.2 路线 B：直接用 token 调 API

**原理**：完全复刻扩展 `asyncSession()` 的请求，绕过浏览器。

**请求格式**：

```http
POST https://<随机UUID>.<api域名>/api2/session?token=<TOKEN>&version=2.9.3
Content-Type: application/json
token: <TOKEN>

{"tester":{}}
```

其中 `<随机UUID>` 是 `makeTmpUrl()` 的行为——把域名池里的 `*` 替换成随机 UUID，规避 DNS 污染。API 域名池自动轮询：

```
gheapi.com  /  apihuawei.com  /  plebvps.com  /  apikuaishou.com
```

**响应结构**（约 40 KB）：

```json
{
  "session": {
    "token": { "token": "...", "ghelper_proxy_auth": {...} },
    "is_pac_mode": true,
    "pac_proxy_id": 26,
    "tester": [ ... ],
    "proxy": {
      "pac": {
        "scope": "regular",
        "value": {
          "mode": "pac_script",
          "pacScript": { "data": "var proxy=\"HTTPS ...\"..." }
        }
      },
      "pk": "f664265e88867d7996c8cc5b91d499ab",
      "pac_encode": null
    }
  }
}
```

节点池就在 `session.proxy.pac.value.pacScript.data` 里，解析方式与路线 A 完全相同。

**关于"刷新"的真相**：服务端在会话窗口内会**复用**当前会话。连续两次调用返回同一个 `pk`，节点完全一样。真正换新只发生在：

- 服务端会话窗口到期
- 插件里断开重连
- 切换地区线路（会换 `pac_proxy_id`）

所以正确的用法是定期询问，服务端自己决定给不给新的，不需要猜窗口长度。

### 6.3 路线对比

| | 路线 A（读存储） | 路线 B（调 API） |
|---|---|---|
| 依赖 Chrome 运行 | 是 | **否** |
| 依赖扩展在线 | 是 | **否** |
| 主动触发 | 做不到 | 可以主动问 |
| 数据新鲜度 | 上次落盘的会话 | 服务端当前有效会话 |
| 前置条件 | Chrome + 已登录扩展 | **仅需 token** |
| 网络消耗 | 无 | 每次 40 KB |

---

## 7. 脚本使用

两个脚本，功能等价，数据源不同。

### 7.1 `ghelper2clash.ps1` —— 读 Chrome 存储

```powershell
# 完整配置，直接导入
.\ghelper2clash.ps1 -Full -Test -OutFile C:\Users\yz\ghelper-clash.yaml

# 只出片段，贴进已有配置
.\ghelper2clash.ps1 -Test -OutFile C:\Users\yz\ghelper-nodes.yaml

# 挂机监听：在 Chrome 里点连接，脚本自动抓
.\ghelper2clash.ps1 -Watch -Full -Test
```

### 7.2 `ghelper-api.ps1` —— 调 API

```powershell
# 完整配置
.\ghelper-api.ps1 -Full -Test -OutFile C:\Users\yz\ghelper-clash.yaml

# 手动指定 token（不从 Chrome 读）
.\ghelper-api.ps1 -Token <32位hex> -Full -Test
```

### 7.3 参数表

| 参数 | 脚本 | 说明 |
|---|---|---|
| `-Profile` | A | Chrome 配置目录名，默认 `Default` |
| `-Token` | B | 32 位 hex，不传则从 Chrome 存储读 |
| `-OutFile` | A/B | YAML 输出路径 |
| `-Test` | A/B | 逐个测速，只保留通的并按延迟排序 |
| `-Full` | A/B | 输出完整配置（端口/DNS/规则）而非片段 |
| `-Watch` | A | 常驻监听存储文件变化 |
| `-GroupName` | A/B | 分组名前缀，默认 `GHelper` |
| `-ApiHost` | B | 自定义 API 域名池 |

### 7.4 环境要求

- PowerShell 5.1+，脚本须存为 **UTF-8 with BOM**（否则 5.1 会按 GBK 解析中文导致语法错误）
- `curl.exe`（Windows 10 1803+ 自带）

---

## 8. Clash 配置

脚本生成的节点长这样：

```yaml
proxies:
  - name: "GHelper-HKT-01"
    type: http
    server: 15b83f70-tnn6o0-tp1cgt-w7lw.hkt.cdnhuawei.com
    port: 443
    tls: true                 # 关键：HTTPS 代理
    skip-cert-verify: true    # 关键：节点证书是伪装的 CDN 证书
    udp: false

proxy-groups:
  - name: "GHelper"
    type: url-test
    url: "https://www.gstatic.com/generate_204"
    interval: 300
    tolerance: 100
    proxies: [ "GHelper-HKT-01", "GHelper-GZCM1-02", ... ]

  - name: "节点选择"
    type: select
    proxies: [ "GHelper", "DIRECT" ]
```

完整配置默认值：

| 项 | 值 |
|---|---|
| `mixed-port` | 7891（避开常见 7890） |
| `external-controller` | 127.0.0.1:9091 |
| DNS | fake-ip 模式，国内 223.5.5.5 / 119.29.29.29 |
| 规则 | `.cn`、常见国内域名、GEOIP CN 直连，其余走 `节点选择` |

**导入方式**：
- Clash Verge Rev：订阅 → 新建 → 类型选「本地」→ 选文件
- Clash for Windows：直接拖进 Profiles 窗口

---

## 9. 局限与风险

### 9.1 技术局限

| 问题 | 说明 |
|---|---|
| 会话级节点 | 约 180 分钟后整池作废，必须重新生成配置 |
| 无法无限刷新 | 服务端在窗口内复用会话，调用再频繁也不会给新节点 |
| 只读 Chrome | 路线 A 不认 Edge / Firefox，换浏览器要改路径 |
| 存储格式风险 | 若 GHelper 改为加密存储，路线 A 失效 |
| 纯浏览器向 | 节点为国内入口 + 海外落地，非传统机场线路，延迟偏高 |

### 9.2 账号与合规风险

- **服务条款**：GHelper 是否允许第三方客户端使用未经确认。若禁止，属于违约，最坏结果是封号。
- **风控**：跳过扩展后，请求指纹（UA、TLS 指纹、行为节奏）与官方客户端不同，异常频率可能触发检测。
- **凭证安全**：`token` 等同于账号凭证，与 `pk`、PAC 一起明文存在于 leveldb 中。任何能读该文件的程序都能拿到。**不要提交到代码仓库、不要贴到公开场合。**
- **流量口径**：节点池里的节点共享账号配额，批量测速会消耗流量（每次 204 响应，量极小，但非零）。

### 9.3 建议

1. 定期生成配置的间隔设为 **30 分钟**即可，无需更频繁
2. 只在个人设备上使用，不分享节点地址
3. 优先用路线 B（调 API），它不依赖浏览器状态，更稳定
4. 若账号出现异常提示，立即停止并回退到官方客户端

---

## 10. 附录

### 10.1 实测数据

| 指标 | 值 |
|---|---|
| 单次会话节点池 | 29 个 |
| 测通率 | 28–29 / 29 |
| 最快节点 | HKT（香港）约 100–270 ms |
| PAC 大小 | 约 40 KB |
| API 响应耗时 | < 2 s |
| 历史 PAC 快照（15 天） | 37 份 |
| token 写入次数（15 天） | 1 次 |

### 10.2 存储键说明

| 键 | 内容 | 变化频率 |
|---|---|---|
| `token` | 32 位 hex 账号凭证 | 极低（15 天 1 次） |
| `proxy` | 完整 PAC 对象 | 每 180 分钟 |
| `pac_key` | 32 位 hex，即 `pk` | 随 session |
| `api_host` / `api_hosts` / `api_origin` | 当前可用的 API 域名 | 按需 |
| `tester` | 各节点测速结果 | 实时 |

### 10.3 相关文件

| 文件 | 说明 |
|---|---|
| `ghelper2clash.ps1` | 路线 A 脚本 |
| `ghelper-api.ps1` | 路线 B 脚本 |
| `ghelper-nodes.yaml` | 生成的配置片段 |
| `ghelper-clash.yaml` | 生成的完整配置 |

---

*本文档仅记录技术链路，使用者需自行评估服务条款与账号风险。*
