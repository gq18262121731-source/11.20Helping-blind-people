#pragma once
#include "config.h"
#include "websocket_manager.h"

// MAX9814 是模拟模块。这里提供 16 kHz/16 bit 单声道 PCM 分帧接口；
// 默认由后续 VAD/按键触发，避免持续上传无效音频。
inline void sampleAudioFrame(){
  if(!audioWsConnected()) return;
  static int16_t pcm[320];
  for(int i=0;i<320;i++){ uint16_t v=analogRead(PIN_MIC_ADC); pcm[i]=(int16_t)((int)v-2048)<<4; delayMicroseconds(62); }
  sendAudioBinaryFrame((uint8_t*)pcm,sizeof(pcm));
}

// Hardware button/VAD integration can bracket a capture with these calls.
inline void beginAudioSession(const char* session){ if(audioWsConnected()) sendAudioStart(session); }
inline void endAudioSession(const char* session){ if(audioWsConnected()) sendAudioEnd(session); }
