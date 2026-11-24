from backend.vision import ApproachDetector, AlertController, analyze_object


def analyze_frames(frames, width=640, height=480):
    detector = ApproachDetector()
    controller = AlertController()
    rows = []
    for index, objects in enumerate(frames, 1):
        for obj in objects:
            ratio = analyze_object(obj["bbox"], width, height).center_x_ratio
            if ratio < 0.35:
                obj["direction"] = "left"
            elif ratio > 0.65:
                obj["direction"] = "right"
            else:
                obj["direction"] = "front"
        observations = detector.update("demo", objects, width, height)
        decisions = controller.update("demo", observations, index, index)
        for item in observations:
            alerts = [d for d in decisions if d.track_id == item.track_id]
            rows.append({
                "frame": index,
                "track_id": item.track_id,
                "bbox": item.bbox,
                "direction": item.direction,
                "focus": item.spatial.focus_region,
                "area": item.spatial.area_ratio,
                "growth": item.growth_rate,
                "candidate": item.level_hint,
                "alerts": [d.level.value for d in alerts],
                "voice": " ".join(d.voice_text or "" for d in alerts),
                "vibration": [d.vibration for d in alerts],
            })
    return rows
