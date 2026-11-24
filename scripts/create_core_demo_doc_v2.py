from pathlib import Path

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from create_core_demo_doc import (
    add_bullets,
    add_callout,
    add_code as add_code_block,
    add_explanation as add_explanation_paragraph,
    add_heading,
    add_numbered,
    add_table,
    add_text,
    set_paragraph_keep,
    set_run_font,
)

OUTPUT = Path.home() / "Desktop" / "启明帽核心技能现场演示脚本_逐段讲解优化版.docx"


def add_code(doc, code, label="现场输入代码"):
    paragraph = add_code_block(doc, code, label)
    set_paragraph_keep(paragraph, keep_next=True)
    return paragraph


def add_explanation(doc, title, body):
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(2)
    paragraph.paragraph_format.space_after = Pt(6)
    label_run = paragraph.add_run(f"敲完后讲解  {title}  ")
    set_run_font(label_run, size=10, color="1F4E79", bold=True)
    body_run = paragraph.add_run(body)
    set_run_font(body_run, size=10)
    set_paragraph_keep(paragraph, keep_together=True)
    return paragraph


def build_document():
    from create_core_demo_doc import Document

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Pt(48)
    section.bottom_margin = Pt(48)
    section.left_margin = Pt(54)
    section.right_margin = Pt(54)

    normal = doc.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia",
        "Microsoft YaHei",
    )
    normal.font.size = Pt(10.5)
    normal.paragraph_format.line_spacing = 1.16
    normal.paragraph_format.space_after = Pt(6)
    title_properties = doc.styles["Title"]._element.get_or_add_pPr()
    title_border = title_properties.find(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pBdr"
    )
    if title_border is not None:
        title_properties.remove(title_border)

    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = header.add_run("启明帽 QimingHat  |  核心技能现场演示")
    set_run_font(run, size=8, color="6B7280")
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run("启明智能陪护管理平台")
    set_run_font(run, size=8, color="6B7280")

    title = doc.add_paragraph(style="Title")
    title_properties = title._p.get_or_add_pPr()
    title_border = title_properties.find(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pBdr"
    )
    if title_border is not None:
        title_properties.remove(title_border)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(6)
    run = title.add_run("启明帽核心技能现场演示脚本")
    set_run_font(run, size=24, color="000000", bold=True)
    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_after = Pt(16)
    run = subtitle.add_run("核心技术二与三现场讲解，核心技能一快速启动")
    set_run_font(run, size=13, color="4F6B8A")

    add_callout(
        doc,
        "演示目标",
        "核心技能一约 20—30 秒即可启动项目。核心技术二与三各安排约 2—3 分钟输入并讲解代码，保存并重启后到原管理平台验证实际功能。",
    )
    add_heading(doc, "一 角色与项目入口", 1)
    add_text(
        doc,
        "图中三位成员的工作分别对应启动与硬件画面接入、多帧视觉跟踪与预警、拍照识字与中文朗读。项目采用 FastAPI 提供登录页和管理页。视觉结果进入实时监测和告警记录，识字结果显示在实时监测页的文字区域。",
    )
    add_table(
        doc,
        ["成员", "职责模块"],
        [
            ("成员一", "启动项目并进入原管理平台"),
            ("成员二", "多帧跟踪、方位判断与接近预警"),
            ("成员三", "OCR 文字排序与中文朗读"),
        ],
        widths=[1.2, 5.4],
    )
    add_heading(doc, "二 共用准备", 1)
    add_numbered(
        doc,
        [
            "在 VS Code 打开项目文件夹 D:\\工坊\\Morning-Star-main。",
            "代码在 VS Code 编辑器中输入，集成终端选择 Command Prompt（CMD）运行命令。",
            "准备 Chrome 或 Edge、可用摄像头和扬声器，并允许浏览器访问摄像头。",
        ],
    )
    add_heading(doc, "核心技能一 项目启动", 2)
    add_text(
        doc,
        "在 VS Code 集成终端选择 CMD，确认当前目录为项目根目录，再输入下面两行。",
    )
    add_code(
        doc,
        r'''cd /d D:\工坊\Morning-Star-main
scripts\start.bat''',
        "成员一在 VS Code CMD 启动项目",
    )
    add_explanation(
        doc,
        "启动脚本",
        "项目已有 scripts/start.bat，会检查 Conda 环境并启动 backend.main:app。看到 Uvicorn running 后保持这个终端运行。",
    )
    add_text(doc, "浏览器打开 http://127.0.0.1:8765/ 登录后进入“实时监测”，本段约 20—30 秒。")

    add_heading(doc, "核心技术二 多帧视觉分析与接近趋势预警", 1, page_break_before=True)
    add_text(
        doc,
        "YOLO 识别每帧中的目标，TrackerAdapter 让同一目标跨帧保留 track_id。ApproachDetector 按目标 ID 记录检测框面积变化，AlertController 再根据连续帧结果更新 NORMAL、APPROACH、URGENT 状态。面积变化是图像空间趋势，不代表真实距离或碰撞时间。",
    )
    add_heading(doc, "2.1 让检测框跨帧保持同一 ID", 2)
    add_text(
        doc,
        "在 VS Code 打开 backend/services.py，进入 Runtime.detect_loop。前四段替换跟踪推理和检测框处理代码，后三段替换现有模型状态判断分支中的趋势分析代码。保留已有状态判断和 tracker 元数据代码。每组按“段标题、代码、敲完后讲解”的顺序操作，讲解词只需口述。全部完成后统一保存。代码首行与对应源码行对齐，内部缩进照抄。",
    )
    add_code(
        doc,
        r'''pred, tracker_name = await asyncio.to_thread(
    self.tracker.infer,
    self.model,
    img,
    source_id=device_id,
    conf=settings.yolo_confidence,
    imgsz=768,
)''',
        "成员二输入第 1 段",
    )
    add_explanation(
        doc,
        "启动跟踪推理",
        "asyncio.to_thread 将耗时的图像推理放到工作线程执行。tracker.infer 接收当前摄像头来源、模型、置信度和图像尺寸，持续跟踪同一来源中的目标。",
    )
    add_code(
        doc,
        r'''names = self.model.names
for box in pred[0].boxes:
    x1, y1, x2, y2 = [float(value) for value in box.xyxy[0].tolist()]
    conf = float(box.conf[0])
    cls = int(box.cls[0])''',
        "成员二输入第 2 段",
    )
    add_explanation(
        doc,
        "读取检测框",
        "循环逐个处理当前帧的检测结果。x1、y1、x2、y2 是检测框的左上角和右下角，conf 是置信度，cls 是类别编号。",
    )
    add_code(
        doc,
        r'''    track_id = None
    if getattr(box, "id", None) is not None:
        try:
            track_id = int(box.id[0])
        except (TypeError, ValueError, IndexError):
            track_id = None
    direction = self.direction((x1 + x2) / 2, img.shape[1])''',
        "成员二输入第 3 段",
    )
    add_explanation(
        doc,
        "关联目标与方位",
        "优先读取跟踪器提供的 box.id，并在 ID 缺失或格式异常时保留 None。再用检测框中心点和图像宽度判断目标位于左侧、前方还是右侧。",
    )
    add_code(
        doc,
        r'''    item = {
        "class": names[cls],
        "confidence": round(conf, 3),
        "bbox": [x1, y1, x2, y2],
        "direction": direction,
    }
    if track_id is not None:
        item["track_id"] = track_id
    result["objects"].append(item)''',
        "成员二输入第 4 段",
    )
    add_explanation(
        doc,
        "整理检测结果",
        "把类别、置信度、坐标和方位整理为一个对象。只有成功获得 track_id 时才写入该字段，然后将对象加入当前帧结果。",
    )

    add_heading(doc, "2.2 按 ID 统计面积变化并更新预警", 2)
    add_text(
        doc,
        "下面三段仍属于现有 model_status 条件分支。不要移动或删除前后的状态判断，按原位置依次替换趋势分析、结果回填和预警更新代码。",
    )
    add_code(
        doc,
        r'''observations = self.approach_detector.update(
    device_id, result["objects"], frame_width, frame_height,
)''',
        "成员二输入第 5 段",
    )
    add_explanation(
        doc,
        "计算接近趋势",
        "ApproachDetector 按摄像头来源和 track_id 区分目标，保存检测框面积历史，并结合画面关注区域判断 NORMAL、APPROACH 或 URGENT 候选。",
    )
    add_code(
        doc,
        r'''for observation in observations:
    if 0 <= observation.object_index < len(result["objects"]):
        result["objects"][observation.object_index]["approach"] = observation.as_dict()''',
        "成员二输入第 6 段",
    )
    add_explanation(
        doc,
        "附加分析结果",
        "object_index 将趋势结果对应回原检测框。as_dict 把面积变化、连续增长帧数和候选状态放进该目标的数据中。",
    )
    add_code(
        doc,
        r'''decisions = self.alert_controller.update(
    device_id, observations, time.time(), frame_index,
)''',
        "成员二输入第 7 段",
    )
    add_explanation(
        doc,
        "确认预警状态",
        "AlertController 汇总连续帧候选、告警冷却和目标丢失情况。达到状态条件后，后续流程会发送方向、语音和振动指令，并写入告警记录。",
    )
    add_text(
        doc,
        "按顺序将七段代码放入 Runtime.detect_loop 对应位置，全部输入后保存 backend/services.py。接着在 CMD 按 Ctrl+C 停止旧后端，再运行 scripts\\start.bat。Python 后端代码需重启服务后生效。",
    )
    add_heading(doc, "2.3 进入系统观察", 2)
    add_numbered(
        doc,
        [
            "登录后进入“实时监测”，选择电脑或帽端的真实画面。",
            "点击“启动摄像头”，让目标从画面中远处逐步靠近，观察检测框和方位变化。track_id、面积趋势与状态切换由后端处理。",
            "进入“告警记录”，核对后端写入的目标、时间、方位和播报内容。",
        ],
    )
    add_callout(
        doc,
        "振动状态",
        "后端会输出左右或双侧振动指令。实物马达输出仍处于禁用状态时，页面和记录可演示指令数据，不能当作马达实际震动验收。",
        "FFF4E5",
    )
    add_callout(
        doc,
        "预计用时",
        "逐段输入并紧接讲解七段代码约 2—3 分钟，再用真实摄像头在现有管理页验证检测框和告警记录。",
    )

    add_heading(doc, "核心技术三 OCR 文字排序与中文朗读", 1)
    add_text(
        doc,
        "系统使用当前摄像头画面拍照，后端调用 RapidOCR 生成文字框、文字和置信度，再按文字框位置恢复阅读顺序。前端把识别原文显示在实时监测下方，按标点分段并用浏览器中文语音朗读。",
    )
    add_heading(doc, "3.1 过滤低置信度结果并恢复阅读顺序", 2)
    add_text(
        doc,
        "在 VS Code 打开 backend/ocr.py，选中整个 _rapid_text 函数。按顺序输入下面五段，逐段补全同一个函数。每组按“段标题、代码、敲完后讲解”的顺序操作，讲解词只需口述。全部完成后再保存。",
    )
    add_code(
        doc,
        r'''def _rapid_text(result):
    """将 RapidOCR 结果整理为可阅读文本。"""
    # 兼容不同版本返回的 tuple、字典和列表
    items = result[0] if isinstance(result, tuple) and len(result) == 2 else result
    if items is None:
        return NO_TEXT''',
        "成员三输入第 1 段",
    )
    add_explanation(
        doc,
        "兼容识别器返回值",
        "函数接收 RapidOCR 结果。不同版本可能用 tuple 包装识别列表，代码先拆出列表；没有识别内容时立即返回统一提示 NO_TEXT。",
    )
    add_code(
        doc,
        r'''    rows = []
    for item in items:
        if isinstance(item, dict):
            text = item.get("text", "")
            score = item.get("score", 0)
            box = item.get("box", item.get("points", [[0, 0]]))
        elif len(item) >= 3:
            box, text, score = item[0], item[1], item[2]
        else:
            continue''',
        "成员三输入第 2 段",
    )
    add_explanation(
        doc,
        "读取识别项",
        "循环读取每个文字区域，分别取得文字、置信度和边界框。字典格式与列表格式都能处理，无法识别的格式直接跳过。",
    )
    add_code(
        doc,
        r'''        # 过滤空字符串和置信度过低的识别结果
        if str(text).strip() and float(score or 0) >= 0.25:
            y = min(float(point[1]) for point in box) if box else 0
            x = min(float(point[0]) for point in box) if box else 0
            rows.append((round(y / 12), x, str(text).strip()))''',
        "成员三输入第 3 段",
    )
    add_explanation(
        doc,
        "筛选并保存坐标",
        "只保留非空且置信度不低于 0.25 的文字。y 坐标用于归并到阅读行，x 坐标用于行内排序，再把坐标和文字存入 rows。",
    )
    add_code(
        doc,
        r'''    if not rows:
        return NO_TEXT
    # 先按行，再按每行的横向位置排序
    rows.sort(key=lambda value: (value[0], value[1]))
    lines = []
    last_y = None''',
        "成员三输入第 4 段",
    )
    add_explanation(
        doc,
        "恢复阅读顺序",
        "过滤后没有有效文字时返回统一提示。否则先按 y 坐标排出行序，再按 x 坐标排出每行从左到右的顺序，并准备逐行合并。",
    )
    add_code(
        doc,
        r'''    for y, _, text in rows:
        if lines and last_y is not None and abs(y - last_y) <= 1:
            lines[-1] += " " + text
        else:
            lines.append(text)
        last_y = y
    # 保留换行并限制返回长度，避免结果过长
    return "\n".join(lines)[:12_000]''',
        "成员三输入第 5 段",
    )
    add_explanation(
        doc,
        "合并并返回文本",
        "同一行的文字片段合并为一句，不同行保留换行。最后把结果限制在 12000 个字符内，避免单次识别返回过长。",
    )
    add_heading(doc, "3.2 重启服务并在原系统验证", 2)
    add_text(
        doc,
        "保存 backend/ocr.py 后，在 CMD 按 Ctrl+C 停止旧后端，再运行 scripts\\start.bat。服务启动后回到原管理平台并按 Ctrl+F5 刷新。",
    )
    add_numbered(
        doc,
        [
            "进入“实时监测”，启动电脑摄像头或选择已连接的帽端画面。",
            "将摄像头对准大字说明书或纸张，点击“识字并朗读”。",
            "识别结果显示在当前页面下方，浏览器使用中文语音播报；可点“重新朗读”或“停止”。",
        ],
    )
    add_explanation(
        doc,
        "前后端链路",
        "frontend/ocr-reader.js 截取摄像头画面并请求 /api/ocr/read。backend/main.py 校验登录和图片大小后调用 backend/ocr.py。识别文本返回实时监测页，由浏览器中文语音播报。",
    )
    add_callout(
        doc,
        "OCR 环境与用时",
        "OCR 需在启动脚本使用的 Conda 环境安装 rapidocr-onnxruntime 与 onnxruntime，依赖见 backend/requirements-conda.txt。浏览器中文语音由 Chrome 或 Edge 提供。五段代码输入并讲解约 2—3 分钟，随后实拍识字并听取朗读。",
    )

    trailing_paragraph = doc.paragraphs[-1]
    if not trailing_paragraph.text.strip():
        trailing_paragraph._element.getparent().remove(trailing_paragraph._element)

    add_heading(doc, "四 现场检查与排错", 1, page_break_before=True)
    troubleshooting_table = add_table(
        doc,
        ["现象", "检查方法"],
        [
            ("浏览器无法打开", "确认启动终端仍运行，检查 http://127.0.0.1:8765/api/health。"),
            ("端口 8765 占用", "结束旧后端进程后重试，或换端口并同步调整设备后端地址。"),
            ("摄像头没有画面", "本机使用 127.0.0.1 地址，确认浏览器权限、摄像头占用和所选来源。"),
            ("模型未就绪", "查看健康接口和启动日志，核对 backend/models/yolov8n.pt。"),
            ("识字提示 RapidOCR 未安装", "在实际 Conda 环境安装项目列出的 rapidocr-onnxruntime 与 onnxruntime，再重启后端。"),
            ("识别有结果但没有声音", "点击“重新朗读”，检查 Edge/Chrome 中文语音、系统音量和语音总开关。"),
        ],
        widths=[1.8, 4.9],
    )
    header_properties = troubleshooting_table.rows[0]._tr.get_or_add_trPr()
    repeat_header = header_properties.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tblHeader")
    if repeat_header is None:
        from docx.oxml import OxmlElement

        repeat_header = OxmlElement("w:tblHeader")
        header_properties.append(repeat_header)
    doc.core_properties.title = "启明帽核心技能现场演示脚本"
    doc.core_properties.subject = "项目启动、视觉跟踪与接近预警、OCR与中文朗读"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    build_document()
