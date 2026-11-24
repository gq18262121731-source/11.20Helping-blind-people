#pragma once
#include <WiFi.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <cstring>
#include "config.h"
WebSocketsClient ws;
WebSocketsClient audioWs;
inline bool wsConnected(){return WiFi.status()==WL_CONNECTED;}
inline bool audioWsConnected(){return WiFi.status()==WL_CONNECTED;}
inline void initWifiAndWebSocket(){WiFi.begin(WIFI_SSID,WIFI_PASSWORD);uint32_t t=millis();while(WiFi.status()!=WL_CONNECTED&&millis()-t<15000){delay(300);Serial.print('.');}ws.begin(BACKEND_HOST,BACKEND_PORT,"/ws/device");ws.onEvent([](WStype_t type,uint8_t* p,size_t n){if(type==WStype_TEXT){StaticJsonDocument<512>d;if(deserializeJson(d,p,n)==DeserializationError::Ok&&d["type"]=="device_command"&&d["command"]=="vibrate")vibrate(d["direction"],d["duration_ms"]|300);}});ws.setReconnectInterval(3000);audioWs.begin(BACKEND_HOST,BACKEND_PORT,"/ws/audio");audioWs.setReconnectInterval(3000);}
inline void websocketLoop(){ws.loop();audioWs.loop();}
inline void sendHeartbeat(){StaticJsonDocument<256>d;d["type"]="heartbeat";d["device_id"]=DEVICE_ID;d["firmware_version"]="0.1.0";d["camera_ok"]=true;d["mic_ok"]=true;d["timestamp"]=millis();String s;serializeJson(d,s);ws.sendTXT(s);}
inline void sendOnline(){StaticJsonDocument<256>d;d["type"]="device_online";d["device_id"]=DEVICE_ID;d["firmware_version"]="0.1.0";d["timestamp"]=millis();String s;serializeJson(d,s);ws.sendTXT(s);}
inline void sendBinaryFrame(uint8_t type,const uint8_t* data,size_t len){
  // 一个 WebSocket 二进制帧包含完整协议头和 payload，避免拆成两个业务帧。
  size_t total=26+len; uint8_t* out=(uint8_t*)malloc(total); if(!out)return;
  out[0]='Q';out[1]='H';out[2]=1;out[3]=type;out[4]=26;out[5]=0;
  uint32_t seq=millis(); memcpy(out+6,&seq,4); uint64_t ts=millis(); memcpy(out+10,&ts,8);
  uint16_t z=0; memcpy(out+18,&z,2); memcpy(out+20,&z,2); uint32_t n=len; memcpy(out+22,&n,4);
  memcpy(out+26,data,len); ws.sendBIN(out,total); free(out);
}
inline void sendAudioStart(const char* session){StaticJsonDocument<256>d;d["type"]="audio_start";d["device_id"]=DEVICE_ID;d["session_id"]=session;d["sample_rate"]=16000;d["channels"]=1;d["bits_per_sample"]=16;String s;serializeJson(d,s);audioWs.sendTXT(s);}
inline void sendAudioEnd(const char* session){StaticJsonDocument<128>d;d["type"]="audio_end";d["session_id"]=session;String s;serializeJson(d,s);audioWs.sendTXT(s);}
inline void sendAudioBinaryFrame(const uint8_t* data,size_t len){size_t total=26+len;uint8_t* out=(uint8_t*)malloc(total);if(!out)return;out[0]='Q';out[1]='H';out[2]=1;out[3]=2;out[4]=26;out[5]=0;uint32_t seq=millis();memcpy(out+6,&seq,4);uint64_t ts=millis();memcpy(out+10,&ts,8);uint16_t z=0;memcpy(out+18,&z,2);memcpy(out+20,&z,2);uint32_t n=len;memcpy(out+22,&n,4);memcpy(out+26,data,len);audioWs.sendBIN(out,total);free(out);}
inline void handleCommands(){}
