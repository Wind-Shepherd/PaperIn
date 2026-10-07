param([string]$PythonPath = "python", [switch]$SkipInstall)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (Test-Path -LiteralPath $PythonPath) {
    $resolvedPython = (Resolve-Path -LiteralPath $PythonPath).Path
} else {
    $resolvedPython = (Get-Command $PythonPath -ErrorAction Stop).Source
}
$version = & $resolvedPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($version -notin @("3.11", "3.12", "3.13")) {
    throw "请先用 Python 3.11–3.13 创建并激活虚拟环境；当前版本为 $version。BabelDOC 暂不支持 Python 3.14。"
}

if (-not $SkipInstall) {
    & $resolvedPython -m pip install -e '.[desktop]' pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "安装项目依赖或 PyInstaller 失败。" }
}
& $resolvedPython -m PyInstaller --noconfirm --windowed --onedir `
    --name "纸间论文翻译器" `
    --icon app.ico `
    --add-data "app.ico;." `
    --additional-hooks-dir desktop_hooks `
    --collect-all babeldoc `
    --collect-all onnx `
    --collect-all onnxruntime `
    --collect-all fitz `
    --collect-all cv2 `
    --collect-all tkinterdnd2 `
    --collect-submodules bitstring `
    --collect-all bitarray `
    --collect-submodules tiktoken_ext `
    desktop_app.py
if ($LASTEXITCODE -ne 0) { throw "EXE 打包失败。" }

Copy-Item README-EXE.txt dist/纸间论文翻译器/README-EXE.txt -Force
$report = Join-Path $PSScriptRoot 'build\desktop-check.json'
$executable = Join-Path $PSScriptRoot 'dist\纸间论文翻译器\纸间论文翻译器.exe'
$check = Start-Process -FilePath $executable -ArgumentList @('--self-check', ('"' + $report + '"')) -WindowStyle Hidden -PassThru
if (-not $check.WaitForExit(60000)) {
    $check.Kill()
    throw "打包后验证超时，请检查 build/desktop-check.json。"
}
if ($check.ExitCode -ne 0) { throw "打包后验证失败，请检查 build/desktop-check.json。" }
$result = Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $result.ok) { throw "打包后验证未通过：$($result.error)" }
Write-Host "打包完成：$((Resolve-Path dist/纸间论文翻译器).Path)"
