---
surface: desktop
domain: system
title: 启明智能陪护控制台
subject: 帽端设备与环境事件
owner: 陪护值守人员
roles:
  - name: 陪护值守人员
    opens_daily: true
    does: 查看设备是否在线、处理实时环境告警、回看识字与语音交互
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
      - { level: L1, shows: 16:9 实时画面 + 检测覆盖层 + 控制台, actions: [启动摄像头, 识字并朗读, 停止] }
  - name: 告警记录
    type: record
    weight: regular
    does: 按时间回看障碍物、方位、置信度和播报结果
    pages:
      - { level: L1, shows: 按时间倒序的告警表格, filters: [时间, 目标, 方位], actions: [导出 Excel] }
      - { level: L2, shows: 单条告警详情与来源, actions: [返回列表] }
  - name: 语音交互
    type: record
    weight: regular
    does: 回看用户问题、系统回复与软件联调状态
    pages:
      - { level: L1, shows: 交互时间轴列表, filters: [时间, 类型, 状态], actions: [查看全部] }
  - name: 设备与参数
    type: knowledge
    weight: occasional
    does: 查看设备心跳并调整识别置信度、预警冷却和语音开关
    pages:
      - { level: L1, shows: 设备状态列表 + 分组设置表单, actions: [保存设置] }
  - name: 软件联调
    type: tool
    weight: occasional
    does: 在没有硬件时用模拟设备验证语音查询链路
    pages:
      - { level: L1, shows: 单行问题输入 + 原始响应面板, actions: [测试语音查询] }
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
    fields: [id, user_text, reply_text, request_type, tts_status, occurred_at, source]
    written_by: { id: system, user_text: user, reply_text: system, request_type: derived, tts_status: system, occurred_at: system, source: derived }
    relations: []
depends_on: []
mvp: [总览, 实时监测, 告警记录, 语音交互, 设备与参数]
later: [软件联调的设备级回放, 告警详情页]
visual: "深夜蓝黑底，冰川青作为安全信号，琥珀色只标记模拟来源或需要注意的状态；高信息密度但保持大片留白，像值守台而不是报表墙"
seam: { type: none, why: 内部陪护控制台，价值在及时发现异常，不在页面内收费 }
excluded:
  - { item: 真实距离与碰撞时间, reason: 当前只有图像空间趋势，不能伪装成测距能力 }
  - { item: 设备固件配置下发, reason: 现阶段由硬件与后端联调负责，先保持前端只读和监测 }
deferred:
  - { item: 多角色权限矩阵, reason: 目前只有陪护值守人员这一种日常打开者 }
---

## 这个台子是给谁的

这是给陪护值守人员用的网页控制台，围着帽端设备和它产生的环境事件转。每天打开先确认设备和检测链路是否在线，再处理最近一条告警；电脑摄像头可以作为没有硬件时的可见演示来源。

## 每天怎么用

进入总览后，首屏先读设备状态、最近告警和今天的异常数量。需要判断现场时进入实时监测，选择电脑或帽端画面，确认检测框、方位和播报。需要追溯时进入告警记录或语音交互，设置只在设备和模型行为发生变化时使用。

## 为什么是这几个频道

- 总览回答“现在要不要处理什么”。
- 实时监测承载视觉和语音这条主流程。
- 告警记录把系统自动写入的事件变成可追溯的记录。
- 语音交互保留识字、问答和软件联调的上下文。
- 设备与参数集中放低频配置，避免首屏被表单占满。
- 软件联调是工具页，不抢占日常导航主位。

## 已经想过但没做的

真实距离、碰撞时间、多角色权限和固件配置下发暂不进入本次前端改造。它们需要新的数据源或权限模型，不能用视觉装饰先假装已经存在。

## 给实现方（不熟悉本规范的 AI 或开发，照这段做即可）

- 这是桌面浏览器里的系统工作台。首屏第一行是 hook，要先说设备状态、最近告警和今日需要处理的事项。
- hook 的数据必须来自现有 `/api/overview`、WebSocket 检测事件和设备会话；没有数据时显示 `cold_start.day_1` 的行动句，不要塞假数字。
- `monitor` 的首屏形态是数字状态 + 异常队列 + 趋势和可下钻记录，不要把它做成只有统计卡片的报表墙。
- `实时监测` 页面保留现有 `cameraVideo`、`cameraRemote`、`cameraOverlay`、`cameraSelect`、`ocr*` 和状态节点，摄像头、检测、播报和识字脚本依赖这些 DOM id。
- `告警记录` 和 `语音交互` 可以使用表格，但总览的主视觉优先使用行动队列、趋势图和状态列表。
- 只做 mvp 中的频道；设置与软件联调放在低频区域。颜色只把冰川青用于在线/可用，把琥珀色用于模拟或需要注意的状态。
