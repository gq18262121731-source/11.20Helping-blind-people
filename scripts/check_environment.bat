@echo off
setlocal
cd /d "%~dp0.."
set "QIMING_CONDA=conda"
if defined CONDA_EXE if exist "%CONDA_EXE%" set "QIMING_CONDA=%CONDA_EXE%"
call "%QIMING_CONDA%" run --no-capture-output -n yolo-blind python -c "import sys, fastapi, uvicorn, httpx, cv2, numpy, dotenv, pandas, openpyxl, ultralytics, websockets, rapidocr_onnxruntime, onnxruntime, sherpa_onnx, pytest, serial; print('Python:', sys.executable); print('Version:', sys.version); print('Project imports: OK')"
if errorlevel 1 exit /b 1
call "%QIMING_CONDA%" run --no-capture-output -n yolo-blind python -m pip check
if errorlevel 1 exit /b 1
if exist backend\.env (echo .env exists) else (echo Copy backend\.env.example to backend\.env before starting.)
