"""Small, deterministic demo for the project's OCR post-processing path."""

from backend.ocr import NO_TEXT, _rapid_text


def collect_reading_text(raw_result, threshold=0.25):
    """Keep confident boxes and return normal reading order text."""
    items = raw_result[0] if isinstance(raw_result, tuple) else raw_result
    confident = []
    for box, text, score in items or []:
        if str(text).strip() and float(score) >= threshold:
            top = min(point[1] for point in box)
            left = min(point[0] for point in box)
            confident.append((round(top / 12), left, str(text).strip()))
    confident.sort(key=lambda item: (item[0], item[1]))
    return "\n".join(item[2] for item in confident) or NO_TEXT


detector_output = [
    ([[180, 72], [280, 72], [280, 105], [180, 105]], "请注意台阶", 0.96),
    ([[48, 18], [140, 18], [140, 48], [48, 48]], "启明帽", 0.99),
    ([[50, 72], [120, 72], [120, 105], [50, 105]], "前方入口", 0.91),
    ([[200, 18], [260, 18], [260, 48], [200, 48]], "噪声", 0.12),
]

text = collect_reading_text(detector_output)
print("OCR 识别结果：")
print(text)
print("\n发送给朗读模块：", text.replace("\n", "，"))
print("项目归一化函数结果：", _rapid_text(detector_output))
