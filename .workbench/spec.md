---
surface: desktop
domain: system
title: 启明智能陪护控制台
subject: 帽端设备、共享位置、环境事件与多模态交互任务
owner: 陪护值守人员
roles:
  - name: 陪护值守人员
    opens_daily: true
    does: 查看设备是否在线、处理实时环境告警、回看识字与语音交互
  - name: 陪护家属
    opens_daily: false
    does: 在佩戴者开启共享后查看授权位置与实时画面
  - name: 软件联调人员
    opens_daily: false
    does: 在没有帽端时验证 OCR、VLM、ASR 与 TTS 的软件链路
structure:
  primary: monitor
  secondary: console
dials:
  cadence: realtime
  input: 3
  depth: 4
hook:
  text: "{设备状态} · {最近告警} · {今天需要处理的事项}"
  fields:
    - { name: devices.real_online, reads: 设备状态, writes: system, when: 帽端心跳会话更新 }
    - { name: recent_alerts[0], reads: 最近告警, writes: system, when: YOLO 检测完成并写入预警记录 }
    - { name: today_alerts.total, reads: 今天需要处理的事项, writes: derived, when: 按浏览器时区读取当天记录 }
  cold_start: "还没有帽端数据：先连接设备，或进入实时环境监测启动电脑摄像头"
cold_start:
  day_1: "先连接一台帽端设备，或者启动电脑摄像头，首屏会在收到第一帧后告诉你发生了什么"
home:
  - hook
  - 今日状态卡
  - 最近需要关注的告警
  - 近 7 天预警趋势
  - 模块运行状态
channels:
  - name: 总览
    type: today
    weight: primary
    does: 先看设备与模型状态，再处理最新告警和今日异常
    pages:
      - { level: L1, shows: 结论条 + 状态卡 + 告警队列 + 趋势图, actions: [前往实时监测, 查看全部告警] }
  - name: 实时监测
    type: today
    weight: regular
    does: 选择电脑或帽端画面，查看检测框、位置和语音播报
    pages:
      - { level: L1, shows: 16:9 实时画面 + 检测覆盖层 + 预警播报控制台, actions: [启动摄像头, 前往语音与识字, 停止] }
  - name: 远程守护
    type: today
    weight: regular
    does: 让家属快速确认设备在线情况、佩戴者位置、现场环境和重要出行动态
    pages:
      - { level: L1, shows: 四项紧凑状态 + 真实地图 + 佩戴者第一视角 + YOLO 环境摘要 + 重要动态, actions: [刷新位置, 查看轨迹, 查看画面, 暂停共享] }
      - { level: L2, shows: 同一定位来源与出行区间内的历史轨迹和时间, actions: [返回设备守护] }
  - name: AI地图助手
    type: tool
    weight: regular
    does: 让佩戴者通过语音或文字查询真实周边地点并查看路线
    pages:
      - { level: L1, shows: 对话与ASR确认 + 真实POI结果 + 独立地图 + 路线进度, actions: [查询当前位置, 搜索周边设施, 规划步行路线, 取消导航] }
  - name: 开发者诊断
    type: tool
    weight: occasional
    does: 管理本机定位采集与模拟场景，排查数据来源冲突
    pages:
      - { level: L1, shows: 当前定位Provider + 诊断设备选择 + 模拟路线控制, actions: [切换Provider, 启停定位, 查看诊断状态] }
  - name: 告警记录
    type: record
    weight: regular
    does: 按时间回看障碍物、方位、置信度和播报结果
    pages:
      - { level: L1, shows: 按时间倒序的告警表格, filters: [时间, 目标, 方位], actions: [导出 Excel] }
      - { level: L2, shows: 单条告警详情与来源, actions: [返回列表] }
  - name: 语音与识字
    type: console
    weight: regular
    does: 由现有意图识别路由识字、环境查询、画面解读与停止/重读等任务，结合对应画面的 YOLO 结果和 VLM 场景描述组织回复并回看朗读链路；软件联调作为页内模式
    pages:
      - { level: L1, shows: 实机/软件联调切换 + 来源选择 + 当前帧 + 问题输入 + 意图结果 + 最近回答 + ASR/意图/YOLO/VLM/回复/TTS 链路状态 + 最近任务列表, filters: [来源, 意图, 状态, 时间], actions: [提交问题, 识字并朗读, 停止任务, 查看详情, 运行联调] }
      - { level: L2, shows: 单次任务输入帧与序号、识别到的意图及置信度、同帧 YOLO 目标、VLM 场景描述、最终回复依据、模型名、逐段耗时和 TTS 目标/状态, actions: [重新处理, 重新朗读, 返回列表] }
  - name: 设备与参数
    type: knowledge
    weight: occasional
    does: 查看设备心跳并调整识别置信度、预警冷却和语音开关
    pages:
      - { level: L1, shows: 设备状态列表 + 分组设置表单, actions: [保存设置] }
entities:
  - name: Device
    fields: [device_id, online, last_heartbeat, source]
    written_by: { device_id: integration, online: system, last_heartbeat: integration, source: derived }
    relations: [Device 1-n Detection, Device 1-n Alert]
  - name: Detection
    fields: [id, device_id, object_class, direction, confidence, image_seq, detected_at]
    written_by: { id: system, device_id: integration, object_class: system, direction: derived, confidence: system, image_seq: integration, detected_at: system }
    relations: [Detection n-1 Device, Detection 0-1 Alert]
  - name: Alert
    fields: [id, device_id, object_class, direction, confidence, voice_text, detected_at, source]
    written_by: { id: system, device_id: system, object_class: system, direction: derived, confidence: system, voice_text: derived, detected_at: system, source: derived }
    relations: [Alert n-1 Device]
  - name: VoiceInteraction
    fields: [id, device_id, user_text, reply_text, request_type, asr_status, tts_status, occurred_at, created_at]
    written_by: { id: system, device_id: integration, user_text: user_or_asr, reply_text: system, request_type: derived, asr_status: system, tts_status: system, occurred_at: system, created_at: system }
    relations: [VoiceInteraction n-1 Device, VoiceInteraction 0-1 MultimodalTask]
  - name: MultimodalTask
    fields: [id, voice_interaction_id, device_id, source_id, source_mode, frame_seq, intent, intent_confidence, detection_snapshot, ocr_result, vlm_scene_description, ocr_model, vlm_model, final_reply, response_basis, asr_status, intent_status, yolo_status, vlm_status, reply_status, tts_target, tts_status, stage_latency_ms, error_code, created_at]
    written_by: { id: system, voice_interaction_id: derived, device_id: integration, source_id: integration, source_mode: derived, frame_seq: integration, intent: system, intent_confidence: system, detection_snapshot: system, ocr_result: system, vlm_scene_description: system, ocr_model: configuration, vlm_model: configuration, final_reply: system, response_basis: system, asr_status: system, intent_status: system, yolo_status: system, vlm_status: system, reply_status: system, tts_target: derived, tts_status: system, stage_latency_ms: system, error_code: system, created_at: system }
    relations: [MultimodalTask n-1 Device, MultimodalTask 0-1 VoiceInteraction]
  - name: LocationPoint
    fields: [id, device_id, latitude, longitude, accuracy, source, mode, captured_at, sequence]
    written_by: { device_id: integration, latitude: integration, longitude: integration, accuracy: integration, source: derived, mode: derived, captured_at: system, sequence: system }
    relations: [LocationPoint n-1 Device]
  - name: NavigationSession
    fields: [device_id, status, destination, route, remaining_m, progress, updated_at]
    written_by: { device_id: derived, status: system, destination: derived, route: integration, remaining_m: derived, progress: derived, updated_at: system }
    relations: [NavigationSession n-1 Device]
depends_on:
  - { item: existing_ocr_endpoint, status: available, note: /api/ocr/read 当前以 RapidOCR 为主，只有本地 OCR 无清晰文字且视觉模型已配置时才回退图片模型 }
  - { item: qwen3_vl, status: configured_optional, note: OPENAI_BASE_URL 和 OPENAI_MODEL 可连接兼容图片模型；未配置时不得显示 VLM 已就绪 }
  - { item: device_audio_chain, status: partial, note: 软件 ASR 处理结构已存在，完整帽端麦克风与扬声器回执仍待验收 }
mvp: [总览, 实时监测, 远程守护, AI地图助手, 告警记录, 语音与识字, 设备与参数]
later: [软件联调的设备级回放, 告警详情页]
visual: "延续启明现有浅色产品工作台；地图和真实画面作为主视图，青绿用于可用状态、琥珀用于注意状态；留白承托信息层级，避免重复统计卡片"
seam: { type: none, why: 内部陪护控制台，价值在及时发现异常，不在页面内收费 }
excluded:
  - { item: 真实距离与碰撞时间, reason: 当前只有图像空间趋势，不能伪装成测距能力 }
  - { item: 设备固件配置下发, reason: 现阶段由硬件与后端联调负责，先保持前端只读和监测 }
deferred:
  - { item: 多角色权限矩阵, reason: 目前只有陪护值守人员这一种日常打开者 }
---

## 这个台子是给谁的

这是给陪护值守人员用的网页控制台，围着帽端设备、环境事件和可追溯的交互任务转。每天打开先确认设备和检测链路是否在线，再处理最近一条告警。新设“语音与识字”页面承接画面理解、识字、问答和朗读回看；电脑摄像头、上传图片与模拟设备都作为明确标记的软件联调来源。

## 每天怎么用

进入总览后，首屏先读设备状态、最近告警和今天的异常数量。需要判断现场时进入实时监测，选择电脑或帽端画面，确认检测框、方位和播报。需要读图或追溯语音时进入“语音与识字”：先选来源和任务，再看识字/VLM 回答、模型来源、各段耗时与实际播放回执。没有帽端时切到软件联调模式，上传图片或使用电脑摄像头完成软件侧验证。

## 为什么是这几个频道

- 总览回答“现在要不要处理什么”。
- 实时监测承载视觉和语音这条主流程。
- 告警记录把系统自动写入的事件变成可追溯的记录。
- 语音与识字把画面来源、OCR/VLM、问题、回复和 TTS 状态串成一条可回看的任务链；软件联调作为页内模式，与实机数据分开筛选。
- 设备与参数集中放低频配置，避免首屏被表单占满；远程守护只向设备授权的账号开放位置和视频。

## 已经想过但没做的

真实距离、碰撞时间、多角色权限和固件配置下发暂不进入本次前端改造。VLM 仅做语义场景解释，不能替代 YOLO/风险链路或承诺视障安全；未完成设备端音频回执时，不能把浏览器本地朗读显示成帽端已播放。首版不默认留存完整原图和录音。

## “语音与识字”页面方案

页面主流程是“选来源 → 选任务 → 处理画面 → 展示回复 → 核对是否朗读”。顶部显示当前来源、OCR/VLM/ASR/TTS 能力状态和最近任务状态；主区展示帽端/电脑画面或图片上传、任务选择（识字并朗读、解读画面、环境问答）和用户问题输入；回答卡显示简短可朗读回复、实际模型、来源标签和重新朗读入口；链路追踪逐段显示采集、OCR/VLM、回复生成、TTS 和播放状态及耗时；下方为可按来源、任务、时间和状态筛选的最近任务。

用户提交语音或文本后，先经过现有意图识别，再按识别结果选择处理链路。页面按钮也可显式发起“识字”或“画面解读”，但这些操作映射到同一意图路由，不另造相互冲突的任务判定。

现有实时监测页中的“识字并朗读”、重新朗读、停止朗读和识字结果区迁入新页；实时监测保留目标检测、空间状态和障碍物播报。新页可以独立选择帽端或电脑画面并拍摄，不要求用户先打开另一页。实现时把摄像头来源会话与页面 DOM 解耦后复用，避免出现两套摄像头状态、后台重复开流或切换来源时读到旧帧。

回复链路按“ASR（若输入为语音）→ 现有意图识别 → 取同一来源、同一帧的识别结果 → 按意图调用 OCR/VLM → 回复组织 → TTS”执行。复用现有 `OCR_READ`、`OCR_AGAIN`、`CAMERA_CAPTURE`、`STOP`、`ENV_QUERY` 和 `UNKNOWN`；为明确的“描述当前画面”请求在现有分类器和注册表内增加 `SCENE_DESCRIBE`，不新建平行分类器。

当前 `/ws/audio` 已通过 `IntentClassifier` 路由；`/api/simulate/voice` 与 `dialogue()` 仍有单独的关键词判断。新页接入时要把网页文本、软件联调和 ASR 转写统一交给同一个意图执行器，避免相同问题因入口不同得到不同结果。

对于环境查询，系统读取该帧 YOLO 的类别、方位、置信度等结构化结果，并让 VLM 只生成该帧的场景描述与不确定项；回复组织器依据已识别意图，把两类结果合成面向用户的短回复。YOLO 负责可验证的目标与方位，VLM 负责补充场景语义，不能覆盖检测结果、编造目标或推断未测量的距离。VLM 不可用时降级为基于 YOLO 的简短回复，并标明场景描述不可用；不能因此丢弃已有识别结果。无法判断时要明确说不确定。OCR 意图仍优先走 RapidOCR，只有没有清晰文字且图片模型已配置时才回退；`STOP`、`OCR_AGAIN` 等控制意图不应无条件触发 VLM。

实机模式只接受帽端设备来源，显示设备编号、帧序号和最后心跳；只有收到设备播放回执才显示“帽端已播放”。软件联调模式使用电脑摄像头、上传图片或模拟文本请求，并以琥珀色标记；浏览器 SpeechSynthesis 标成“本机浏览器朗读”，模拟接口标成“模拟 TTS”。模型未配置或请求失败时显示具体能力缺口，不以示例结果填充成功状态。

统一由 `POST /api/voice-vision/tasks` 接收来源、文本/转写结果，以及当前帧引用或上传图片，复用现有 `IntentClassifier` 完成意图分流，再执行对应处理器；`ENV_QUERY` 与 `SCENE_DESCRIBE` 调用仅返回场景描述的 VLM 服务，并将同帧 YOLO 结果交给回复组织器。任务响应包含 `intent`、`intent_confidence`、`detection_snapshot`、`vlm_scene_description`、`final_reply` 和逐阶段状态。`GET /api/voice-vision/capabilities` 返回能力状态；可筛选的 `GET /api/voice-vision/tasks` 返回历史；`PATCH /api/voice-vision/tasks/{id}/tts` 回写本机朗读状态。现有 `/ws/audio` ASR 完成后与网页文本/联调入口共用同一处理器，`/api/simulate/voice` 保留为兼容入口。

新增或扩展任务记录至少保存来源模式、设备、帧序号、意图与置信度、同帧 YOLO 结果摘要、VLM 场景描述、最终回复依据、各阶段状态/耗时、TTS 目标和错误码。可以新建 `multimodal_tasks` 并关联 `voice_logs`，避免把图片任务硬塞进仅用于语音问答的旧表。这样能区分“意图识别失败”“检测/VLM 失败”“回复组织失败”和“回答已生成但播报失败”。接口沿用登录鉴权、超时、取消和真实错误回传。

## 给实现方（不熟悉本规范的 AI 或开发，照这段做即可）

- 这是桌面浏览器里的系统工作台。首屏第一行是 hook，要先说设备状态、最近告警和今日需要处理的事项；“语音与识字”是独立页面，不再把软件联调放在单独导航项。
- hook 的数据必须来自现有 `/api/overview`、WebSocket 检测事件和设备会话；没有数据时显示 `cold_start.day_1` 的行动句，不要塞假数字。
- `monitor` 的首屏形态是数字状态 + 异常队列 + 趋势和可下钻记录，不要把它做成只有统计卡片的报表墙。
- `远程守护` 和独立一级页面 `AI地图助手` 复用相同 `device_id`、位置Provider和最新LocationPoint。
- 一个设备同一时刻只允许一个位置Provider；切换前结束上一个Provider，历史轨迹仅显示同一Provider和采集区间的数据。
- 家属页面不展示设备编号、模拟操作或集成说明；无真实位置、画面或地图服务时如实显示不可用状态，不使用示意街道、静态坐标或虚构POI填充。
- 真实地图Key缺失时不渲染地图底图、不返回假地址/地点/路线。课堂演示数据只可通过开发者诊断入口的明确开关提供。
- MJPEG 使用已有 LatestFrameHub，视频只读、短时缓存、不录像。每个位置、视频和 WebSocket 请求都做设备绑定授权；暂停共享后立即拒绝家属读取。
- 定位超过 15 秒自动视为过期，暂停路线进度；普通步行路线不宣称为视障安全路线。
- 实时位置点不逐条变成动态；按移动距离或状态变化生成摘要，预警、偏航、到达和共享变化始终单独记录。
- 视频状态只在存在新鲜帧时显示已连接；MJPEG自动绑定当前设备ID，浏览器用同源地址连接，不能硬编码127.0.0.1。
- AI 地图助手使用现有中文ASR和语音输出能力；识别后先显示可编辑文本，用户确认提交后再调用地图工具。
- `实时监测` 页面继续承载检测、空间分析和障碍物播报；把 `ocrCapture`、`ocrReplay`、`ocrStop`、`ocrStatus`、`ocrResults`、`ocrText` 及 OCR Reader 控件迁到“语音与识字”。确认不再有脚本引用旧节点后再从监测页移除。
- 新页要能独立采集实时监测当前选择的帽端/电脑画面。将来源会话与页面 DOM 解耦并复用，不复制第二套摄像头状态机；切换页时避免重复开流、断流和旧帧串入新任务。
- `语音与识字` 复用 `backend/intent/intent_classifier.py` 与意图注册表；网页文本、软件联调和 `/ws/audio` 的 ASR 结果最终走同一个任务分发器。现有 `ENV_QUERY` 继续处理环境问题，新增 `SCENE_DESCRIBE` 时也扩展当前分类器/注册表，不要另写页面专属关键词分类器。
- `ENV_QUERY` / `SCENE_DESCRIBE` 必须使用与来源和帧序号绑定的 YOLO 结果，并调用只输出场景描述的 VLM 服务；回复组织器把意图、检测结果和 VLM 描述合成答案。VLM 不能覆盖 YOLO 目标/方位，不能提供真实距离或安全结论。
- `OCR_READ` 走现有 OCR 逻辑；`OCR_AGAIN`、`STOP` 等控制意图不要重新调用视觉模型。正常 RapidOCR 命中时不得虚报 VLM 参与。浏览器 TTS 与设备端 TTS 状态分别呈现。
- `POST /api/voice-vision/tasks` 统一意图分流和任务追踪，`GET /api/voice-vision/capabilities` 提供能力状态，`GET /api/voice-vision/tasks` 查询历史，`PATCH /api/voice-vision/tasks/{id}/tts` 回写本机朗读状态；现有 `/api/voices` 和 `/api/simulate/voice` 可保留为兼容入口。
- 只做 mvp 中的频道；软件联调并入“语音与识字”页面模式。颜色只把冰川青用于在线/可用，把琥珀色用于模拟或需要注意的状态。
