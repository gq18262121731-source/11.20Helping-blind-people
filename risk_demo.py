"""启明辅行技能展示二。对一张照片运行检测、深度融合和演示分级。

把 risk_demo.jpg 放在脚本旁，在项目的 Python 环境中运行。
需要 YOLOv8n 权重、Depth Anything V2 Small 相对深度权重及其源码。
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps


HERE = Path(__file__).resolve().parent
LEVELS = ("待判定", "低", "中", "高")
COLORS = ("#a9b2c1", "#71e1ba", "#ffd166", "#ff7676")
NAMES = {"person": "行人", "chair": "椅子", "bicycle": "自行车", "car": "汽车"}


@dataclass
class Target:
    box: tuple[int, int, int, int]
    name: str
    depth: float | None = None
    near: float | None = None
    direction: str = ""
    level: int = 0


def fuse_depth(frame, detect, model):
    import torch
    from torch.nn import functional as F

    # 用同一帧进行检测和深度推理
    dets = detect(frame)
    image, size = model.image2tensor(frame)
    with torch.inference_mode():
        depth = model(image)[:, None]
        depth = F.interpolate(depth, size, mode="bilinear", align_corners=True)
    depth = depth[0, 0].cpu().numpy()
    # 按检测框提取目标深度
    for det in dets:
        x1, y1, x2, y2 = det.box
        roi = depth[y1:y2, x1:x2]
        valid = roi[np.isfinite(roi)]
        det.depth = float(np.median(valid)) if valid.size else None
    return depth, dets


def model_files(args):
    if not args.project.is_dir():
        raise ValueError(f"项目目录不存在：{args.project}")
    files = {
        "yolov8n.pt": args.yolo_weights,
        "depth_anything_v2_vits.pth": args.depth_weights,
        "dpt.py": args.depth_source / "depth_anything_v2" / "dpt.py" if args.depth_source else None,
    }
    skip = {"__pycache__", "node_modules", "venv", "env", "site-packages", "metric_depth"}
    for folder, subdirs, names in os.walk(args.project):
        subdirs[:] = sorted(d for d in subdirs if not d.startswith(".") and d not in skip)
        for name, path in files.items():
            if path is None and name in names:
                parent = Path(folder)
                if name != "dpt.py" or parent.name == "depth_anything_v2":
                    files[name] = parent / name
        if all(files.values()):
            break
    for name, option in (("yolov8n.pt", "--yolo-weights"),
                         ("depth_anything_v2_vits.pth", "--depth-weights"),
                         ("dpt.py", "--depth-source")):
        path = files[name]
        if name == "dpt.py" and path is None:
            continue  # 也支持在当前环境中已安装的官方模块
        if path is None or not path.is_file():
            raise ValueError(f"找不到 {path or name}，请用 {option} 指定路径。")
    return files["yolov8n.pt"], files["depth_anything_v2_vits.pth"], files["dpt.py"]


def run_models(frame, args):
    yolo_weights, depth_weights, depth_file = model_files(args)
    if depth_file:
        sys.path.insert(0, str(depth_file.resolve().parents[1]))
    import torch
    from ultralytics import YOLO

    try:
        from depth_anything_v2.dpt import DepthAnythingV2
    except ModuleNotFoundError as exc:
        if exc.name != "depth_anything_v2":
            raise
        raise ValueError("请用 --depth-source 指定 Depth-Anything-V2 源码目录。") from exc

    device = "cpu"
    if torch.cuda.is_available():
        device = "cuda:0"
    elif torch.backends.mps.is_available():
        device = "mps"
    yolo = YOLO(str(yolo_weights))
    model = DepthAnythingV2(encoder="vits", features=64, out_channels=[48, 96, 192, 384])
    model.load_state_dict(torch.load(depth_weights, map_location="cpu", weights_only=True))
    model.to(device).eval()

    def detect(image):
        result = yolo.predict(image, imgsz=640, conf=0.35, device=device, verbose=False)[0]
        height, width = image.shape[:2]
        targets = []
        for box, cls in zip(result.boxes.xyxy.tolist(), result.boxes.cls.tolist()):
            x1, y1, x2, y2 = (round(value) for value in box)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(width, x2), min(height, y2)
            if x1 < x2 and y1 < y2:
                name = result.names[int(cls)]
                targets.append(Target((x1, y1, x2, y2), NAMES.get(name, name)))
        return targets

    return fuse_depth(frame, detect, model)


def grade_targets(depth, targets):
    # 相对深度值越大表示越近。按本图的分位数映射到 0～1。
    finite = depth[np.isfinite(depth)]
    low, high = np.percentile(finite, [5, 95]) if finite.size else (0, 0)
    near_map = np.full(depth.shape, np.nan, dtype=np.float32)
    if high > low:
        valid = np.isfinite(depth)
        near_map[valid] = np.clip((depth[valid] - low) / (high - low), 0, 1)

    for target in targets:
        center = (target.box[0] + target.box[2]) / (2 * depth.shape[1])
        target.direction = "左侧" if center < 1 / 3 else "右侧" if center > 2 / 3 else "前方"
        if target.depth is None or high <= low:
            continue
        target.near = float(np.clip((target.depth - low) / (high - low), 0, 1))
        # 单图演示阈值。优先提示前方较近的目标。
        front = target.direction == "前方"
        if front and target.near >= 0.65:
            target.level = 3
        elif target.near >= (0.40 if front else 0.80):
            target.level = 2
        else:
            target.level = 1
    targets.sort(key=lambda t: (t.level, t.near or 0), reverse=True)
    return near_map


def load_font(size):
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    paths = (windows / "msyh.ttc", windows / "simhei.ttf",
             Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
             Path("/System/Library/Fonts/PingFang.ttc"))
    for path in paths:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    raise ValueError("找不到可用的中文字体。")


def render(photo, near_map, targets):
    colors = np.array([(43, 27, 108), (29, 107, 191), (38, 177, 145),
                       (241, 207, 70), (236, 77, 55)])
    scale = np.linspace(0, 1, len(colors))
    values = np.nan_to_num(near_map)
    heat = np.stack([np.interp(values, scale, channel) for channel in colors.T], axis=-1)
    heat[~np.isfinite(near_map)] = (43, 48, 59)
    heatmap = Image.fromarray(heat.astype(np.uint8))

    width, height = photo.size
    panel_w = 760
    panel_h = round(panel_w * height / width)
    margin, top = 20, 62
    left, right = margin, panel_w + 2 * margin
    canvas = Image.new("RGB", (2 * panel_w + 3 * margin, top + panel_h + 56), "#161d2b")
    for panel, x in ((photo, left), (heatmap, right)):
        canvas.paste(panel.resize((panel_w, panel_h), Image.Resampling.BILINEAR), (x, top))
    draw = ImageDraw.Draw(canvas)
    title, label = load_font(27), load_font(21)
    draw.text((left, 13), "目标检测与告警等级", font=title, fill="white")
    draw.text((right, 13), "相对深度图（暖色表示较近）", font=title, fill="white")

    # 保留两个目标，优先对照不同等级，避免检测框挡住画面。
    focus = targets[:1]
    if len(targets) > 1:
        focus.append(next((t for t in targets[1:] if t.level != focus[0].level), targets[1]))
    for index, target in enumerate(focus, 1):
        x1, y1, x2, y2 = target.box
        color = COLORS[target.level]
        near = f"{target.near:.2f}" if target.near is not None else "待判定"
        labels = (f"{index}  {target.name}  {target.direction}  {LEVELS[target.level]}",
                  f"{index}  相对接近度 {near}")
        for origin, caption in zip((left, right), labels):
            x = origin + round(x1 * panel_w / width)
            y = top + round(y1 * panel_h / height)
            end_x = origin + min(panel_w - 1, round(x2 * panel_w / width))
            end_y = top + min(panel_h - 1, round(y2 * panel_h / height))
            draw.rectangle((x, y, end_x, end_y), outline=color, width=3)
            tag_w = round(draw.textlength(caption, font=label)) + 16
            tag_x = min(x, origin + panel_w - tag_w)
            tag_y = max(top, y - 33)
            draw.rectangle((tag_x, tag_y, tag_x + tag_w, tag_y + 32), fill=color)
            draw.text((tag_x + 8, tag_y + 1), caption, font=label, fill="#111827")

    grade = LEVELS[targets[0].level] if targets else "未检测到目标"
    footer = f"最高告警等级  {grade}    ｜    单图演示分级依据  方位与相对深度"
    draw.text((left, top + panel_h + 12), footer, font=label, fill="white")
    return canvas


def main():
    parser = argparse.ArgumentParser(description="单图障碍物检测、相对深度与告警分级演示")
    parser.add_argument("image", type=Path, nargs="?", default=HERE / "risk_demo.jpg")
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="项目目录，默认当前目录")
    parser.add_argument("--yolo-weights", type=Path, help="YOLOv8n 权重路径")
    parser.add_argument("--depth-source", type=Path, help="Depth-Anything-V2 源码目录")
    parser.add_argument("--depth-weights", type=Path, help="Depth Anything V2 Small 相对深度权重路径")
    parser.add_argument("--output", type=Path, default=HERE / "risk_demo_result.png")
    parser.add_argument("--no-window", action="store_true", help="只保存结果图")
    args = parser.parse_args()
    try:
        if args.output.resolve() == args.image.resolve():
            raise ValueError("请为结果图另选文件名。")
        with Image.open(args.image) as source:
            photo = ImageOps.exif_transpose(source).convert("RGB")
        frame = np.ascontiguousarray(np.asarray(photo)[:, :, ::-1])
        print("正在识别目标并计算相对深度…", flush=True)
        depth, targets = run_models(frame, args)
        near_map = grade_targets(depth, targets)
        for target in targets:
            near = f"{target.near:.2f}" if target.near is not None else "待判定"
            print(f"{target.name}  {target.direction}  相对接近度 {near}  告警等级 {LEVELS[target.level]}")
        result = render(photo, near_map, targets)
        result.save(args.output)
        print(f"结果图已保存：{args.output.resolve()}")
        if not args.no_window:
            if os.name == "nt":
                os.startfile(str(args.output.resolve()))
            else:
                result.show(title="启明辅行 · 告警分级")
    except (OSError, ImportError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"运行失败：{exc}\n")


if __name__ == "__main__":
    main()
