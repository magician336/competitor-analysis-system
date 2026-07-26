<#
.SYNOPSIS
  CodeRadar 一键部署脚本 — 从零到运行只需一条命令。

.DESCRIPTION
  1. 检查前置条件（Docker、Docker Compose）
  2. 初始化 .env（从 .env.example 复制，提示必填项）
  3. 构建所有 Docker 镜像
  4. 启动 Elasticsearch，等待就绪
  5. 初始化数据库（alembic migrations）
  6. 导入种子数据与基线产物
  7. 启动 API / Worker / 前端，等待就绪
  8. 构建 ES 索引
  9. 输出访问地址

.PARAMETER Port
  前端 nginx 监听端口，默认 8080。
.PARAMETER ApiPort
  API 直接访问端口（调试用），默认 8001。
.PARAMETER SkipInit
  跳过所有初始化步骤（数据库迁移、种子数据、ES 索引）。
.PARAMETER SkipDB
  跳过数据库迁移（alembic upgrade head）。
.PARAMETER SkipSeed
  跳过种子数据导入（init_database）。
.PARAMETER SkipIndex
  跳过 ES 索引构建（build_index）。
.PARAMETER ForceEnv
  覆盖已有的 .env 文件（从 .env.example 重新复制）。

.EXAMPLE
  .\deploy.ps1
  .\deploy.ps1 -Port 3000 -ApiPort 9001
  .\deploy.ps1 -SkipIndex
  .\deploy.ps1 -ForceEnv
#>

param(
  [int]$Port = 8080,
  [int]$ApiPort = 8001,
  [switch]$SkipInit,
  [switch]$SkipDB,
  [switch]$SkipSeed,
  [switch]$SkipIndex,
  [switch]$ForceEnv
)

# ── 从 .env 读取端口覆盖（命令行参数优先） ──────────────────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = Get-Location }
$envFile = Join-Path $ScriptDir ".env"

if (Test-Path $envFile) {
  $envApiPort = (Select-String -Path $envFile -Pattern "^CODERADAR_API_PORT=" | ForEach-Object { $_.Line -replace '^CODERADAR_API_PORT=' ,'' } | ForEach-Object { $_.Trim() })
  $envUiPort = (Select-String -Path $envFile -Pattern "^CODERADAR_UI_PORT=" | ForEach-Object { $_.Line -replace '^CODERADAR_UI_PORT=' ,'' } | ForEach-Object { $_.Trim() })

  if ($ApiPort -eq 8001 -and $envApiPort -match '^\d+$') { $ApiPort = [int]$envApiPort }
  if ($Port -eq 8080 -and $envUiPort -match '^\d+$')    { $Port   = [int]$envUiPort }
}

# ── 颜色与输出辅助函数 ─────────────────────────────────────────────────────
$ColorInfo   = "Cyan"
$ColorSuccess = "Green"
$ColorWarning = "Yellow"
$ColorError   = "Red"
$ColorStep    = "Magenta"

function Write-Step  { param([string]$Msg) Write-Host "`n>>> $Msg" -ForegroundColor $ColorStep }
function Write-Info  { param([string]$Msg) Write-Host "  $Msg"   -ForegroundColor $ColorInfo }
function Write-Ok    { param([string]$Msg) Write-Host "  [✓] $Msg" -ForegroundColor $ColorSuccess }
function Write-Warn  { param([string]$Msg) Write-Host "  [!] $Msg" -ForegroundColor $ColorWarning }
function Write-Err   { param([string]$Msg) Write-Host "  [✗] $Msg" -ForegroundColor $ColorError }

function Exit-With-Error {
  param([string]$Msg)
  Write-Err $Msg; Write-Err "部署失败。请根据上述错误信息修复后重试。"
  exit 1
}

function Show-DeploymentInfo {
  Write-Host ""
  Write-Host "╔══════════════════════════════════════════════════════════╗" -ForegroundColor $ColorSuccess
  Write-Host "║        CodeRadar 部署成功！                            ║" -ForegroundColor $ColorSuccess
  Write-Host "╠══════════════════════════════════════════════════════════╣" -ForegroundColor $ColorSuccess
  function Read-EnvValue {
    param([string]$Key)
    $line = Select-String -Path $envFile -Pattern "^${Key}=" 2>$null
    if ($line) { return ($line.Line -replace "^${Key}=", '').Trim() }; return ''
  }
  $agentMode = Read-EnvValue "CODERADAR_AGENT_MODE"
  $authEnabled = Read-EnvValue "CODERADAR_AUTH_ENABLED"
  $ragProfile = Read-EnvValue "MINIRAG_PROFILE"
  $installMl = Read-EnvValue "MINIRAG_INSTALL_ML"
  Write-Host "║                                                      ║" -ForegroundColor $ColorSuccess
  Write-Host "  🌐  前端 UI：         http://localhost:$Port" -ForegroundColor $ColorInfo
  Write-Host "  🔗  API（直接）：     http://localhost:${ApiPort}/health" -ForegroundColor $ColorInfo
  Write-Host "  🔍  Elasticsearch：  http://localhost:9200" -ForegroundColor $ColorInfo
  Write-Host ""
  Write-Host "  当前配置：" -ForegroundColor $ColorInfo
  Write-Host "    Agent 模式：    $(if ($agentMode) { $agentMode } else { 'rules' })" -ForegroundColor $ColorInfo
  Write-Host "    认证已启用：    $(if ($authEnabled -eq 'true') { '是' } else { '否' })" -ForegroundColor $ColorInfo
  Write-Host "    RAG Profile：   $(if ($ragProfile) { $ragProfile } else { 'default' })" -ForegroundColor $ColorInfo
  Write-Host "    ML 依赖：       $(if ($installMl -eq 'true') { '已安装' } else { '未安装' })" -ForegroundColor $ColorInfo
  Write-Host ""
  Write-Host "  常用命令：" -ForegroundColor $ColorInfo
  Write-Host "    查看日志：      docker compose logs -f api" -ForegroundColor $ColorInfo
  Write-Host "    停止服务：      docker compose down" -ForegroundColor $ColorInfo
  Write-Host "    重新部署：      .\deploy.ps1" -ForegroundColor $ColorInfo
  Write-Host "║                                                      ║" -ForegroundColor $ColorSuccess
  Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor $ColorSuccess
}

# ── 1. 检查前置条件 ──────────────────────────────────────────────────────
Write-Step "步骤 1/9：检查前置条件"
Set-Location $ScriptDir
Write-Info "工作目录: $(Get-Location)"

try { $dockerVer = docker --version 2>$null; if (-not $dockerVer) { throw }; Write-Ok "Docker: $dockerVer" } catch { Exit-With-Error "Docker 未安装或未运行" }
try { $composeVer = docker compose version 2>$null; if (-not $composeVer) { throw }; Write-Ok "Docker Compose: $composeVer" } catch { Exit-With-Error "Docker Compose 不可用" }

function Test-PortAvailable {
  param([int]$Port, [string]$Name)
  $inUse = netstat -an 2>$null | Select-String ":$Port\s"
  if ($inUse) { Write-Warn "端口 $Port（$Name）已被占用。" } else { Write-Ok "端口 $Port（$Name）可用" }
}
Test-PortAvailable $Port    "前端 UI"
Test-PortAvailable $ApiPort "API 调试"
Test-PortAvailable 9200     "Elasticsearch"

# ── 2. 检查并初始化 .env ──────────────────────────────────────────────────
Write-Step "步骤 2/9：检查环境配置"
$envFile = Join-Path $ScriptDir ".env"
$envExample = Join-Path $ScriptDir ".env.example"

if ($ForceEnv -and (Test-Path $envFile)) { Write-Warn "参数 -ForceEnv 已设置，覆盖已有 .env 文件"; Remove-Item $envFile -Force }

if (-not (Test-Path $envFile)) {
  Copy-Item $envExample $envFile; Write-Ok ".env 已创建"
  Write-Warn "=========================================================="
  Write-Warn "  请编辑 .env 文件，配置以下必填项："
  Write-Warn "    1. GITHUB_TOKEN"
  Write-Warn "    2. DEEPSEEK_API_KEY"
  Write-Warn "    3. CODERADAR_API_KEY"
  Write-Warn "=========================================================="
  Write-Info "按 Enter 继续（或 Ctrl+C 退出编辑后再运行）..."; $null = Read-Host
} else { Write-Ok ".env 文件已存在" }

# ── 3. 构建镜像 ──────────────────────────────────────────────────────────
Write-Step "步骤 3/9：构建 Docker 镜像"
$env:CODERADAR_UI_PORT = $Port.ToString()
$env:CODERADAR_API_PORT = $ApiPort.ToString()

Write-Info "构建所有镜像..."
$buildResult = docker compose build 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Err "构建失败："; $buildResult | ForEach-Object { Write-Host "    $_" -ForegroundColor $ColorError }
  Exit-With-Error "Docker 构建失败"
}
Write-Ok "镜像构建完成"

# ── 4. 启动 Elasticsearch ────────────────────────────────────────────────
Write-Step "步骤 4/9：启动 Elasticsearch"
$esUp = docker compose up -d elasticsearch 2>&1
if ($LASTEXITCODE -ne 0) { Exit-With-Error "Elasticsearch 启动失败" }

Write-Info "等待 Elasticsearch 就绪（最多 120 秒）..."
$esReady = $false
for ($i = 0; $i -lt 120; $i++) {
  try { $r = Invoke-WebRequest -Uri "http://127.0.0.1:9200/_cluster/health" -UseBasicParsing -TimeoutSec 3 2>$null; if ($r.StatusCode -eq 200) { $esReady = $true; break } } catch {}
  if ($i % 10 -eq 0 -and $i -gt 0) { Write-Info "  已等待 $i 秒..." }
  Start-Sleep -Seconds 1
}
if (-not $esReady) { Exit-With-Error "Elasticsearch 未在 120 秒内就绪" }
Write-Ok "Elasticsearch 就绪"

# ── 跳过初始化？ ─────────────────────────────────────────────────────────
if ($SkipInit) {
  Write-Step "已跳过初始化步骤"
  Write-Info "提示：运行 'docker compose up -d api worker frontend' 启动其余服务"
  Show-DeploymentInfo; exit 0
}

# ── 5. 数据库迁移（在 API 启动前执行） ────────────────────────────────────
if (-not $SkipDB) {
  Write-Step "步骤 5/9：数据库迁移（alembic upgrade head）"
  $result = docker compose run --rm api python -m alembic upgrade head 2>&1
  if ($LASTEXITCODE -eq 0) { Write-Ok "数据库迁移完成" }
  else { Write-Warn "数据库迁移可能有警告："; $result | ForEach-Object { Write-Host "    $_" } }
} else { Write-Info "跳过数据库迁移（-SkipDB）" }

# ── 6. 种子数据 ──────────────────────────────────────────────────────────
if (-not $SkipSeed) {
  Write-Step "步骤 6/9：导入种子数据与基线产物"
  $result = docker compose run --rm api python -m scripts.init_database --seed-competitors --seed-codemate --import-artifacts artifacts/week3 2>&1
  if ($LASTEXITCODE -eq 0) { Write-Ok "种子数据导入完成" }
  else { Write-Warn "种子数据导入可能有异常："; $result | ForEach-Object { Write-Host "    $_" } }
} else { Write-Info "跳过种子数据导入（-SkipSeed）" }

# ── 7. 启动 API / Worker / 前端 ──────────────────────────────────────────
Write-Step "步骤 7/9：启动 API / Worker / 前端"
$svcUp = docker compose up -d api worker frontend 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Err "服务启动失败："; $svcUp | ForEach-Object { Write-Host "    $_" -ForegroundColor $ColorError }
  Exit-With-Error "服务启动失败"
}

Write-Info "等待 API 就绪（最多 120 秒）..."
$healthUrl = "http://127.0.0.1:${ApiPort}/health"
$ready = $false
for ($i = 0; $i -lt 120; $i++) {
  try { $r = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3 2>$null; if ($r.StatusCode -eq 200) { $ready = $true; break } } catch {}
  if ($i % 10 -eq 0 -and $i -gt 0) { Write-Info "  已等待 $i 秒..." }
  Start-Sleep -Seconds 1
}
if (-not $ready) { Exit-With-Error "API 服务未在 120 秒内就绪" }
Write-Ok "API 服务就绪"

# ── 8. 构建 ES 索引 ──────────────────────────────────────────────────────
if (-not $SkipIndex) {
  Write-Step "步骤 8/9：构建 Elasticsearch 索引"
  $result = docker compose exec api python -m scripts.build_index 2>&1
  if ($LASTEXITCODE -eq 0) { Write-Ok "ES 索引构建完成" }
  else {
    Write-Warn "ES 索引构建可能未完全成功，可稍后手动执行："
    Write-Warn "  docker compose exec api python -m scripts.build_index"
    $result | ForEach-Object { Write-Host "    $_" }
  }
} else { Write-Info "跳过 ES 索引构建（-SkipIndex）" }

# ── 9. 输出部署信息 ──────────────────────────────────────────────────────
Write-Step "步骤 9/9：部署信息"
Show-DeploymentInfo
