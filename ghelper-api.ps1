#Requires -Version 5.1
<#
.SYNOPSIS
    用 GHelper 账号 token 直接调用官方 API 拉取代理节点池，生成 Clash 配置。
    不依赖 Chrome 运行，不依赖扩展在线。

.DESCRIPTION
    复刻扩展 sw.js 里 asyncSession() 的请求：
      POST https://<随机uuid>.<api域名>/api2/session?token=<token>&version=2.9.3
      Header: token: <token>
      Body:   {"tester":{}}
    响应里的 session.proxy.pac.value.pacScript.data 就是 PAC 脚本，
    里面含当前会话的全部节点（不只是 var proxy 那 3 个）。

.PARAMETER Token
    32 位 hex 账号 token。不传则自动从 Chrome 存储里读。

.PARAMETER OutFile
    YAML 输出路径。默认脚本同目录 ghelper-nodes.yaml。

.PARAMETER Test
    逐个 curl 测速，只保留通的，并按延迟排序。

.PARAMETER Full
    输出完整 Clash 配置（含端口/DNS/规则），可直接导入。

.PARAMETER ApiHost
    指定 API 域名，默认轮询 gheapi.com / apihuawei.com / plebvps.com / apikuaishou.com。

.EXAMPLE
    .\ghelper-api.ps1 -Full -Test -OutFile C:\Users\yz\ghelper-clash.yaml
#>
[CmdletBinding()]
param(
    [string]$Token = "",
    [string]$OutFile = "",
    [switch]$Test,
    [switch]$Full,
    [string]$GroupName = "GHelper",
    [string[]]$ApiHost = @("gheapi.com", "apihuawei.com", "plebvps.com", "apikuaishou.com")
)

$ErrorActionPreference = "Stop"

if (-not $OutFile) {
    $scriptDir = $PSScriptRoot
    if (-not $scriptDir) { $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
    if (-not $scriptDir) { $scriptDir = (Get-Location).Path }
    $OutFile = Join-Path $scriptDir "ghelper-nodes.yaml"
}

$extId    = "cieikaeocafmceoapfogpffaalkncpkc"
$storeDir = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data\Default\Local Extension Settings\$extId"
$version  = "2.9.3"

# ---------- 1. 取 token ----------
if (-not $Token) {
    if (-not (Test-Path $storeDir)) {
        throw "没传 -Token，且找不到 Chrome 存储目录：$storeDir`n请手动指定 -Token <32位hex>"
    }
    $found = $null
    foreach ($lg in (Get-ChildItem $storeDir -Filter "*.log" -ErrorAction SilentlyContinue)) {
        $fs = [System.IO.File]::Open($lg.FullName, 'Open', 'Read', 'ReadWrite')
        try {
            $ms = New-Object System.IO.MemoryStream
            $fs.CopyTo($ms)
            $txt = [System.Text.Encoding]::GetEncoding(28591).GetString($ms.ToArray())
        }
        finally { $fs.Dispose() }
        if ($txt -match 'token""([a-f0-9]{32})') { $found = $Matches[1] }
    }
    if (-not $found) { throw "没能从 Chrome 存储里读到 token，请用 -Token 手动指定。" }
    $Token = $found
    Write-Host ("token: {0}...（从 Chrome 存储读取）" -f $Token.Substring(0, 8)) -ForegroundColor DarkGray
}

# ---------- 2. 调 API ----------
$resp = $null
$lastErr = $null
foreach ($apiName in $ApiHost) {
    $uuid = [guid]::NewGuid().ToString()
    $url  = "https://$uuid.$apiName/api2/session?token=$Token&version=$version"
    try {
        $raw = & curl.exe -s -X POST $url `
                    -H "content-type: application/json" `
                    -H "token: $Token" `
                    -d '{"tester":{}}' --max-time 20 2>$null
        $body = ($raw -join "`n")
        if (-not $body) { $lastErr = "空响应"; continue }
        $obj = $body | ConvertFrom-Json
        if ($obj.PSObject.Properties.Name -contains 'error' -and $obj.error) {
            $lastErr = "API error: $($obj.error | ConvertTo-Json -Compress -Depth 3)"
            continue
        }
        if ($obj.session -and $obj.session.proxy) {
            $resp = $obj
            Write-Host ("API 命中: {0}" -f $apiName) -ForegroundColor DarkGray
            break
        }
        $lastErr = "响应里没有 session.proxy"
    }
    catch {
        $lastErr = $_.Exception.Message
    }
}

if (-not $resp) { throw "所有 API 域名都失败，最后错误：$lastErr`n（token 可能已失效，请在插件里重新登录）" }

# ---------- 3. 解析 PAC ----------
$pk  = $resp.session.proxy.pk
$pac = $resp.session.proxy.pac.value.pacScript.data
if (-not $pac) { throw "PAC 数据为空" }

$sessionCode = $null
if ($pac -match '-([a-z0-9]{6})-tp1cgt-w7lw') { $sessionCode = $Matches[1] }

$nodes = @()
if ($sessionCode) {
    $pat  = 'HTTPS\s+([A-Za-z0-9\-]+)-' + [regex]::Escape($sessionCode) + '-tp1cgt-w7lw\.([A-Za-z0-9\.\-]+):(\d+)'
    $seen = @{}
    foreach ($m in [regex]::Matches($pac, $pat)) {
        $srv = "{0}-{1}-tp1cgt-w7lw.{2}" -f $m.Groups[1].Value, $sessionCode, $m.Groups[2].Value
        $prt = [int]$m.Groups[3].Value
        $key = "${srv}:$prt"
        if (-not $seen.ContainsKey($key)) {
            $seen[$key] = $true
            $region = ($m.Groups[2].Value -split '\.')[0].ToUpper()
            $nodes += [pscustomobject]@{ Server = $srv; Port = $prt; Region = $region }
        }
    }
}

if ($nodes.Count -eq 0) {
    foreach ($part in (($pac | Select-String -Pattern 'var proxy="([^"]*)"').Matches[0].Groups[1].Value -split ';')) {
        if ($part -match '^HTTPS\s+(?<host>[^:\s]+):(?<port>\d+)$') {
            $nodes += [pscustomobject]@{ Server = $Matches['host']; Port = [int]$Matches['port']; Region = '?' }
        }
    }
}

if ($nodes.Count -eq 0) { throw "没从 PAC 里解析出节点" }

Write-Host ("pk={0}  会话代号 {1}  节点池 {2} 个" -f $pk.Substring(0,8), $sessionCode, $nodes.Count) -ForegroundColor Cyan

# ---------- 4. 测速 ----------
$picked = @()
$idx = 0
foreach ($n in $nodes) {
    $idx++
    $ok  = $true
    $lat = 0
    if ($Test) {
        Write-Host ("  [{0}/{1}] {2}:{3} ... " -f $idx, $nodes.Count, $n.Region, $n.Port) -NoNewline
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $code = & curl.exe --proxy ("https://{0}:{1}" -f $n.Server, $n.Port) --proxy-insecure -s -o NUL `
                    -w "%{http_code}" --max-time 8 https://www.gstatic.com/generate_204 2>$null
        $sw.Stop()
        $lat = [int]$sw.ElapsedMilliseconds
        $ok  = ($code -eq '204')
        if ($ok) { Write-Host ("OK  {0} ms" -f $lat) -ForegroundColor Green }
        else     { Write-Host ("失败 (code={0})" -f $code) -ForegroundColor DarkGray }
    }
    if ($ok) { $picked += [pscustomobject]@{ Node = $n; Latency = $lat } }
}

if ($Test) { $picked = @($picked | Sort-Object Latency) }
if ($picked.Count -eq 0) { throw "所有节点都连不上，会话可能已过期。" }

# ---------- 5. 生成 YAML ----------
$header = @(
    'mixed-port: 7891'
    'allow-lan: false'
    'bind-address: "*"'
    'mode: rule'
    'log-level: info'
    'ipv6: false'
    'external-controller: 127.0.0.1:9091'
    'dns:'
    '  enable: true'
    '  ipv6: false'
    '  enhanced-mode: fake-ip'
    '  fake-ip-range: 198.18.0.1/16'
    '  fake-ip-filter:'
    '    - "*.lan"'
    '    - "*.local"'
    '  nameserver:'
    '    - 223.5.5.5'
    '    - 119.29.29.29'
    '  fallback:'
    '    - https://1.1.1.1/dns-query'
    '    - https://8.8.8.8/dns-query'
)
$footer = @(
    'rules:'
    '  - DOMAIN-SUFFIX,cn,DIRECT'
    '  - DOMAIN-KEYWORD,baidu,DIRECT'
    '  - DOMAIN-KEYWORD,qq.com,DIRECT'
    '  - DOMAIN-KEYWORD,taobao,DIRECT'
    '  - DOMAIN-KEYWORD,jd.com,DIRECT'
    '  - DOMAIN-KEYWORD,bilibili,DIRECT'
    '  - GEOIP,CN,DIRECT'
    '  - MATCH,节点选择'
)

$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine("# 由 ghelper-api.ps1 于 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 生成")
[void]$sb.AppendLine("# 来源：GHelper API /api2/session   pk=$pk   会话代号=$sessionCode")
if ($Full) {
    [void]$sb.AppendLine("# 完整配置，可直接导入 Clash Verge / mihomo")
    foreach ($l in $header) { [void]$sb.AppendLine($l) }
}
[void]$sb.AppendLine("proxies:")
$names = @()
$k = 0
foreach ($u in $picked) {
    $k++
    $name = "{0}-{1}-{2:D2}" -f $GroupName, $u.Node.Region, $k
    $names += $name
    [void]$sb.AppendLine("  - name: `"$name`"")
    [void]$sb.AppendLine("    type: http")
    [void]$sb.AppendLine("    server: $($u.Node.Server)")
    [void]$sb.AppendLine("    port: $($u.Node.Port)")
    [void]$sb.AppendLine("    tls: true")
    [void]$sb.AppendLine("    skip-cert-verify: true")
    [void]$sb.AppendLine("    udp: false")
}
[void]$sb.AppendLine("")
[void]$sb.AppendLine("proxy-groups:")
[void]$sb.AppendLine("  - name: `"$GroupName`"")
[void]$sb.AppendLine("    type: url-test")
[void]$sb.AppendLine("    url: `"https://www.gstatic.com/generate_204`"")
[void]$sb.AppendLine("    interval: 300")
[void]$sb.AppendLine("    tolerance: 100")
[void]$sb.AppendLine("    proxies:")
foreach ($nm in $names) { [void]$sb.AppendLine("      - `"$nm`"") }
if ($Full) {
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("  - name: `"节点选择`"")
    [void]$sb.AppendLine("    type: select")
    [void]$sb.AppendLine("    proxies:")
    [void]$sb.AppendLine("      - `"$GroupName`"")
    [void]$sb.AppendLine("      - `"DIRECT`"")
    [void]$sb.AppendLine("")
    foreach ($l in $footer) { [void]$sb.AppendLine($l) }
}

Set-Content -Path $OutFile -Value $sb.ToString() -Encoding UTF8
Write-Host ("已写入 {0}（{1} 个可用节点）" -f $OutFile, $picked.Count) -ForegroundColor Cyan
