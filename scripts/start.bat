@echo off
setlocal
cd /d "%~dp0.."
set "QIMING_CONDA=conda"
if defined CONDA_EXE if exist "%CONDA_EXE%" set "QIMING_CONDA=%CONDA_EXE%"
if not defined QIMING_ENV set "QIMING_ENV=torchgpu"
if not defined QIMING_ALLOW_CPU set "QIMING_ALLOW_CPU=0"
call "%QIMING_CONDA%" run --no-capture-output -n "%QIMING_ENV%" python -c "import torch; print('PyTorch:', torch.__version__, 'CUDA:', torch.version.cuda, 'available:', torch.cuda.is_available()); assert torch.cuda.is_available() or '%QIMING_ALLOW_CPU%' == '1'"
if errorlevel 1 (
    echo CUDA is unavailable in Conda environment %QIMING_ENV%.
    echo Check the environment name, NVIDIA driver, and CUDA-enabled PyTorch installation.
    echo To intentionally run CPU mode, set QIMING_ALLOW_CPU=1 before starting.
    exit /b 1
)
call "%QIMING_CONDA%" run --no-capture-output -n "%QIMING_ENV%" python -c "import uvicorn, fastapi, httpx, cv2, numpy, dotenv, pandas, openpyxl, ultralytics, websockets, rapidocr_onnxruntime, onnxruntime, sherpa_onnx, yaml, psutil, edge_tts"
if errorlevel 1 (
    echo Required project dependencies are missing in Conda environment %QIMING_ENV%.
    echo Run: conda run -n %QIMING_ENV% python -m pip install -r backend\requirements-conda.txt
    exit /b 1
)
echo Starting backend with Conda environment %QIMING_ENV%.
call "%QIMING_CONDA%" run --no-capture-output -n "%QIMING_ENV%" python -m uvicorn backend.main:app --host 0.0.0.0 --port 8765
exit /b %errorlevel%
