from backend.ocr import NO_TEXT


def build_reading_text(result, threshold=0.25):
    items = result[0] if isinstance(result, tuple) else result
    rows = []
    for item in items or []:
        if isinstance(item, dict):
            box = item.get("box", item.get("points", []))
            text = item.get("text", "")
            score = item.get("score", 0)
        else:
            box, text, score = item[:3]
        text = str(text).strip()
        if not text or float(score) < threshold or not box:
            continue
        top = min(point[1] for point in box)
        left = min(point[0] for point in box)
        row = round(top / 12)
        rows.append((row, left, text))

    rows.sort(key=lambda value: (value[0], value[1]))
    lines = []
    previous_row = None
    for row, left, text in rows:
        same_line = (
            previous_row is not None
            and abs(row - previous_row) <= 1
        )
        if same_line:
            lines[-1] += " " + text
        else:
            lines.append(text)
        previous_row = row
    reading_text = "\n".join(lines)
    return reading_text[:12_000] or NO_TEXT
