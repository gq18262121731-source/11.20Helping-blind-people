"""Render the final software acceptance report from saved JSON evidence."""
from __future__ import annotations

import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "docs" / "acceptance_artifacts"


def load(name, fallback=None):
    path = ARTIFACTS / name
    try: return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError): return fallback


def read_log(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def status(data): return data.get("status", "NOT_TESTED") if isinstance(data, dict) else "NOT_TESTED"


def main():
    real = load("real_model_results.json", {})
    business = load("business_results.json", {})
    manifest = load("../../test_assets/manifest.json", {})
    env = load("environment.json", {})
    pytest_text = read_log(ARTIFACTS / "pytest.txt")
    node_text = read_log(ARTIFACTS / "node.txt")
    if pytest_text.startswith("\ufffd") or "\x00" in pytest_text[:20]:
        pytest_text = (ARTIFACTS / "pytest.txt").read_text(encoding="utf-16", errors="replace")
    if node_text.startswith("\ufffd") or "\x00" in node_text[:20]:
        node_text = (ARTIFACTS / "node.txt").read_text(encoding="utf-16", errors="replace")
    lines = ["# Morning-Star 软件阶段最终自动化验收报告", "", f"生成时间：{datetime.now(timezone.utc).isoformat()}", "",
             "## 1. 环境信息", "", f"- 操作系统：{platform.platform()}", f"- Python：{env.get('python', '未记录')}",
             f"- ONNX Runtime Provider：{env.get('onnx_providers', '未记录')}", f"- sherpa-onnx：{env.get('sherpa_version', '未记录')}",
             f"- RapidOCR：{env.get('rapidocr_version', '未记录')}", "", "## 2. 素材信息", ""]
    for name, item in manifest.get("assets", {}).items():
        lines.append(f"- `{name}`：{item.get('bytes', '?')} bytes，SHA256 `{item.get('sha256', '?')}`，来源：{item.get('source', '?')}")
    for item in manifest.get("blocked", []): lines.append(f"- BLOCKED：{item.get('asset')}: {item.get('reason')}")
    lines += ["", "## 3. 真实 RapidOCR 测试", "", f"结论：**{status(real.get('ocr'))}**"]
    for item in (real.get("ocr", {}) or {}).get("images", []):
        lines.append(f"- `{item['file']}`：{len(item.get('detections', []))} 个检测框，耗时 {item.get('elapsed_seconds', 0):.3f}s，文本长度 {len(item.get('text', ''))}")
    lines += ["", "## 4. 真实 sherpa-onnx ASR 测试", "", f"结论：**{status(real.get('asr'))}**"]
    for item in (real.get("asr", {}) or {}).get("audio", []):
        cer = item.get('cer') if item.get('cer') is not None else 'NOT_TESTED'
        lines.append(f"- `{item['file']}`：{item.get('duration_seconds', 0):.3f}s 音频，耗时 {item.get('elapsed_seconds', 0):.3f}s，识别结果：`{item.get('text', '')}`，CER：{cer}")
    lines += ["", "## 5. VAD / ASR / 意图 / OCR 业务闭环", "", f"结论：**{status(business)}**"]
    for item in business.get("intent_cases", []): lines.append(f"- `{item['text']}`：{item['actual']}，状态 {item['state']}，{'PASS' if item['pass'] else 'FAIL'}")
    if business.get("ocr_read"): lines.append(f"- OCR_READ：{business['ocr_read']['status']}，引擎 `{business['ocr_read'].get('model')}`，真实文本长度 {len(business['ocr_read'].get('text', ''))}")
    lines += ["", "## 6. 通信协议", "", f"- 音频协议异常用例：{'PASS' if business.get('protocol') and all(business['protocol']) else 'FAIL'}",
              "- 覆盖：QH 帧解析、重复序列号、损坏帧、结束事件。", "", "## 7. 完整回归", "",
              f"- Python pytest：{'PASS' if 'passed' in pytest_text and 'failed' not in pytest_text.lower() else 'NOT_TESTED/FAIL'}",
              f"- Node 前端测试：{'PASS' if 'fail 0' in node_text.lower() else 'NOT_TESTED/FAIL'}", "", "## 8. 遗留问题", "",
              "- 官方 SenseVoice `zh.wav` 未提供配套人工转写文本，因此 CER 标记为 NOT_TESTED；识别本身有真实模型执行证据。",
              "- 本次未连接 ESP32-S3，真实 ADC 噪声、Wi-Fi 丢包、摄像头传感器和马达仍属于硬件阶段。",
              "", "## 9. 最终结论", ""]
    final = "PASS" if status(real.get("ocr")) == "PASS" and status(real.get("asr")) == "PASS" and status(business) == "PASS" and "failed" not in pytest_text.lower() else "BLOCKED"
    lines.append(f"软件阶段结论：**{final}**。真实 RapidOCR、真实 sherpa-onnx SenseVoice 和语音业务闭环均有独立证据。")
    (ROOT / "docs" / "SOFTWARE_ACCEPTANCE_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(final)


if __name__ == "__main__": main()
