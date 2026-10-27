#Requires -Version 5.1
<#
.SYNOPSIS
    从 Chrome 中 GHelper 扩展的本地存储里提取当前生效的代理节点，生成 Clash / mihomo 配置片段。

.DESCRIPTION
    GHelper 通过 chrome.proxy 下发一份 PAC 脚本，脚本里的 `var proxy="HTTPS a:1;HTTPS b:2;HTTPS c:3"`
    就是当前会话的三个节点（1 主 2 备）。这些节点是标准 HTTPS 代理，Clash 可以直接用。
    节点随会话刷新（约 180 分钟），过期即失效，所以必须在「已连接」状态下提取。

.PARAMETER Profile
    Chrome 用户配置目录名，默认 Default。

.PARAMETER OutFile
    YAML 输出路径。默认写到脚本同目录下的 ghelper-nodes.yaml。

.PARAMETER Test
    生成的节点逐个用 curl 测试连通性，只把测通的结果写进 YAML。

.PARAMETER Watch
    常驻监听：盯着 Chrome 存储文件，一旦 GHelper 写入了新快照就自动提取。
    适合在 GHelper 里点连接之前就先挂着。

.PARAMETER GroupName
    生成的 proxy-group 名称前缀。

.EXAMPLE
    .\ghelper2clash.ps1 -Watch -Test
#>
[CmdletBinding()]
param(
    [string]$Profile = "Default",
    [string]$OutFile = "",
    [switch]$Test,
    [switch]$Watch,
    [switch]$Full,
    [int]$WatchInterval = 3,
    [string]$GroupName = "GHelper"
)

$ErrorActionPreference = "Stop"

# $PSScriptRoot 在 param 默认值求值时尚未初始化，这里再算
if (-not $OutFile) {
    $scriptDir = $PSScriptRoot
    if (-not $scriptDir) { $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
    if (-not $scriptDir) { $scriptDir = (Get-Location).Path }
    $OutFile = Join-Path $scriptDir "ghelper-nodes.yaml"
}

$extId   = "cieikaeocafmceoapfogpffaalkncpkc"
$storeDir = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data\$Profile\Local Extension Settings\$extId"

if (-not (Test-Path $storeDir)) {
    throw "找不到 GHelper 扩展存储目录：$storeDir`n请确认扩展已安装在 Chrome 的 $Profile 配置下（脚本只读 Chrome，不含 Edge）。"
}

# Chrome 运行时会独占 leveldb，但允许共享读
function Read-Shared([string]$Path) {
    $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        $ms = New-Object System.IO.MemoryStream
        $fs.CopyTo($ms)
        return $ms.ToArray()
    }
    finally { $fs.Dispose() }
}

function Get-NodeSnapshots {
    param([string]$StoreDir)
    $found = @()
    foreach ($lg in (Get-ChildItem $StoreDir -Filter "*.log" -ErrorAction SilentlyContinue)) {
        try { $bytes = Read-Shared $lg.FullName } catch { continue }
        $text = [System.Text.Encoding]::GetEncoding(28591).GetString($bytes)
        foreach ($m in [regex]::Matches($text, 'var proxy=\\"([^\\]*)\\"')) {
            $found += [pscustomobject]@{
                Mtime = $lg.LastWriteTime
                Pos   = $m.Index
                Raw   = $m.Groups[1].Value
            }
        }
    }
    return $found
}

function Get-StoreStamp {
    param([string]$StoreDir)
    $logs = Get-ChildItem $StoreDir -Filter "*.log" -ErrorAction SilentlyContinue
    if (-not $logs) { return "" }
    $max = ($logs | Measure-Object -Property LastWriteTime -Maximum).Maximum
    $sum = ($logs | Measure-Object -Property Length -Sum).Sum
    return "$max|$sum"
}

$FullConfigHeader = @(
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

$FullConfigFooter = @(
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

function Invoke-Extract {
    param([switch]$Quiet)
    $snapshots = Get-NodeSnapshots -StoreDir $storeDir
    if ($null -eq $snapshots -or @($snapshots).Count -eq 0) {
        if (-not $Quiet) { Write-Warning "没读到任何 PAC 快照。请先在 Chrome 里打开 GHelper 并点击连接。" }
        return $false
    }

    # 最新的一份在最前
    $ordered = @($snapshots) | Sort-Object -Property @{Expression='Mtime';Descending=$true}, @{Expression='Pos';Descending=$true}
    $directCount = @($ordered | Where-Object { $_.Raw -match '^\s*DIRECT\s*$' }).Count
    $live = $ordered | Where-Object { $_.Raw -notmatch '^\s*DIRECT\s*$' } | Select-Object -First 1

    if (-not $live) {
        if (-not $Quiet) {
            Write-Warning "最近的所有快照都是 DIRECT —— GHelper 当前处于断开状态。"
            Write-Warning "请到 Chrome 里打开 GHelper 并点击连接。"
        }
        return $false
    }

    if ($live.Mtime -lt (Get-Date).AddHours(-3) -and -not $Quiet) {
        Write-Warning ("这份节点快照来自 {0:yyyy-MM-dd HH:mm}，已超过 3 小时，很可能被服务端回收。" -f $live.Mtime)
    }

    # 解析节点：先取当前会话代号，再挖该代号下的完整节点池
    # GHelper 每次会话用一个 6 位代号（如 tnn6o0），PAC 里只把 3 个当主力，
    # 但同一个代号下的全部候选节点都在 PAC 数据里，且都能直连。
    $nodes = @()
    $sessionCode = $null
    if ($live.Raw -match '-([a-z0-9]{6})-tp1cgt-w7lw') { $sessionCode = $Matches[1] }

    if ($sessionCode) {
        $pat = 'HTTPS\s+([A-Za-z0-9\-]+)-' + [regex]::Escape($sessionCode) + '-tp1cgt-w7lw\.([A-Za-z0-9\.\-]+):(\d+)'
        $seen = @{}
        foreach ($lg2 in (Get-ChildItem $storeDir -Filter "*.log" -ErrorAction SilentlyContinue)) {
            try { $bytes2 = Read-Shared $lg2.FullName } catch { continue }
            $txt2 = [System.Text.Encoding]::GetEncoding(28591).GetString($bytes2)
            foreach ($m in [regex]::Matches($txt2, $pat)) {
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
        if (-not $Quiet) {
            Write-Host ("[{0:HH:mm:ss}] 会话代号 {1}，节点池 {2} 个" -f (Get-Date), $sessionCode, $nodes.Count) -ForegroundColor Cyan
        }
    }

    # 兜底：节点池没挖到就只用 PAC 里现役的那几个
    if ($nodes.Count -eq 0) {
        foreach ($part in ($live.Raw -split ';')) {
            $p = $part.Trim()
            if ($p -match '^(?:HTTPS?|PROXY|SOCKS5?|SOCKS4)\s+(?<host>[^:\s]+):(?<port>\d+)$') {
                $nodes += [pscustomobject]@{ Server = $Matches['host']; Port = [int]$Matches['port']; Region = '?' }
            }
        }
        if ($nodes.Count -gt 0 -and -not $Quiet) {
            Write-Host ("[{0:HH:mm:ss}] 仅取到现役 {1} 个节点（未识别会话代号）" -f (Get-Date), $nodes.Count) -ForegroundColor Yellow
        }
    }

    if ($nodes.Count -eq 0) {
        if (-not $Quiet) { Write-Warning "从快照里没解析出节点，原始内容：$($live.Raw)" }
        return $false
    }

    $picked = @()
    $idx = 0
    foreach ($n in $nodes) {
        $idx++
        $ok = $true
        $lat = 0
        if ($Test) {
            Write-Host ("  [{0}/{1}] {2}:{3} ... " -f $idx, $nodes.Count, $n.Region, $n.Port) -NoNewline
            $sw = [System.Diagnostics.Stopwatch]::StartNew()
            $code = & curl.exe --proxy ("https://{0}:{1}" -f $n.Server, $n.Port) --proxy-insecure -s -o NUL `
                        -w "%{http_code}" --max-time 8 https://www.gstatic.com/generate_204 2>$null
            $sw.Stop()
            $lat = [int]$sw.ElapsedMilliseconds
            $ok = ($code -eq '204')
            if ($ok) { Write-Host ("OK  {0} ms" -f $lat) -ForegroundColor Green }
            else     { Write-Host ("失败 (code={0})" -f $code) -ForegroundColor DarkGray }
        }
        if ($ok) { $picked += [pscustomobject]@{ Node = $n; Index = $idx; Latency = $lat } }
    }

    # 测过速就按延迟排序，最快的最靠前
    if ($Test) { $picked = @($picked | Sort-Object Latency) }

    if ($picked.Count -eq 0) {
        if (-not $Quiet) {
            Write-Warning "所有节点都连不上。多半是快照过期（会话被回收），请在 GHelper 里断开重连。"
        }
        return $false
    }

    # 生成 YAML
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine("# 由 ghelper2clash.ps1 于 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 生成")
    [void]$sb.AppendLine("# 节点来源快照时间：$($live.Mtime.ToString('yyyy-MM-dd HH:mm:ss'))")
    if ($Full) {
        [void]$sb.AppendLine("# 完整配置，可直接导入 Clash Verge / mihomo")
        foreach ($line in $FullConfigHeader) { [void]$sb.AppendLine($line) }
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
        foreach ($line in $FullConfigFooter) { [void]$sb.AppendLine($line) }
    }

    $yaml = $sb.ToString()
    Set-Content -Path $OutFile -Value $yaml -Encoding UTF8
    Write-Host ("  已写入 {0}（{1} 个可用节点）" -f $OutFile, $picked.Count) -ForegroundColor Cyan
    return $true
}

# ---- 启动检查 ----
$chromeRunning = [bool](Get-Process chrome -ErrorAction SilentlyContinue)
if (-not $chromeRunning) {
    Write-Warning "Chrome 当前没有运行，或 GHelper 装在别的浏览器里（脚本只读 Chrome，不读 Edge）。"
    Write-Warning "请先启动 Chrome 并在里面连接 GHelper。"
}

if ($Watch) {
    Write-Host "监听中（Ctrl+C 退出）——去 Chrome 里点 GHelper 的连接，采到节点后会自动生成配置。" -ForegroundColor Yellow
    $last = Get-StoreStamp -StoreDir $storeDir
    while ($true) {
        Start-Sleep -Seconds $WatchInterval
        $now = Get-StoreStamp -StoreDir $storeDir
        if ($now -ne $last) {
            $last = $now
            Start-Sleep -Milliseconds 500   # 等 leveldb 落盘
            $null = Invoke-Extract
        }
    }
}
else {
    $ok = Invoke-Extract
    if (-not $ok) { exit 1 }
}
