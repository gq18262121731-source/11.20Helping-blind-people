param(
    [string]$DeviceId = ''
)

$ErrorActionPreference = 'Stop'
$preferredAdb = Join-Path $env:LOCALAPPDATA 'QimingTools\Android\platform-tools\adb.exe'
if (Test-Path -LiteralPath $preferredAdb) {
    $adb = $preferredAdb
} else {
    $command = Get-Command adb -ErrorAction SilentlyContinue
    if (-not $command) {
        throw '未找到 ADB。请安装 Android platform-tools，或将 adb 加入 PATH。'
    }
    $adb = $command.Source
}

$address = 'http://127.0.0.1:8765'
try {
    $health = Invoke-RestMethod -Uri "$address/api/health" -TimeoutSec 10
} catch {
    throw "电脑后端未就绪，请先运行 scripts\start.bat。$($_.Exception.Message)"
}
if ($health.ok -ne $true) {
    throw '8765 端口可访问，但后端健康检查未通过。'
}

$deviceOutput = & $adb devices -l
if ($LASTEXITCODE -ne 0) {
    throw '无法读取 ADB 设备列表，请检查 USB 连接。'
}
$devices = @(
    foreach ($line in $deviceOutput) {
        if ($line -match '^(\S+)\s+(device|unauthorized|offline)\b') {
            [pscustomobject]@{ Id = $Matches[1]; Status = $Matches[2] }
        }
    }
)

if ($DeviceId) {
    $selected = @($devices | Where-Object { $_.Id -eq $DeviceId })
    if ($selected.Count -ne 1) {
        throw "未找到设备 $DeviceId，请检查 USB 连接和设备编号。"
    }
    if ($selected[0].Status -eq 'unauthorized') {
        throw "设备 $DeviceId 尚未授权，请解锁平板并允许这台电脑进行 USB 调试后重试。"
    }
    if ($selected[0].Status -ne 'device') {
        throw "设备 $DeviceId 当前离线，请重新连接 USB 后重试。"
    }
} else {
    $selected = @($devices | Where-Object { $_.Status -eq 'device' })
    if ($selected.Count -gt 1) {
        throw "检测到多个已授权设备，请使用 -DeviceId 指定平板。设备编号为 $($selected.Id -join ', ')。"
    }
    if ($selected.Count -eq 0) {
        if (@($devices | Where-Object { $_.Status -eq 'unauthorized' }).Count -gt 0) {
            throw '平板尚未授权，请解锁平板并允许这台电脑进行 USB 调试后重试。'
        }
        throw '未检测到已连接且已授权的 Android 设备，请检查 USB 连接并开启 USB 调试。'
    }
}

$serial = $selected[0].Id
& $adb -s $serial reverse tcp:8765 tcp:8765 | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "设备 $serial 的 USB 后端端口映射失败。"
}
$reverseOutput = & $adb -s $serial reverse --list
if ($LASTEXITCODE -ne 0 -or -not ($reverseOutput -match '\btcp:8765\s+tcp:8765\b')) {
    throw "设备 $serial 的 USB 后端端口映射未确认成功。"
}

Write-Output "已连接平板 $serial，电脑后端健康检查通过。"
Write-Output "请在平板浏览器手动打开 $address"
Write-Output '保持 USB 连接。重新插拔后可再次运行本脚本恢复连接。'
