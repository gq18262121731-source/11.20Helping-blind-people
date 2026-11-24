from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = Path.home() / "Desktop"
OUTPUT = DESKTOP / "启明帽核心技能现场演示脚本.docx"


def set_cell_shading(cell, fill):
    properties = cell._tc.get_or_add_tcPr()
    shading = properties.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        properties.append(shading)
    shading.set(qn("w:fill"), fill)


def set_cell_border(cell, color="D9E2F3", size="6"):
    properties = cell._tc.get_or_add_tcPr()
    borders = properties.first_child_found_in("w:tcBorders")
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        properties.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = "w:" + edge
        element = borders.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            borders.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), size)
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), color)


def set_run_font(run, name="Microsoft YaHei", size=None, color=None, bold=None):
    run.font.name = name
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), name)
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), name)
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), name)
    if size is not None:
        run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = RGBColor.from_string(color)
    if bold is not None:
        run.bold = bold


def set_paragraph_keep(paragraph, keep_next=False, keep_together=False):
    properties = paragraph._p.get_or_add_pPr()
    if keep_next:
        properties.append(OxmlElement("w:keepNext"))
    if keep_together:
        properties.append(OxmlElement("w:keepLines"))


def add_text(doc, text, style=None, bold_prefix=None):
    paragraph = doc.add_paragraph(style=style)
    if bold_prefix and text.startswith(bold_prefix):
        prefix = paragraph.add_run(bold_prefix)
        set_run_font(prefix, bold=True)
        rest = paragraph.add_run(text[len(bold_prefix):])
        set_run_font(rest)
    else:
        run = paragraph.add_run(text)
        set_run_font(run)
    return paragraph


def add_code(doc, code, label="现场输入代码"):
    label_paragraph = doc.add_paragraph()
    label_paragraph.paragraph_format.space_before = Pt(6)
    label_paragraph.paragraph_format.space_after = Pt(2)
    set_paragraph_keep(label_paragraph, keep_next=True)
    label_run = label_paragraph.add_run(label)
    set_run_font(label_run, size=9, color="4F6B8A", bold=True)
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.left_indent = Inches(0.18)
    paragraph.paragraph_format.right_indent = Inches(0.12)
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(7)
    paragraph.paragraph_format.line_spacing = 1.0
    set_paragraph_keep(paragraph, keep_together=True)
    properties = paragraph._p.get_or_add_pPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), "F3F6FA")
    properties.append(shading)
    border = OxmlElement("w:pBdr")
    for edge in ("top", "left", "bottom", "right"):
        item = OxmlElement("w:" + edge)
        item.set(qn("w:val"), "single")
        item.set(qn("w:sz"), "4")
        item.set(qn("w:space"), "4")
        item.set(qn("w:color"), "D9E2F3")
        border.append(item)
    properties.append(border)
    for index, line in enumerate(code.strip("\n").splitlines()):
        run = paragraph.add_run(line)
        set_run_font(run, name="Consolas", size=8.8, color="1F2937")
        if index < len(code.strip("\n").splitlines()) - 1:
            run.add_break()
    return paragraph


def add_callout(doc, title, body, fill="EAF2F8"):
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    cell = table.cell(0, 0)
    set_cell_shading(cell, fill)
    set_cell_border(cell, "C6D8EA", "6")
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_before = Pt(4)
    paragraph.paragraph_format.space_after = Pt(4)
    title_run = paragraph.add_run(title + "  ")
    set_run_font(title_run, size=10, color="1F4E79", bold=True)
    body_run = paragraph.add_run(body)
    set_run_font(body_run, size=10)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)


def add_bullets(doc, items):
    for item in items:
        paragraph = doc.add_paragraph(style="List Bullet")
        paragraph.paragraph_format.space_after = Pt(3)
        run = paragraph.add_run(item)
        set_run_font(run, size=10)


def add_numbered(doc, items):
    for index, item in enumerate(items, start=1):
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(4)
        run = paragraph.add_run(f"{index}. {item}")
        set_run_font(run, size=10)


def add_table(doc, headers, rows, widths=None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        set_cell_shading(cell, "1F4E79")
        set_cell_border(cell, "C6D8EA")
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        run = paragraph.add_run(header)
        set_run_font(run, size=9.5, color="FFFFFF", bold=True)
    for row_index, row in enumerate(rows):
        cells = table.add_row().cells
        for col_index, value in enumerate(row):
            cell = cells[col_index]
            set_cell_border(cell)
            if row_index % 2 == 1:
                set_cell_shading(cell, "F7F9FC")
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(2)
            run = paragraph.add_run(str(value))
            set_run_font(run, size=9)
            if widths:
                cell.width = Inches(widths[col_index])
    doc.add_paragraph().paragraph_format.space_after = Pt(0)
    return table


def add_heading(doc, text, level=1, page_break_before=False):
    paragraph = doc.add_heading(text, level=level)
    paragraph.paragraph_format.page_break_before = page_break_before
    for run in paragraph.runs:
        set_run_font(run, size={1: 16, 2: 13, 3: 11}[level], color="000000", bold=True)
    set_paragraph_keep(paragraph, keep_next=True)
    return paragraph


def add_explanation(doc, title, body):
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(2)
    paragraph.paragraph_format.space_after = Pt(6)
    title_run = paragraph.add_run(title + "  ")
    set_run_font(title_run, size=10, color="1F4E79", bold=True)
    body_run = paragraph.add_run(body)
    set_run_font(body_run, size=10)
    return paragraph


def add_picture_with_caption(doc, image_path, caption):
    if not image_path.exists():
        return
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_before = Pt(2)
    paragraph.paragraph_format.space_after = Pt(2)
    paragraph.add_run().add_picture(str(image_path), width=Inches(3.85))
    caption_paragraph = doc.add_paragraph()
    caption_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    caption_paragraph.paragraph_format.space_after = Pt(7)
    caption_run = caption_paragraph.add_run(caption)
    set_run_font(caption_run, size=8.5, color="6B7280")


def page_break(doc):
    paragraph = doc.add_paragraph()
    paragraph.add_run().add_break(WD_BREAK.PAGE)


def build_document():
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.72)
    section.bottom_margin = Inches(0.72)
    section.left_margin = Inches(0.78)
    section.right_margin = Inches(0.78)
    section.header_distance = Inches(0.32)
    section.footer_distance = Inches(0.32)

    normal = doc.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.16
    for style_name, size in (("Title", 24), ("Heading 1", 16), ("Heading 2", 13), ("Heading 3", 11)):
        style = doc.styles[style_name]
        style.font.name = "Microsoft YaHei"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0, 0, 0)
        if style_name != "Title":
            style.font.bold = True
    title_properties = doc.styles["Title"]._element.get_or_add_pPr()
    title_border = title_properties.find(qn("w:pBdr"))
    if title_border is not None:
        title_properties.remove(title_border)

    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    header_run = header.add_run("启明帽 QimingHat  |  核心技能现场演示")
    set_run_font(header_run, size=8, color="6B7280")
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_run = footer.add_run("现场脚本 · 以当前源码为准")
    set_run_font(footer_run, size=8, color="6B7280")

    title = doc.add_paragraph(style="Title")
    title_properties = title._p.get_or_add_pPr()
    title_border = title_properties.find(qn("w:pBdr"))
    if title_border is not None:
        title_properties.remove(title_border)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(7)
    title_run = title.add_run("启明帽核心技能现场演示脚本")
    set_run_font(title_run, size=24, color="000000", bold=True)
    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_after = Pt(18)
    subtitle_run = subtitle.add_run("项目启动、视觉接近趋势、拍照识字与中文朗读")
    set_run_font(subtitle_run, size=13, color="4F6B8A")

    add_callout(
        doc,
        "演示目的",
        "让输入者可以边敲代码边讲解，现场看到浏览器前端、视觉趋势输出和文字朗读结果。每个核心技能设计为约 2—3 分钟，代码使用项目已有入口和模块。",
    )
    add_heading(doc, "一 先读懂图中的三段分工", 1)
    add_text(doc, "图中三段核心技能可以落到当前项目的三个入口。成员一负责项目启动、摄像头来源选择以及设备与网页的数据联通。成员二负责 YOLO 检测之后的跟踪 ID、方位判断、关注区域和接近趋势预警。成员三负责拍照识字、阅读顺序整理和中文分段朗读。现场启动项目后会看到演示页，后两位成员分别输入算法函数、保存并刷新同一页面，再按按钮查看输出。")
    add_table(
        doc,
        ["演示段", "源码落点", "现场可见结果"],
        [
            ("核心技能一 项目启动", "tools/skill_demo/live/start.py、backend/main.py、frontend/index.html", "浏览器进入项目启动的演示工作台，保留原项目登录页入口"),
            ("核心技能二 视觉分析", "backend/vision/spatial_analyzer.py、approach_detector.py、alert_controller.py", "同一 track_id 的框逐帧变大，状态由 NORMAL 进入 APPROACH，再触发 URGENT"),
            ("核心技能三 识字朗读", "backend/ocr.py、backend/main.py、frontend/ocr-reader.js", "识别结果按阅读顺序显示，浏览器按短句进行中文朗读"),
        ],
        widths=[1.35, 2.65, 2.75],
    )
    add_heading(doc, "二 现场共用准备", 1)
    add_numbered(
        doc,
        [
            "打开 PowerShell，进入项目根目录。下面所有相对路径都从 D:\\工坊\\Morning-Star-main 开始。",
            "确认 Python 解释器和项目依赖。当前机器已经验证 C:\\Anaconda\\envs\\yolov8 可导入 FastAPI、Uvicorn、OpenCV、NumPy、Ultralytics、httpx 和 websockets。",
            "如果现场电脑的环境名称不同，把代码中的解释器路径替换为该电脑实际通过依赖检查的 Python。",
            "真实摄像头演示需要 Chrome 或 Edge 的摄像头权限。无摄像头时可启用项目的模拟设备链路，文档会标明模拟结果。",
        ],
    )
    add_heading(doc, "2.1 准备独立的现场代码文件", 2)
    add_code(
        doc,
        r'''cd "D:\工坊\Morning-Star-main"
$py = "C:\Anaconda\envs\yolov8\python.exe"
& $py tools\skill_demo\manage.py reset --target all''',
        "赛前准备 不计入现场输入时长",
    )
    add_explanation(doc, "每次演示开始前", "命令创建 tools/skill_demo/live 下的三个演示入口文件，其中视觉和 OCR 文件带有待编写状态。演示页只有在成员输入并保存完整代码后才会启用运行按钮。已有现场版本会自动备份到 .workbench/skill-demo/backups。")
    add_heading(doc, "2.2 准备 OCR 的隔离依赖", 2)
    add_code(
        doc,
        r'''& $py -m pip install --target .workbench\skill-demo-qa\ocr\vendor --no-deps rapidocr_onnxruntime onnxruntime pyclipper shapely coloredlogs humanfriendly flatbuffers protobuf''',
        "赛前准备 仅首次需要",
    )
    add_explanation(doc, "单独安装位置", "FastAPI 演示服务会从 .workbench/skill-demo-qa/ocr/vendor 加载 RapidOCR。依赖安装在项目隔离目录，不会替换现有 Conda 环境中的 NumPy 或模型包。首次运行时 RapidOCR 可能下载 OCR 模型，请提前联网运行一次。当前项目图片已经用这套真实识字引擎验证过。")
    add_callout(doc, "重要边界", "视觉接近判断使用图像空间中的边界框面积和位置变化，表示视觉趋势，不等同于真实距离、碰撞时间或绝对安全判断。", "FFF4E5")
    add_text(doc, "以下代码块按现场敲入顺序排列。每个代码块后都有口述重点和检查点。项目通过 tools/skill_demo 的隔离入口提供演示页面。核心技能二使用固定检测框回放，直接调用项目已有分析模块。核心技能三会真实运行 RapidOCR，并在网页显示检测框、整理后的文本和朗读控制。")

    page_break(doc)
    add_heading(doc, "核心技能一 项目启动与前端显示", 1)
    add_text(doc, "这一段对应图中“进入项目目录，运行启动脚本，打开浏览器，进入登录平台，选择实时监测并接入摄像头”的流程。演示入口会启动项目原有 FastAPI 应用，同时提供一个独立的核心技能演示页。前端由 FastAPI 提供，不需要 npm 构建。")
    add_heading(doc, "1.1 在启动文件中逐行输入", 2)
    add_text(doc, "打开 tools/skill_demo/live/start.py，把初始化占位内容替换为下面代码并保存。")
    add_code(
        doc,
        r'''from pathlib import Path
import os
import sys
import webbrowser
from threading import Thread
from time import sleep
from urllib.request import urlopen

import uvicorn

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".workbench/skill-demo-qa/ocr/vendor"))
os.chdir(ROOT)
os.environ["DATABASE_PATH"] = str(ROOT / ".workbench/skill-demo/demo.sqlite")
PORT = int(os.environ.get("SKILL_DEMO_PORT", "8765"))

from tools.skill_demo.support import attach_demo
from backend.main import app

attach_demo(app)

def open_when_ready():
    for attempt in range(90):
        try:
            with urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=1):
                webbrowser.open(f"http://127.0.0.1:{PORT}/skill-demo")
            return
        except OSError:
            sleep(1)

if __name__ == "__main__":
    Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=PORT)''',
        "在 tools/skill_demo/live/start.py 中逐行输入",
    )
    add_explanation(doc, "导入区", "准备项目根路径、环境变量、浏览器启动和 Uvicorn 所需模块。sys.path 将已安装的隔离 OCR 依赖加入搜索路径。")
    add_explanation(doc, "应用连接区", "ROOT 回到 Morning-Star-main 项目根目录，os.chdir 固定数据库等相对路径。attach_demo 把可编辑演示页和接口接到原有 FastAPI app。")
    add_explanation(doc, "自动打开区", "后台线程等待健康检查接口可访问后，自动打开 /skill-demo 页面，每秒检查一次，最多等待 90 秒。原项目登录入口仍由 / 提供。")
    add_explanation(doc, "启动区", "uvicorn.run 默认监听 8765 端口并保持当前终端运行。端口已占用时，可在运行前设置 SKILL_DEMO_PORT，例如 8766。每次演示请求都会重新读取 live/vision.py 和 live/ocr.py。")

    add_heading(doc, "1.2 保存并启动", 2)
    add_code(
        doc,
        r'''& $py -m tools.skill_demo.live.start''',
        "运行现场启动代码",
    )
    add_explanation(doc, "运行结果", "命令导入刚写好的启动入口，浏览器自动打开 /skill-demo。视觉和 OCR 状态会显示待编写，运行按钮处于禁用状态。")
    add_callout(doc, "刷新确认", "在浏览器按 Ctrl+F5 强制刷新。确认页面显示项目演示工作台和两块待编写区域，后续成员保存代码并刷新即可运行。")

    add_heading(doc, "1.3 原项目实时摄像头入口", 2)
    add_numbered(
        doc,
        [
            "先说明电脑摄像头和 ESP32-S3 帽端都走同一个管理平台，前端只展示当前选择的来源。",
            "选择电脑摄像头后，浏览器采集 JPEG，通过 /api/local-camera/frame 发送给后端；后端把图像交给 YOLO，再把检测结果通过 WebSocket 推回页面。",
            "选择 ESP32 帽端后，页面等待设备的 WebSocket 图像。设备离线时显示等待画面，不会偷偷切回电脑摄像头。",
            "出现检测框、类别、方位和状态后，再点击“启动识别播报”或“测试播报”。这能把画面、检测和声音三个结果连起来。",
        ],
    )
    add_text(doc, "原项目真实摄像头页面仍可从演示页的“打开项目登录页”链接进入。登录后选择“实时环境监测”，再启动电脑摄像头或查看 ESP32 帽端画面。演示工作台的固定检测框用于算法讲解，不会写入设备记录。")
    add_heading(doc, "1.4 这一段的口述稿", 2)
    add_text(doc, "“我先准备 OCR 隔离依赖，再在启动入口中导入原有 FastAPI 应用，把演示页挂载到同一个服务。应用启动后会等待健康检查通过，再自动打开浏览器。前端现在显示待编写的视觉和 OCR 区域，后续成员保存代码并刷新页面就能看到结果。”")

    page_break(doc)
    add_heading(doc, "核心技能二 多帧跟踪与接近趋势预警", 1)
    add_text(doc, "图中第二段的关键不是单帧识别，而是“同一个人”在连续画面里的变化。项目用 YOLO 提供检测框，再用 BoT-SORT 或 ByteTrack 维持 track_id，之后由 ApproachDetector 保存每个 ID 的面积历史，由 AlertController 进行 NORMAL、APPROACH、URGENT 状态确认。成员二编辑 live/vision.py，保存后按 Ctrl+F5 刷新页面，再点击“回放多帧分析”。")
    add_heading(doc, "2.1 在视觉文件中逐行输入", 2)
    add_text(doc, "打开 tools/skill_demo/live/vision.py，把等待编写的函数替换为下面代码并保存。")
    add_code(
        doc,
        r'''from backend.vision import ApproachDetector, AlertController, analyze_object

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
                "frame": index, "track_id": item.track_id,
                "bbox": item.bbox, "direction": item.direction,
                "focus": item.spatial.focus_region,
                "area": item.spatial.area_ratio,
                "growth": item.growth_rate,
                "candidate": item.level_hint,
                "alerts": [d.level.value for d in alerts],
                "voice": " ".join(d.voice_text or "" for d in alerts),
                "vibration": [d.vibration for d in alerts],
            })
    return rows''',
        "成员二在 tools/skill_demo/live/vision.py 中逐行输入",
    )
    add_explanation(doc, "导入与初始化", "导入项目已有的面积历史和预警状态机。detector 和 controller 在帧循环外创建，因此同一跟踪 ID 的面积历史能连续保留。")
    add_explanation(doc, "frames 输入", "演示页面提供七帧检测框。ID 7 的框逐帧变大，ID 12 在右侧稳定移动。每帧对象保留类别、置信度、框坐标和跟踪 ID。")
    add_explanation(doc, "analyze_object", "函数把框转换为归一化中心、面积、画面带和中央关注区。0.35 与 0.65 是项目默认的左右方位阈值。这里的面积是图像空间比例，不是米制距离。")
    add_explanation(doc, "状态与返回值", "每个结果行把框坐标、方向、关注区域、面积占比、增长率、候选状态、实际告警、语音和振动指令返回给网页。告警字段来自 AlertController 的实际决策。")
    add_explanation(doc, "刷新后运行", "保存 live/vision.py，浏览器按 Ctrl+F5。代码状态就绪后点击“回放多帧分析”。页面画出检测框，并逐帧高亮表格数据。")
    add_callout(doc, "预期画面", "ID 7 从 NORMAL 进入 APPROACH 候选，再进入 URGENT 候选。状态机连续确认后触发 URGENT、双侧振动和“前方有行人，请注意”。ID 12 保持稳定，不触发接近告警。", "EAF2F8")

    add_heading(doc, "2.2 现场口述重点", 2)
    add_bullets(
        doc,
        [
            "“YOLO 先回答画面里有什么，跟踪器再回答连续帧里的这个目标是不是同一个。”",
            "“中心横坐标决定左侧、前方或右侧，框面积变化用来表达视觉上的接近趋势。”",
            "“同一 ID 的历史放在 detector 中，状态确认放在 controller 中，两个对象都必须在循环外初始化。”",
            "“系统可以根据趋势触发语音和方向振动，但不能把这个输出描述成真实距离或碰撞时间。”",
        ],
    )
    page_break(doc)
    add_heading(doc, "核心技能三 OCR 文字提取与中文朗读", 1)
    add_text(doc, "图中第三段采用“先识别，再整理阅读顺序，最后交给 TTS”的链路。演示接口真实读取中文说明图片，再把文字框、识别文字和置信度交给成员编写的函数。保存 live/ocr.py 并刷新后，网页显示检测框、排序后的全文和置信度，浏览器再分段朗读。")
    add_picture_with_caption(
        doc,
        ROOT / "tools" / "skill_demo" / "assets" / "instructions.png",
        "现场可使用的中文说明图片示例，项目真实 RapidOCR 已识别出 8 个文字框",
    )
    add_heading(doc, "3.1 在 OCR 文件中逐行输入", 2)
    add_text(doc, "打开 tools/skill_demo/live/ocr.py，把等待编写的函数替换为下面代码并保存。")
    add_code(
        doc,
        r'''from backend.ocr import NO_TEXT

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
        rows.append((round(top / 12), left, text))

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
    return "\n".join(lines)[:12_000] or NO_TEXT''',
        "成员三在 tools/skill_demo/live/ocr.py 中逐行输入",
    )
    add_explanation(doc, "第 1 行", "沿用项目中的 NO_TEXT 常量，保证无文字时的提示和接口输出一致。")
    add_explanation(doc, "过滤步骤", "每项包含文字框、识别文字和置信度。低于 0.25 的识别结果会被过滤。")
    add_explanation(doc, "排序步骤", "先取每个文字框的顶部和左侧坐标，把顶部相近的文字归入同一行，再从上到下、从左到右排序。")
    add_explanation(doc, "返回步骤", "同一行用空格连接，不同行用换行连接。网页收到全文后，按标点分段并调用浏览器中文语音合成。")
    add_callout(doc, "预期结果", "真实识字接口会返回 8 个中文文字框。网页显示检测框、排序文本和置信度，按钮会播放中文分段朗读。")

    add_heading(doc, "3.2 刷新页面并朗读", 2)
    add_numbered(
        doc,
        [
            "保存 tools/skill_demo/live/ocr.py，浏览器按 Ctrl+F5。",
            "确认 OCR 区域状态就绪，点击“真实识字”并等待检测结果。",
            "查看说明图片上的检测框、阅读顺序文本和置信度列表。",
            "点击“中文朗读”开始播报，点击“停止朗读”立即取消。",
        ],
    )
    add_heading(doc, "3.3 直接调用识字接口", 2)
    add_code(
        doc,
        r'''$result = Invoke-RestMethod -Uri "http://127.0.0.1:8765/skill-demo/api/ocr" -Method Post
$result.text
$result.engine''',
        "可选的接口检查 不计入现场输入时长",
    )
    add_explanation(doc, "接口检查", "这段 PowerShell 直接调用演示 API 使用的同一个本机 RapidOCR 识字函数。返回文本和引擎名称，可用来核对网页显示的数据。")
    add_explanation(doc, "配置条件", "RapidOCR 依赖需要按赛前步骤安装在隔离目录，启动入口会将该路径加入 Python 的模块搜索路径。示例图片与结果 JSON 已经通过实际 RapidOCR 1.4.4 和 ONNX Runtime 1.23.2 CPU 验证。")
    add_callout(doc, "语音边界", "浏览器中文语音合成依赖 Chrome 或 Edge 的中文语音和系统音量。第一次播放需要用户点击页面按钮。", "FFF4E5")

    add_heading(doc, "3.4 这一段的口述稿", 2)
    add_text(doc, "“OCR 返回的是一组文字框，不是按阅读顺序拼好的段落。我们先检查文字内容和置信度，再从坐标恢复阅读顺序。点击朗读后，浏览器把长文本拆成短句，逐段使用中文语音播放。识字和朗读可以分别演示，也可以随时停止。”")

    page_break(doc)
    add_heading(doc, "四 故障处理与现场检查表", 1)
    add_table(
        doc,
        ["现象", "处理动作", "讲解结论"],
        [
            ("浏览器打不开", "确认 Uvicorn 终端仍在运行，访问 http://127.0.0.1:8765/api/health", "先验证后端，再检查浏览器地址"),
            ("页面能打开但摄像头没有画面", "使用本机 127.0.0.1 地址，允许浏览器摄像头权限，确认摄像头未被其他程序占用", "浏览器采集需要安全上下文和设备权限"),
            ("YOLO 状态 unavailable", "检查 backend/models/yolov8n.pt 和启动环境，重启后端", "没有权重时不伪造识别结果"),
            ("视觉样例没有 URGENT", "确认 detector/controller 在循环外初始化，完整输入七帧", "趋势需要历史和连续帧确认"),
            ("OCR 返回 503", "安装 RapidOCR，或配置 OPENAI_BASE_URL、OPENAI_MODEL 后重启", "识字模型服务与 YOLO 服务是两条独立链路"),
            ("识字成功但没有声音", "点击重新朗读，确认中文语音、系统音量和浏览器权限", "文字识别和浏览器 TTS 是两个阶段"),
        ],
        widths=[1.55, 3.0, 2.2],
    )
    add_heading(doc, "五 演示结束时的总结句", 1)
    add_text(doc, "“本次演示从项目启动开始，先让浏览器看到统一管理平台，再用同一 track_id 的多帧框变化展示视觉接近趋势，最后把当前画面中的文字按阅读顺序整理并朗读出来。代码调用的是项目现有的启动、视觉决策和 OCR 前端链路，模拟回放部分只用于稳定展示算法，不代表真实硬件验收。”")
    add_heading(doc, "六 关联源码位置", 1)
    add_table(
        doc,
        ["文件", "现场讲解用途"],
        [
            ("tools/skill_demo/support.py", "隔离演示页面、固定视觉回放和真实 RapidOCR 接口"),
            ("tools/skill_demo/manage.py", "准备空白输入文件，安全备份现场演示代码"),
            ("backend/main.py", "页面路由、健康检查和 /api/ocr/read 接口"),
            ("backend/services.py", "图像解码、YOLO 推理、检测结果广播和趋势状态调用"),
            ("backend/vision/tracker.py", "BoT-SORT、ByteTrack 和 predict 回退"),
            ("backend/vision/spatial_analyzer.py", "中心、面积、中央关注区和画面带"),
            ("backend/vision/approach_detector.py", "按 track_id 保存面积历史和接近候选"),
            ("backend/vision/alert_controller.py", "NORMAL、APPROACH、URGENT 状态确认"),
            ("backend/ocr.py", "图片校验、RapidOCR、置信度过滤和坐标排序"),
            ("frontend/ocr-reader.js", "识字请求、显示、取消、分段和中文朗读"),
        ],
        widths=[2.8, 3.95],
    )

    # Add document properties without inventing an author identity.
    doc.core_properties.title = "启明帽核心技能现场演示脚本"
    doc.core_properties.subject = "项目启动、视觉接近趋势、拍照识字与中文朗读"
    doc.core_properties.comments = "Generated from the current Morning-Star project source."
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    build_document()
