# 构建 GHelper token 提取器的 Windows 单文件 exe。
# 用法：pwsh -File scripts\build_extractor_exe.ps1 [-Python .\.venv312\Scripts\python.exe]
param([string]$Python = ".\.venv\Scripts\python.exe")

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

if (-not (Test-Path $Python)) {
    throw "找不到 Python 解释器：$Python（可用 -Python 指定）"
}

& $Python -c "import dfindexeddb, snappy, zstd, PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "缺少依赖，请先安装：pip install -r requirements-local.txt pyinstaller"
}

& $Python -m PyInstaller --noconfirm --clean --onefile --console `
    --name ghelper-token-extractor `
    --distpath dist-exe --workpath build-exe --specpath build-exe `
    scripts\extract_browser_tokens.py
if ($LASTEXITCODE -ne 0) {
    throw "打包失败"
}

$exe = Join-Path $root "dist-exe\ghelper-token-extractor.exe"
Write-Host "已生成：$exe"
