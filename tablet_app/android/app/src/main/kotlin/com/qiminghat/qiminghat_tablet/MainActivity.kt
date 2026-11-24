package com.qiminghat.qiminghat_tablet

import android.Manifest
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Handler
import android.os.Looper
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.Executors
import kotlin.math.sqrt

class MainActivity : FlutterActivity() {
    private val voicePermissionRequest = 7104
    private val sampleRate = 16_000
    private val handler = Handler(Looper.getMainLooper())
    private val recorderExecutor = Executors.newSingleThreadExecutor()
    private var voiceChannel: MethodChannel? = null
    private var voiceResult: MethodChannel.Result? = null
    private var session = 0
    private var permissionSession: Int? = null
    private var recording: Recording? = null

    private class Recording(val session: Int) {
        @Volatile var stopRequested = false
        @Volatile var cancelled = false
        @Volatile var recorder: AudioRecord? = null
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        voiceChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "qiming/family_voice")
        voiceChannel?.setMethodCallHandler { call, result ->
            when (call.method) {
                "available" -> result.success(
                    packageManager.hasSystemFeature(PackageManager.FEATURE_MICROPHONE) &&
                        AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT) > 0
                )
                "start" -> startVoice(result)
                "stop" -> {
                    if (recording == null) cancelVoice() else stopRecorder(recording!!, false)
                    result.success(null)
                }
                "cancel" -> {
                    cancelVoice()
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
    }

    private fun startVoice(result: MethodChannel.Result) {
        if (voiceResult != null) {
            result.error("busy", "正在录音，请先结束本次录音。", null)
            return
        }
        voiceResult = result
        session++
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            val blocked = getPreferences(MODE_PRIVATE).getBoolean("family_microphone_blocked", false)
            if (blocked && !shouldShowRequestPermissionRationale(Manifest.permission.RECORD_AUDIO)) {
                finishVoice(errorCode = "permission_permanently_denied", message = "麦克风权限已关闭，请到系统设置的应用权限中允许麦克风，再重试。")
                return
            }
            permissionSession = session
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), voicePermissionRequest)
            return
        }
        beginRecording()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != voicePermissionRequest || voiceResult == null || permissionSession != session) return
        permissionSession = null
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
            getPreferences(MODE_PRIVATE).edit().putBoolean("family_microphone_blocked", false).apply()
            beginRecording()
        } else {
            val permanent = !shouldShowRequestPermissionRationale(Manifest.permission.RECORD_AUDIO)
            if (grantResults.isNotEmpty()) {
                getPreferences(MODE_PRIVATE).edit().putBoolean("family_microphone_blocked", permanent).apply()
            }
            finishVoice(
                errorCode = if (permanent) "permission_permanently_denied" else "permission_denied",
                message = if (permanent) "请到系统设置的应用权限中允许麦克风，再重试。" else "语音留言需要麦克风权限，请允许后重试，也可以使用文字留言。"
            )
        }
    }

    private fun beginRecording() {
        if (voiceResult == null) return
        val current = Recording(session)
        recording = current
        recorderExecutor.execute { captureAudio(current) }
    }

    private fun captureAudio(current: Recording) {
        var audio: AudioRecord? = null
        var wav: ByteArray? = null
        var errorCode: String? = null
        var message: String? = null
        try {
            val minimum = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            if (minimum <= 0) throw IllegalStateException("unsupported_audio")
            audio = AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION, sampleRate,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minimum * 2, 4096)
            )
            current.recorder = audio
            if (current.cancelled) return
            if (audio.state != AudioRecord.STATE_INITIALIZED) throw IllegalStateException("audio_initialization")
            audio.startRecording()
            if (audio.recordingState != AudioRecord.RECORDSTATE_RECORDING) throw IllegalStateException("microphone_busy")
            val pcm = ByteArrayOutputStream()
            val samples = ShortArray(640)
            val bytes = ByteArray(samples.size * 2)
            var totalSamples = 0
            var speechSamples = 0
            var lastSpeechSample = 0
            while (!current.stopRequested && !current.cancelled && totalSamples < sampleRate * 20) {
                val count = audio.read(samples, 0, minOf(samples.size, sampleRate * 20 - totalSamples), AudioRecord.READ_BLOCKING)
                if (count < 0) {
                    if (current.stopRequested || current.cancelled) break
                    throw IllegalStateException("audio_read_$count")
                }
                if (count == 0) continue
                var energy = 0.0
                for (index in 0 until count) {
                    val value = samples[index].toInt()
                    energy += value.toDouble() * value
                    bytes[index * 2] = (value and 0xff).toByte()
                    bytes[index * 2 + 1] = ((value ushr 8) and 0xff).toByte()
                }
                pcm.write(bytes, 0, count * 2)
                totalSamples += count
                if (sqrt(energy / count) >= 350.0) {
                    speechSamples += count
                    lastSpeechSample = totalSamples
                }
                if (totalSamples >= sampleRate * 0.4 &&
                    speechSamples >= sampleRate / 10 &&
                    totalSamples - lastSpeechSample >= sampleRate * 1.2
                ) break
            }
            if (!current.cancelled) {
                if (totalSamples < sampleRate * 0.4) {
                    errorCode = "too_short"
                    message = "录音时间太短，请说完一句话后再结束。"
                } else {
                    wav = wavBytes(pcm.toByteArray())
                }
            }
        } catch (_: SecurityException) {
            errorCode = "permission_denied"
            message = "无法访问麦克风，请在系统应用权限中允许麦克风。"
        } catch (_: Exception) {
            errorCode = "audio_error"
            message = "录音失败，请关闭占用麦克风的应用后重试。"
        } finally {
            current.recorder = null
            try { audio?.stop() } catch (_: Exception) {}
            audio?.release()
            val resultBytes = wav
            val resultError = errorCode
            val resultMessage = message
            handler.post {
                if (recording === current && current.session == session && !current.cancelled) {
                    finishVoice(wav = resultBytes, errorCode = resultError, message = resultMessage)
                }
            }
        }
    }

    private fun wavBytes(pcm: ByteArray): ByteArray {
        return ByteBuffer.allocate(44 + pcm.size).order(ByteOrder.LITTLE_ENDIAN).apply {
            put("RIFF".toByteArray(Charsets.US_ASCII))
            putInt(36 + pcm.size)
            put("WAVEfmt ".toByteArray(Charsets.US_ASCII))
            putInt(16)
            putShort(1.toShort())
            putShort(1.toShort())
            putInt(sampleRate)
            putInt(sampleRate * 2)
            putShort(2.toShort())
            putShort(16.toShort())
            put("data".toByteArray(Charsets.US_ASCII))
            putInt(pcm.size)
            put(pcm)
        }.array()
    }

    private fun stopRecorder(current: Recording, cancel: Boolean) {
        current.cancelled = cancel
        current.stopRequested = true
        try { current.recorder?.stop() } catch (_: Exception) {}
    }

    private fun cancelVoice() {
        recording?.let { stopRecorder(it, true) }
        finishVoice(errorCode = "cancelled", message = "本次录音已取消。")
    }

    private fun finishVoice(wav: ByteArray? = null, errorCode: String? = null, message: String? = null) {
        val result = voiceResult
        voiceResult = null
        permissionSession = null
        recording = null
        session++
        if (result != null) {
            if (errorCode != null) result.error(errorCode, message, null)
            else if (wav != null) result.success(wav)
            else result.error("audio_error", "未获取到有效录音，请重试。", null)
        }
    }

    override fun onStop() {
        cancelVoice()
        super.onStop()
    }

    override fun cleanUpFlutterEngine(flutterEngine: FlutterEngine) {
        cancelVoice()
        voiceChannel?.setMethodCallHandler(null)
        voiceChannel = null
        recorderExecutor.shutdownNow()
        super.cleanUpFlutterEngine(flutterEngine)
    }
}
