@echo off
setlocal
cd /d "%~dp0.."
set "QIMING_CONDA=conda"
if defined CONDA_EXE if exist "%CONDA_EXE%" set "QIMING_CONDA=%CONDA_EXE%"
if not defined QIMING_ENV set "QIMING_ENV=torchgpu"
if not defined QIMING_ALLOW_CPU set "QIMING_ALLOW_CPU=0"
call "%QIMING_CONDA%" run --no-capture-output -n "%QIMING_ENV%" python -c "import torch; print('Python CUDA runtime:', torch.version.cuda); print('CUDA available:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none'); assert torch.cuda.is_available() or '%QIMING_ALLOW_CPU%' == '1'"
if errorlevel 1 exit /b 1
call "%QIMING_CONDA%" run --no-capture-output -n "%QIMING_ENV%" python -c "import sys, fastapi, uvicorn, httpx, cv2, numpy, dotenv, pandas, openpyxl, ultralytics, websockets, rapidocr_onnxruntime, onnxruntime, sherpa_onnx, yaml, psutil, edge_tts; print('Python:', sys.executable); print('Version:', sys.version); print('Project imports: OK')"
if errorlevel 1 exit /b 1
call "%QIMING_CONDA%" run --no-capture-output -n "%QIMING_ENV%" python -m pip check
if errorlevel 1 exit /b 1
if exist backend\.env (echo .env exists) else (echo Copy backend\.env.example to backend\.env before starting.)
