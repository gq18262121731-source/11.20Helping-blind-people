# 部署

服务绑定 0.0.0.0 以支持局域网，电脑定位采集仍从 localhost 打开。手机只接收电脑上传的位置，不负责申请电脑定位。Windows 防火墙只在私人网络开放端口；热点客户端隔离会阻止手机访问电脑。

## PowerShell 启动目录

必须让 Python 能找到项目的 `backend` 包。可以先切换目录再启动：

```powershell
Set-Location -LiteralPath 'F:\学校\工坊\导盲帽子\Morning-Star'
conda run --no-capture-output -n yolo-blind python -m uvicorn backend.main:app --host 0.0.0.0 --port 8765
```

从任意目录启动时，在 uvicorn 参数中加入 `--app-dir "F:\学校\工坊\导盲帽子\Morning-Star"`。项目位置改变后应替换此路径。也可运行项目的 `scripts\start.bat`，该脚本会自动进入根目录。
