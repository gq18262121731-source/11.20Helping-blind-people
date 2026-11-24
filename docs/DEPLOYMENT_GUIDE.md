# V2.0 部署

```powershell
conda activate torchgpu
python -m pip install -r backend/requirements-conda.txt
python tools/prepare_depth_model.py
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8765
```

若无法使用 CUDA，可设置 `QIMING_DEPTH_ENABLED=false`，系统会保留现有 YOLO/基础告警并在页面显示深度不可用。不要把降级模式当作完整空间感知模式。
