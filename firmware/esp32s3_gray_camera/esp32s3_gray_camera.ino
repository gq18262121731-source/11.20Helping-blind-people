#include <WiFi.h>
#include <WebSocketsClient.h>
#include <cstring>
#include <cstdlib>
#include "esp_camera.h"
#include "img_converters.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "driver/gpio.h"
#include "config.h"
#include "tts_player.h"

WebSocketsClient webSocket;
bool cameraReady = false;
bool socketConnected = false;
bool awaitingAck = false;
bool motorActive = false;
uint32_t lastSendMs = 0;
uint32_t sentAtMs = 0;
uint32_t ackWaitStartedMs = 0;
uint32_t motorUntilMs = 0;
uint32_t framesSent = 0;
uint32_t framesAcknowledged = 0;
uint32_t captureFailures = 0;
uint32_t encodeFailures = 0;
bool wifiWasConnected = false;
bool wifiScanActive = false;
uint32_t nextWifiScanMs = 30000;
uint32_t lastMicStatsMs = 0;
constexpr int microphoneAdcPin = 1;
constexpr pixformat_t captureFormat = CAMERA_COLOR_MODE ? PIXFORMAT_RGB565 : PIXFORMAT_GRAYSCALE;
constexpr size_t captureFrameBytes = 320U * 240U * (CAMERA_COLOR_MODE ? 2U : 1U);
static_assert(!CAMERA_COLOR_MODE || CAMERA_JPEG_TRANSPORT,
    "RGB565 color requires JPEG transport; set CAMERA_COLOR_MODE=0 for legacy raw gray");

#if CAMERA_JPEG_TRANSPORT
// WebSocketsClient requires 14 reserved bytes for headerToPayload=true.
// Encode directly after that prefix, so header and JPEG use one TCP write.
constexpr size_t wsHeaderReserve = 14;
constexpr size_t jpegCapacity = 128U * 1024U;
uint8_t* jpegPacket = nullptr;
struct JpegOutput {
  size_t length = 0;
  bool overflow = false;
};

size_t writeJpeg(void* arg, size_t index, const void* data, size_t length) {
  auto* output = static_cast<JpegOutput*>(arg);
  if (!data || length == 0) return 0;
  if (output->overflow || index != output->length || index > jpegCapacity ||
      length > jpegCapacity - index) {
    output->overflow = true;
    return 0;
  }
  memcpy(jpegPacket + wsHeaderReserve + index, data, length);
  output->length += length;
  return length;
}
#endif

constexpr bool cameraPin(int pin) {
  return pin == CAM_PWDN || pin == CAM_RESET || pin == CAM_XCLK ||
      pin == CAM_SDA || pin == CAM_SCL || pin == CAM_D7 || pin == CAM_D6 ||
      pin == CAM_D5 || pin == CAM_D4 || pin == CAM_D3 || pin == CAM_D2 ||
      pin == CAM_D1 || pin == CAM_D0 || pin == CAM_VSYNC ||
      pin == CAM_HREF || pin == CAM_PCLK;
}

// Conservative guards, not a substitute for the exact board schematic:
// exclude camera, USB, console, strapping and possible flash/PSRAM pins.
constexpr bool motorPinAllowed(int pin) {
  return pin >= 0 && pin <= 48 && GPIO_IS_VALID_OUTPUT_GPIO(pin) &&
      !cameraPin(pin) && pin != 0 && pin != 3 && pin != 45 && pin != 46 &&
      pin != 19 && pin != 20 && pin != 43 && pin != 44 &&
      !(pin >= 26 && pin <= 37);
}

#if ENABLE_MOTORS
static_assert(motorPinAllowed(MOTOR_LEFT_PIN), "Unsafe/unset LEFT motor pin; check camera and board wiring");
static_assert(motorPinAllowed(MOTOR_RIGHT_PIN), "Unsafe/unset RIGHT motor pin; check camera and board wiring");
static_assert(MOTOR_LEFT_PIN != MOTOR_RIGHT_PIN, "Motor pins must be different");
#endif

void stopMotors() {
#if ENABLE_MOTORS
  digitalWrite(MOTOR_LEFT_PIN, LOW);
  digitalWrite(MOTOR_RIGHT_PIN, LOW);
#endif
  motorActive = false;
}

void motorCommand(char command) {
  if (command == 'S') {
    stopMotors();
    return;
  }
  if (command != 'L' && command != 'R' && command != 'B') return;
#if ENABLE_MOTORS
  digitalWrite(MOTOR_LEFT_PIN, command == 'L' || command == 'B' ? HIGH : LOW);
  digitalWrite(MOTOR_RIGHT_PIN, command == 'R' || command == 'B' ? HIGH : LOW);
  motorUntilMs = millis() + MOTOR_PULSE_MS;
  motorActive = true;
  Serial.printf("Motor command: %c\n", command);
#else
  Serial.printf("Recognition direction: %c (camera-only mode; motor outputs disabled)\n", command);
#endif
}

void updateMotors() {
  if (motorActive && static_cast<int32_t>(millis() - motorUntilMs) >= 0) stopMotors();
}

void webSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      socketConnected = true;
      awaitingAck = false;
      lastSendMs = millis();
      Serial.println("WebSocket connected");
      break;
    case WStype_DISCONNECTED:
      socketConnected = false;
      awaitingAck = false;
      stopMotors();
      Serial.printf("WebSocket disconnected; WiFi status=%d, RSSI=%d dBm; retry in 3 s\n",
          static_cast<int>(WiFi.status()), WiFi.RSSI());
      break;
    case WStype_BIN:
      if (length == 1) motorCommand(static_cast<char>(payload[0]));
      break;
    case WStype_TEXT:
      if (length == 3 && memcmp(payload, "ACK", 3) == 0) {
        awaitingAck = false;
        ++framesAcknowledged;
        lastSendMs = millis();
        Serial.printf("Frame ACK, total=%lu ms, wait=%lu ms\n",
            static_cast<unsigned long>(millis() - sentAtMs),
            static_cast<unsigned long>(millis() - ackWaitStartedMs));
      } else {
        Serial.print("Backend: ");
        Serial.write(payload, length);
        Serial.println();
      }
      break;
    case WStype_ERROR:
      Serial.println("WebSocket transport error");
      break;
    default:
      break;
  }
}

bool initCamera() {
  Serial.printf("PSRAM total=%u, free=%u bytes\n",
      static_cast<unsigned>(ESP.getPsramSize()), static_cast<unsigned>(ESP.getFreePsram()));
  if (!psramFound()) {
    Serial.println("PSRAM unavailable: check board/PSRAM settings; capture disabled");
    return false;
  }
#if CAMERA_JPEG_TRANSPORT
  jpegPacket = static_cast<uint8_t*>(heap_caps_malloc(
      jpegCapacity + wsHeaderReserve, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (!jpegPacket) {
    Serial.println("JPEG packet allocation failed; capture disabled");
    return false;
  }
#endif
  camera_config_t c = {};
  c.pin_pwdn = CAM_PWDN;
  c.pin_reset = CAM_RESET;
  c.pin_xclk = CAM_XCLK;
  c.pin_sccb_sda = CAM_SDA;
  c.pin_sccb_scl = CAM_SCL;
  c.pin_d7 = CAM_D7;
  c.pin_d6 = CAM_D6;
  c.pin_d5 = CAM_D5;
  c.pin_d4 = CAM_D4;
  c.pin_d3 = CAM_D3;
  c.pin_d2 = CAM_D2;
  c.pin_d1 = CAM_D1;
  c.pin_d0 = CAM_D0;
  c.pin_vsync = CAM_VSYNC;
  c.pin_href = CAM_HREF;
  c.pin_pclk = CAM_PCLK;
  c.xclk_freq_hz = 8000000;
  c.ledc_timer = LEDC_TIMER_0;
  c.ledc_channel = LEDC_CHANNEL_0;
  c.pixel_format = captureFormat;
  c.frame_size = FRAMESIZE_QVGA;
  c.jpeg_quality = 12;  // Raw RGB565/gray uses CAMERA_JPEG_QUALITY in software.
  c.fb_count = 1;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  esp_err_t err = esp_camera_init(&c);
  Serial.printf("Camera init: %s\n", esp_err_to_name(err));
  Serial.println("Transport: reusable JPEG buffer; combined WebSocket header/payload write");
  if (err == ESP_OK) {
    sensor_t* sensor = esp_camera_sensor_get();
    if (sensor) {
      camera_sensor_info_t* info = esp_camera_sensor_get_info(&sensor->id);
      Serial.printf("Camera sensor=%s, PID=0x%04x; %s QVGA; XCLK=8 MHz\n",
          info ? info->name : "unknown", sensor->id.PID,
          CAMERA_COLOR_MODE ? "RGB565 color" : "grayscale");
    }
  }
  return err == ESP_OK;
}

void updateWifiLocation() {
  if (!socketConnected || WiFi.status() != WL_CONNECTED) return;
  uint32_t now = millis();
  if (!wifiScanActive && !awaitingAck && !motorActive &&
      static_cast<int32_t>(now - nextWifiScanMs) >= 0) {
    int result = WiFi.scanNetworks(true, true);
    if (result == WIFI_SCAN_RUNNING) {
      wifiScanActive = true;
      Serial.println("Wi-Fi location scan started");
    } else if (result >= 0) {
      wifiScanActive = true;
    } else {
      nextWifiScanMs = now + 120000U;
    }
  }
  if (!wifiScanActive) return;
  int count = WiFi.scanComplete();
  if (count == WIFI_SCAN_RUNNING) return;
  wifiScanActive = false;
  nextWifiScanMs = now + 120000U;
  if (count < 0) {
    Serial.printf("Wi-Fi location scan failed: %d\n", count);
    WiFi.scanDelete();
    return;
  }
  String message;
  message.reserve(1800);
  message = "{\"type\":\"wifi_scan\",\"access_points\":[";
  int included = 0;
  for (int i = 0; i < count && included < 20; ++i) {
    uint8_t* bssid = WiFi.BSSID(i);
    if (!bssid) continue;
    char mac[18];
    snprintf(mac, sizeof(mac), "%02X:%02X:%02X:%02X:%02X:%02X",
        bssid[0], bssid[1], bssid[2], bssid[3], bssid[4], bssid[5]);
    if (included++) message += ',';
    message += "{\"macAddress\":\"";
    message += mac;
    message += "\",\"signalStrength\":";
    message += String(WiFi.RSSI(i));
    message += '}';
  }
  message += "]}";
  bool sent = webSocket.sendTXT(message);
  Serial.printf("Wi-Fi location scan: found=%d sent=%s\n", included, sent ? "yes" : "no");
  WiFi.scanDelete();
}

void reportMicrophoneLevel() {
  uint32_t now = millis();
  if (now - lastMicStatsMs < 5000U) return;
  lastMicStatsMs = now;

  constexpr uint16_t sampleCount = 160;
  uint16_t minimum = 4095;
  uint16_t maximum = 0;
  uint64_t sum = 0;
  uint64_t sumSquares = 0;
  for (uint16_t i = 0; i < sampleCount; ++i) {
    uint16_t sample = static_cast<uint16_t>(analogRead(microphoneAdcPin));
    minimum = min(minimum, sample);
    maximum = max(maximum, sample);
    sum += sample;
    sumSquares += static_cast<uint64_t>(sample) * sample;
    delayMicroseconds(125);
  }
  uint32_t average = static_cast<uint32_t>(sum / sampleCount);
  double variance = static_cast<double>(sumSquares) / sampleCount -
      static_cast<double>(average) * average;
  uint32_t rms = static_cast<uint32_t>(sqrt(max(0.0, variance)));
  Serial.printf("Mic ADC GPIO%d: avg=%lu min=%u max=%u peak_to_peak=%u rms=%lu\n",
      microphoneAdcPin, static_cast<unsigned long>(average), minimum, maximum,
      static_cast<unsigned>(maximum - minimum), static_cast<unsigned long>(rms));
}

void setup() {
  Serial.begin(115200);
  delay(300);
  pinMode(microphoneAdcPin, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(microphoneAdcPin, ADC_11db);
  initTtsAudioPlayer();
#if ENABLE_MOTORS
  pinMode(MOTOR_LEFT_PIN, OUTPUT);
  pinMode(MOTOR_RIGHT_PIN, OUTPUT);
#else
  Serial.println("CAMERA-ONLY MODE: motor outputs are disabled");
#endif
  stopMotors();
  cameraReady = initCamera();
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  webSocket.onEvent(webSocketEvent);
  webSocket.begin(BACKEND_HOST, BACKEND_PORT, CAMERA_WS_PATH);
  webSocket.setReconnectInterval(3000);
  // A measured full-frame write can take 11-13 s on this Wi-Fi link.
  // Allow the receive loop to process queued Pong frames after a slow write.
  webSocket.enableHeartbeat(15000, 15000, 2);
  Serial.printf("Target: ws://%s:%d%s\n", BACKEND_HOST, BACKEND_PORT, CAMERA_WS_PATH);
  char ttsPath[96];
  snprintf(ttsPath, sizeof(ttsPath), "/ws/tts?device_id=%s", "esp32s3_cam_001");
  ttsWebSocket.onEvent(ttsWebSocketEvent);
  ttsWebSocket.begin(BACKEND_HOST, BACKEND_PORT, ttsPath);
  ttsWebSocket.setReconnectInterval(3000);
  ttsWebSocket.enableHeartbeat(15000, 15000, 2);
  if (xTaskCreatePinnedToCore(ttsNetworkTask, "tts_ws", 6144, nullptr, 2, nullptr, 0) != pdPASS) {
    Serial.println("TTS network task could not start");
  }
  Serial.printf("TTS target: ws://%s:%d%s\n", BACKEND_HOST, BACKEND_PORT, ttsPath);
}

void loop() {
  updateMotors();
  if (WiFi.status() != WL_CONNECTED) {
    if (wifiWasConnected) Serial.println("WiFi connection lost");
    wifiWasConnected = false;
    if (socketConnected) webSocket.disconnect();
    socketConnected = false;
    awaitingAck = false;
    stopMotors();
    static uint32_t lastWifiRetryMs = 0;
    if (millis() - lastWifiRetryMs >= 10000U) {
      lastWifiRetryMs = millis();
      Serial.println("WiFi reconnecting...");
      WiFi.reconnect();
    }
    delay(2);
    return;
  }
  if (!wifiWasConnected) {
    wifiWasConnected = true;
    Serial.printf("WiFi connected: IP=%s, gateway=%s, RSSI=%d dBm\n",
        WiFi.localIP().toString().c_str(), WiFi.gatewayIP().toString().c_str(), WiFi.RSSI());
  }
  webSocket.loop();
  updateMotors();
  reportMicrophoneLevel();
  if (!socketConnected || !cameraReady) {
    delay(2);
    return;
  }
  updateWifiLocation();
  uint32_t now = millis();
  if (awaitingAck && now - ackWaitStartedMs >= FRAME_ACK_TIMEOUT_MS) {
    Serial.println("Frame ACK timeout; reconnecting without queuing more frames");
    webSocket.disconnect();
    delay(2);
    return;
  }
  // Avoid camera capture/send during an active pulse, so they cannot extend it.
  if (!wifiScanActive && !awaitingAck && !motorActive && now - lastSendMs >= IMAGE_INTERVAL_MS) {
    lastSendMs = now;
    uint32_t captureStarted = millis();
    camera_fb_t* fb = esp_camera_fb_get();
    uint32_t captureMs = millis() - captureStarted;
    if (fb) {
      if (fb->format == captureFormat && fb->width == 320 &&
          fb->height == 240 && fb->len == captureFrameBytes) {
        int64_t capturedAtUs = static_cast<int64_t>(fb->timestamp.tv_sec) * 1000000LL +
            fb->timestamp.tv_usec;
        uint32_t encodeStarted = millis();
#if CAMERA_JPEG_TRANSPORT
        JpegOutput output;
        bool encoded = frame2jpg_cb(fb, CAMERA_JPEG_QUALITY, writeJpeg, &output);
        // The independent JPEG no longer needs the camera's only frame buffer.
        esp_camera_fb_return(fb);
        fb = nullptr;
        if (!encoded || output.overflow || output.length == 0) {
          ++encodeFailures;
          Serial.println("JPEG encoding failed; skipping frame");
          delay(2);
          return;
        }
        size_t payloadLength = output.length;
#else
        const uint8_t* payload = fb->buf;
        size_t payloadLength = fb->len;
#endif
        uint32_t encodeMs = millis() - encodeStarted;
        int64_t ageUs = esp_timer_get_time() - capturedAtUs;
        uint32_t frameAgeMs = ageUs > 0 ? static_cast<uint32_t>(ageUs / 1000LL) : 0;
        sentAtMs = millis();
        awaitingAck = true;
#if CAMERA_JPEG_TRANSPORT
        bool sent = webSocket.sendBIN(jpegPacket, payloadLength, true);
#else
        bool sent = webSocket.sendBIN(payload, payloadLength);
#endif
        if (sent) {
          ++framesSent;
          // TCP writing time is not time spent waiting for the application's ACK.
          ackWaitStartedMs = millis();
        }
        Serial.printf("Frame format=%s, bytes=%u, encode_ms=%lu, send=%s, write_ms=%lu, capture_ms=%lu, age_ms=%lu\n",
            CAMERA_JPEG_TRANSPORT ? "jpeg" : "gray", static_cast<unsigned>(payloadLength),
            static_cast<unsigned long>(encodeMs), sent ? "ok" : "failed",
            static_cast<unsigned long>(millis() - sentAtMs),
            static_cast<unsigned long>(captureMs), static_cast<unsigned long>(frameAgeMs));
        if (!sent) {
          awaitingAck = false;
          webSocket.disconnect();
        }
      } else {
        Serial.printf("Unexpected camera frame: %ux%u, bytes=%u, format=%d\n",
            static_cast<unsigned>(fb->width), static_cast<unsigned>(fb->height),
            static_cast<unsigned>(fb->len), static_cast<int>(fb->format));
      }
      if (fb) esp_camera_fb_return(fb);
    } else {
      ++captureFailures;
      Serial.println("Camera capture failed");
    }
  }
  static uint32_t lastStatsMs = 0;
  if (millis() - lastStatsMs >= 15000U) {
    lastStatsMs = millis();
    Serial.printf("Camera stats: sent=%lu, ack=%lu, capture_fail=%lu, encode_fail=%lu, RSSI=%d dBm\n",
        static_cast<unsigned long>(framesSent), static_cast<unsigned long>(framesAcknowledged),
        static_cast<unsigned long>(captureFailures), static_cast<unsigned long>(encodeFailures), WiFi.RSSI());
  }
  delay(2);
}
