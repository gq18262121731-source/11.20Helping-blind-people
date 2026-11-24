#pragma once

#include <WebSocketsClient.h>
#include <driver/gpio.h>
#include <driver/i2s_std.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <cstring>
#include "config.h"

#ifndef TTS_I2S_BCLK_PIN
#define TTS_I2S_BCLK_PIN -1
#endif
#ifndef TTS_I2S_LRCLK_PIN
#define TTS_I2S_LRCLK_PIN -1
#endif
#ifndef TTS_I2S_DIN_PIN
#define TTS_I2S_DIN_PIN -1
#endif
#ifndef TTS_I2S_PORT
#define TTS_I2S_PORT I2S_NUM_1
#endif

constexpr uint8_t TTS_FRAME_VERSION = 1;
constexpr uint8_t TTS_FRAME_START = 1;
constexpr uint8_t TTS_FRAME_CHUNK = 2;
constexpr uint8_t TTS_FRAME_END = 3;
constexpr uint8_t TTS_FRAME_CANCEL = 4;
constexpr uint8_t TTS_FRAME_READY = 0x81;
constexpr uint8_t TTS_FRAME_PLAYBACK_STARTED = 0x82;
constexpr uint8_t TTS_FRAME_PLAYBACK_DONE = 0x83;
constexpr uint8_t TTS_FRAME_PLAYBACK_ERROR = 0x84;
constexpr uint8_t TTS_FRAME_PLAYBACK_CANCELLED = 0x85;
constexpr size_t TTS_AUDIO_BLOCK_BYTES = 1536;
constexpr size_t TTS_AUDIO_QUEUE_DEPTH = 8;
constexpr size_t TTS_MAX_AUDIO_BYTES = 8U * 1024U * 1024U;
constexpr uint32_t TTS_SAMPLE_RATE = 16000U;
constexpr uint32_t TTS_STREAMING_TOTAL_BYTES = 0xFFFFFFFFU;

inline constexpr bool ttsOutputPinAllowed(int pin) {
  return GPIO_IS_VALID_OUTPUT_GPIO(pin) && pin != CAM_PWDN && pin != CAM_RESET &&
      pin != CAM_XCLK && pin != CAM_SDA && pin != CAM_SCL && pin != CAM_D7 &&
      pin != CAM_D6 && pin != CAM_D5 && pin != CAM_D4 && pin != CAM_D3 &&
      pin != CAM_D2 && pin != CAM_D1 && pin != CAM_D0 && pin != CAM_VSYNC &&
      pin != CAM_HREF && pin != CAM_PCLK && pin != 1 && pin != MOTOR_LEFT_PIN &&
      pin != MOTOR_RIGHT_PIN && pin != 0 && pin != 3 && pin != 45 && pin != 46 &&
      pin != 19 && pin != 20 && !(pin >= 26 && pin <= 37);
}

#if TTS_I2S_BCLK_PIN >= 0
static_assert(ttsOutputPinAllowed(TTS_I2S_BCLK_PIN), "TTS BCLK pin conflicts with reserved hardware");
#endif
#if TTS_I2S_LRCLK_PIN >= 0
static_assert(ttsOutputPinAllowed(TTS_I2S_LRCLK_PIN), "TTS LRCLK pin conflicts with reserved hardware");
#endif
#if TTS_I2S_DIN_PIN >= 0
static_assert(ttsOutputPinAllowed(TTS_I2S_DIN_PIN), "TTS DIN pin conflicts with reserved hardware");
#endif

WebSocketsClient ttsWebSocket;
static i2s_chan_handle_t ttsTxChannel = nullptr;
static QueueHandle_t ttsAudioQueue = nullptr;
static QueueHandle_t ttsStatusQueue = nullptr;
static TaskHandle_t ttsPlayerTaskHandle = nullptr;
static bool ttsI2sReady = false;
static bool ttsSocketConnected = false;
static bool ttsSessionActive = false;
static bool ttsReceivingAudio = false;
static uint32_t ttsActiveRequestId = 0;
static uint32_t ttsExpectedBytes = 0;
static uint32_t ttsReceivedBytes = 0;
static uint32_t ttsNextSequence = 0;

struct TtsAudioBlock {
  uint16_t length;
  uint8_t flags;  // bit 0 = end, bit 1 = abort
  uint8_t data[TTS_AUDIO_BLOCK_BYTES];
};

struct TtsStatusFrame {
  uint8_t type;
  uint32_t requestId;
  uint32_t value;
};

inline uint32_t ttsReadU32(const uint8_t* bytes) {
  return static_cast<uint32_t>(bytes[0]) |
      (static_cast<uint32_t>(bytes[1]) << 8) |
      (static_cast<uint32_t>(bytes[2]) << 16) |
      (static_cast<uint32_t>(bytes[3]) << 24);
}

inline void ttsWriteU32(uint8_t* bytes, uint32_t value) {
  bytes[0] = static_cast<uint8_t>(value);
  bytes[1] = static_cast<uint8_t>(value >> 8);
  bytes[2] = static_cast<uint8_t>(value >> 16);
  bytes[3] = static_cast<uint8_t>(value >> 24);
}

inline void ttsSendStatus(uint8_t type, uint32_t requestId, uint32_t value = 0) {
  if (!ttsSocketConnected) return;
  uint8_t frame[12] = {'T', 'S', TTS_FRAME_VERSION, type};
  ttsWriteU32(frame + 4, requestId);
  ttsWriteU32(frame + 8, value);
  ttsWebSocket.sendBIN(frame, sizeof(frame));
}

inline void ttsQueueStatus(uint8_t type, uint32_t requestId, uint32_t value = 0) {
  if (!ttsStatusQueue) return;
  TtsStatusFrame frame{type, requestId, value};
  xQueueSend(ttsStatusQueue, &frame, 0);
}

inline void ttsClearAudioQueue() {
  if (ttsAudioQueue) xQueueReset(ttsAudioQueue);
}

inline void ttsFailSession(uint32_t requestId, uint32_t errorCode) {
  ttsReceivingAudio = false;
  ttsSessionActive = false;
  ttsClearAudioQueue();
  ttsSendStatus(TTS_FRAME_PLAYBACK_ERROR, requestId, errorCode);
}

inline void ttsPlayerTask(void*) {
  TtsAudioBlock block{};
  while (true) {
    if (xQueueReceive(ttsAudioQueue, &block, portMAX_DELAY) != pdTRUE) continue;
    if (block.flags & 0x02) {
      int16_t silence[512] = {};
      size_t written = 0;
      i2s_channel_write(ttsTxChannel, silence, sizeof(silence), &written, pdMS_TO_TICKS(100));
      if (block.flags & 0x04) {
        ttsQueueStatus(TTS_FRAME_PLAYBACK_CANCELLED, ttsActiveRequestId, 0);
      }
      continue;
    }
    if (block.flags & 0x01) {
      // Let the final DMA samples reach the amplifier before acknowledging completion.
      vTaskDelay(pdMS_TO_TICKS(50));
      ttsQueueStatus(TTS_FRAME_PLAYBACK_DONE, ttsActiveRequestId, 0);
      continue;
    }
    size_t written = 0;
    esp_err_t result = i2s_channel_write(
        ttsTxChannel, block.data, block.length, &written, pdMS_TO_TICKS(1000));
    if (result != ESP_OK || written != block.length) {
      ttsQueueStatus(TTS_FRAME_PLAYBACK_ERROR, ttsActiveRequestId,
                     result == ESP_OK ? 5U : static_cast<uint32_t>(result));
      ttsSessionActive = false;
      ttsReceivingAudio = false;
    }
  }
}

inline bool initTtsAudioPlayer() {
  ttsAudioQueue = xQueueCreate(TTS_AUDIO_QUEUE_DEPTH, sizeof(TtsAudioBlock));
  ttsStatusQueue = xQueueCreate(8, sizeof(TtsStatusFrame));
  if (!ttsAudioQueue || !ttsStatusQueue) {
    Serial.println("TTS audio queues unavailable");
    return false;
  }
#if TTS_I2S_BCLK_PIN >= 0 && TTS_I2S_LRCLK_PIN >= 0 && TTS_I2S_DIN_PIN >= 0
  if (TTS_I2S_BCLK_PIN == TTS_I2S_LRCLK_PIN ||
      TTS_I2S_BCLK_PIN == TTS_I2S_DIN_PIN ||
      TTS_I2S_LRCLK_PIN == TTS_I2S_DIN_PIN) {
    Serial.println("TTS I2S pins must be distinct");
    return false;
  }
  i2s_chan_config_t channelConfig = I2S_CHANNEL_DEFAULT_CONFIG(TTS_I2S_PORT, I2S_ROLE_MASTER);
  channelConfig.dma_desc_num = 6;
  channelConfig.dma_frame_num = 256;
  esp_err_t result = i2s_new_channel(&channelConfig, &ttsTxChannel, nullptr);
  if (result != ESP_OK) {
    Serial.printf("TTS I2S channel creation failed: %s\n", esp_err_to_name(result));
    return false;
  }
  i2s_std_config_t standardConfig = {};
  standardConfig.clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(TTS_SAMPLE_RATE);
  standardConfig.slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
      I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
  standardConfig.gpio_cfg.mclk = I2S_GPIO_UNUSED;
  standardConfig.gpio_cfg.bclk = static_cast<gpio_num_t>(TTS_I2S_BCLK_PIN);
  standardConfig.gpio_cfg.ws = static_cast<gpio_num_t>(TTS_I2S_LRCLK_PIN);
  standardConfig.gpio_cfg.dout = static_cast<gpio_num_t>(TTS_I2S_DIN_PIN);
  standardConfig.gpio_cfg.din = I2S_GPIO_UNUSED;
  standardConfig.gpio_cfg.invert_flags.mclk_inv = false;
  standardConfig.gpio_cfg.invert_flags.bclk_inv = false;
  standardConfig.gpio_cfg.invert_flags.ws_inv = false;
  result = i2s_channel_init_std_mode(ttsTxChannel, &standardConfig);
  if (result == ESP_OK) result = i2s_channel_enable(ttsTxChannel);
  if (result != ESP_OK) {
    Serial.printf("TTS I2S setup failed: %s\n", esp_err_to_name(result));
    i2s_del_channel(ttsTxChannel);
    ttsTxChannel = nullptr;
    return false;
  }
  ttsI2sReady = xTaskCreatePinnedToCore(
      ttsPlayerTask, "tts_i2s_player", 4096, nullptr, 3, &ttsPlayerTaskHandle, 1) == pdPASS;
#else
  Serial.println("TTS I2S not configured; set BCLK/LRCLK/DIN GPIO in config.local.h");
#endif
  return ttsI2sReady;
}

inline void ttsHandleStart(const uint8_t* frame, size_t length) {
  if (length != 16) return;
  const uint32_t requestId = ttsReadU32(frame + 4);
  const uint32_t sampleRate = ttsReadU32(frame + 8);
  const uint32_t totalBytes = ttsReadU32(frame + 12);
  if (!ttsI2sReady) {
    ttsSendStatus(TTS_FRAME_PLAYBACK_ERROR, requestId, 1);  // I2S pins/output are not configured.
    return;
  }
  if (ttsSessionActive || sampleRate != TTS_SAMPLE_RATE || totalBytes == 0 ||
      (totalBytes != TTS_STREAMING_TOTAL_BYTES &&
       ((totalBytes & 1U) || totalBytes > TTS_MAX_AUDIO_BYTES))) {
    ttsSendStatus(TTS_FRAME_PLAYBACK_ERROR, requestId, 2);  // Busy or invalid format/length.
    return;
  }
  ttsClearAudioQueue();
  ttsActiveRequestId = requestId;
  ttsExpectedBytes = totalBytes;
  ttsReceivedBytes = 0;
  ttsNextSequence = 0;
  ttsSessionActive = true;
  ttsReceivingAudio = true;
  ttsSendStatus(TTS_FRAME_PLAYBACK_STARTED, requestId, 0);
}

inline void ttsHandleChunk(const uint8_t* frame, size_t length) {
  if (length <= 12 || !ttsReceivingAudio) return;
  const uint32_t requestId = ttsReadU32(frame + 4);
  const uint32_t sequence = ttsReadU32(frame + 8);
  const size_t pcmBytes = length - 12;
  if (requestId != ttsActiveRequestId || sequence != ttsNextSequence ||
      pcmBytes > TTS_AUDIO_BLOCK_BYTES || (pcmBytes & 1U) ||
      pcmBytes > TTS_MAX_AUDIO_BYTES - ttsReceivedBytes ||
      (ttsExpectedBytes != TTS_STREAMING_TOTAL_BYTES &&
       pcmBytes > ttsExpectedBytes - ttsReceivedBytes)) {
    ttsFailSession(requestId, 3);  // Wrong request, sequence, size, or sample alignment.
    return;
  }
  TtsAudioBlock block{};
  block.length = static_cast<uint16_t>(pcmBytes);
  memcpy(block.data, frame + 12, pcmBytes);
  if (xQueueSend(ttsAudioQueue, &block, 0) != pdTRUE) {
    ttsFailSession(requestId, 4);  // Bounded playback queue is full.
    return;
  }
  ttsReceivedBytes += static_cast<uint32_t>(pcmBytes);
  ++ttsNextSequence;
}

inline void ttsHandleControl(const uint8_t* frame, size_t length, uint8_t type) {
  if (length != 12) return;
  const uint32_t requestId = ttsReadU32(frame + 4);
  const uint32_t value = ttsReadU32(frame + 8);
  if (type == TTS_FRAME_CANCEL) {
    if (ttsSessionActive && requestId == ttsActiveRequestId) {
      ttsReceivingAudio = false;
      ttsSessionActive = false;
      ttsClearAudioQueue();
      TtsAudioBlock abortBlock{};
      abortBlock.flags = 0x06;
      xQueueSend(ttsAudioQueue, &abortBlock, 0);
    }
    return;
  }
  if (type != TTS_FRAME_END || !ttsReceivingAudio || requestId != ttsActiveRequestId ||
      value != ttsNextSequence || ttsReceivedBytes == 0 ||
      (ttsExpectedBytes != TTS_STREAMING_TOTAL_BYTES && ttsReceivedBytes != ttsExpectedBytes)) {
    ttsFailSession(requestId, 3);
    return;
  }
  ttsReceivingAudio = false;
  TtsAudioBlock endBlock{};
  endBlock.flags = 0x01;
  if (xQueueSend(ttsAudioQueue, &endBlock, pdMS_TO_TICKS(100)) != pdTRUE) {
    ttsFailSession(requestId, 4);
  }
}

inline void ttsWebSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      ttsSocketConnected = true;
      ttsSendStatus(TTS_FRAME_READY, 0, ttsI2sReady ? 0U : 1U);
      Serial.println("TTS audio WebSocket connected");
      break;
    case WStype_DISCONNECTED:
      ttsSocketConnected = false;
      ttsReceivingAudio = false;
      ttsSessionActive = false;
      ttsClearAudioQueue();
      if (ttsI2sReady) {
        TtsAudioBlock abortBlock{};
        abortBlock.flags = 0x02;
        xQueueSend(ttsAudioQueue, &abortBlock, 0);
      }
      Serial.println("TTS audio WebSocket disconnected");
      break;
    case WStype_BIN:
      if (length < 4 || payload[0] != 'T' || payload[1] != 'S' ||
          payload[2] != TTS_FRAME_VERSION) return;
      switch (payload[3]) {
        case TTS_FRAME_START: ttsHandleStart(payload, length); break;
        case TTS_FRAME_CHUNK: ttsHandleChunk(payload, length); break;
        case TTS_FRAME_END:
        case TTS_FRAME_CANCEL: ttsHandleControl(payload, length, payload[3]); break;
        default: break;
      }
      break;
    case WStype_ERROR:
      Serial.println("TTS audio WebSocket transport error");
      break;
    default:
      break;
  }
}

inline void ttsNetworkTask(void*) {
  TtsStatusFrame frame{};
  while (true) {
    while (ttsStatusQueue && xQueueReceive(ttsStatusQueue, &frame, 0) == pdTRUE) {
      ttsSendStatus(frame.type, frame.requestId, frame.value);
      if (frame.type == TTS_FRAME_PLAYBACK_DONE || frame.type == TTS_FRAME_PLAYBACK_ERROR ||
          frame.type == TTS_FRAME_PLAYBACK_CANCELLED) {
        ttsSessionActive = false;
      }
    }
    ttsWebSocket.loop();
    vTaskDelay(pdMS_TO_TICKS(1));
  }
}
