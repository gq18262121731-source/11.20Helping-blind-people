$ErrorActionPreference = 'Continue'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Artifact = Join-Path $Root 'docs\acceptance_artifacts'
New-Item -ItemType Directory -Force -Path $Artifact | Out-Null
Start-Transcript -Path (Join-Path $Artifact ("run-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')) -Force | Out-Null
$backendProcess = $null

function Run-Step([string]$Name, [string]$Exe, [string[]]$Arguments) {
  Write-Host ("`n=== " + $Name + " ===") -ForegroundColor Cyan
  & $Exe @Arguments
  if ($LASTEXITCODE -ne 0) { Write-Warning ($Name + ' exit code ' + $LASTEXITCODE) }
}

try {
  $candidates = @()
  if ($env:MORNINGSTAR_ACCEPTANCE_PYTHON) { $candidates += $env:MORNINGSTAR_ACCEPTANCE_PYTHON }
  $candidates += @('C:\Users\13010\anaconda3\envs\vision311\python.exe', 'C:\Users\13010\AppData\Local\Programs\Python\Python311\python.exe')
  if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += (& py -3.11 -c "import sys; print(sys.executable)" 2>$null) }
  $candidates += @('python')
  $basePython = $candidates | Where-Object { $_ -and (Test-Path $_ -PathType Leaf -ErrorAction SilentlyContinue) } | Select-Object -First 1
  if (-not $basePython) { $basePython = 'python' }
  $baseReady = $false
  try { & $basePython -c "import fastapi,httpx,cv2,rapidocr_onnxruntime,onnxruntime,sherpa_onnx,ultralytics,pytest" 2>$null; $baseReady = ($LASTEXITCODE -eq 0) } catch {}
  $Venv = Join-Path $Root '.venv'
  $VenvPython = Join-Path $Venv 'Scripts\python.exe'
  if ($baseReady) {
    $Python = $basePython
    Write-Host '复用已具备完整依赖的 Python 环境。' -ForegroundColor Green
  } else {
    if (-not (Test-Path $VenvPython)) { Run-Step '创建可复现 Python 虚拟环境' $basePython @('-m','venv','--system-site-packages',$Venv) }
    if (Test-Path $VenvPython) { $Python = $VenvPython } else { $Python = $basePython }
    Run-Step '安装项目与测试依赖' $Python @('-m','pip','install','--disable-pip-version-check','--prefer-binary','-r',(Join-Path $Root 'backend\requirements-conda.txt'),'pytest')
  }
  & $Python (Join-Path $Root 'tools\environment_probe.py') | Out-File -Encoding utf8 (Join-Path $Artifact 'environment.json')
  Run-Step '准备真实图片、音频和模型素材' $Python @((Join-Path $Root 'tools\prepare_test_assets.py'))
  Run-Step '运行真实 RapidOCR 与 sherpa-onnx 测试' $Python @((Join-Path $Root 'tools\run_real_model_tests.py'))
  Run-Step '运行语音协议与 OCR_READ 业务闭环' $Python @((Join-Path $Root 'tools\run_business_acceptance.py'))

  $modelRoot = Get-ChildItem (Join-Path $Root 'test_assets\models') -Directory -Filter 'sherpa-onnx-sense-voice-*' | Select-Object -First 1
  $env:PORT = '8766'
  $env:DATABASE_PATH = Join-Path $Artifact 'acceptance.sqlite3'
  $env:OCR_ENGINE = 'rapidocr'
  if ($modelRoot) {
    $env:ASR_MODEL = Join-Path $modelRoot.FullName 'model.int8.onnx'
    $env:ASR_TOKENS = Join-Path $modelRoot.FullName 'tokens.txt'
  }
  $serverOut = Join-Path $Artifact 'backend.stdout.log'
  $serverErr = Join-Path $Artifact 'backend.stderr.log'
  Write-Host "`n=== 启动测试后端 ===" -ForegroundColor Cyan
  $backendProcess = Start-Process -FilePath $Python -ArgumentList @('-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8766') -WorkingDirectory $Root -RedirectStandardOutput $serverOut -RedirectStandardError $serverErr -PassThru
  $ready = $false
  1..30 | ForEach-Object { if (-not $ready) { Start-Sleep -Milliseconds 500; try { Invoke-RestMethod 'http://127.0.0.1:8766/api/health' -TimeoutSec 2 | Out-Null; $ready = $true } catch {} } }
  if (-not $ready) { Write-Warning 'backend health endpoint did not become ready' }
  $env:QH_LOCAL_CAMERA_HTTP = 'http://127.0.0.1:8766/api/local-camera/frame'
  $env:QH_CAMERA_SECONDS = '2'
  Run-Step '运行摄像头上传模拟器' $Python @((Join-Path $Root 'tools\camera_simulator.py'))
  $wav = Get-ChildItem (Join-Path $Root 'test_assets\models') -Recurse -Filter 'zh.wav' | Select-Object -First 1
  $env:QH_AUDIO_WS = 'ws://127.0.0.1:8766/ws/audio'
  $env:QH_DEVICE_ID = 'acceptance_simulator'
  $audioArgs = @((Join-Path $Root 'tools\audio_simulator.py'), [string]$wav.FullName)
  Run-Step '运行真实音频 WebSocket 模拟器' $Python $audioArgs
  if ($backendProcess) { Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue; $backendProcess = $null }
  Run-Step '运行完整 Python pytest 回归' $Python @('-m','pytest',(Join-Path $Root 'tests'),'-q') 2>&1 | Tee-Object -FilePath (Join-Path $Artifact 'pytest.txt')
  Run-Step '运行前端 Node 回归' 'node' @('--test','tests\camera_monitor.test.cjs','tests\camera_speech.test.cjs','tests\ocr_reader.test.cjs','tests\overview.test.cjs') 2>&1 | Tee-Object -FilePath (Join-Path $Artifact 'node.txt')
  Run-Step '生成最终验收报告' $Python @((Join-Path $Root 'tools\generate_acceptance_report.py'))
} finally {
  if ($backendProcess) { Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue }
  Stop-Transcript | Out-Null
}
