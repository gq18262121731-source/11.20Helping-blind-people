import pytest

from backend.tts_protocol import (
    CHUNK,
    END,
    PLAYBACK_DONE,
    READY,
    TtsProtocolError,
    pack_chunk,
    pack_control,
    pack_start,
    STREAMING_AUDIO_BYTES,
    unpack_status,
)


def test_start_frame_contains_pcm_format_fields():
    frame = pack_start(7, 24_000, 1920)
    assert frame[:4] == b"TS\x01\x01"
    assert len(frame) == 16
    assert int.from_bytes(frame[4:8], "little") == 7
    assert int.from_bytes(frame[8:12], "little") == 24_000
    assert int.from_bytes(frame[12:16], "little") == 1920


def test_start_frame_accepts_unknown_stream_length_sentinel():
    frame = pack_start(8, 16_000, STREAMING_AUDIO_BYTES)
    assert int.from_bytes(frame[12:16], "little") == STREAMING_AUDIO_BYTES


def test_chunk_frame_has_request_sequence_and_pcm():
    frame = pack_chunk(9, 3, b"\x00\x00\x01\x00")
    assert frame[:4] == b"TS\x01" + bytes([CHUNK])
    assert int.from_bytes(frame[4:8], "little") == 9
    assert int.from_bytes(frame[8:12], "little") == 3
    assert frame[12:] == b"\x00\x00\x01\x00"


def test_status_frames_are_validated_and_decoded():
    assert unpack_status(pack_control(READY, 0)) == {"type": READY, "request_id": 0, "value": 0}
    assert unpack_status(pack_control(PLAYBACK_DONE, 21, 4)) == {
        "type": PLAYBACK_DONE, "request_id": 21, "value": 4,
    }
    with pytest.raises(TtsProtocolError, match="12 bytes"):
        unpack_status(b"bad")
    with pytest.raises(TtsProtocolError, match="positive even"):
        pack_start(1, 24_000, 3)
    with pytest.raises(TtsProtocolError, match="even bytes"):
        pack_chunk(1, 0, b"\x00")


def test_end_control_uses_chunk_count_as_value():
    frame = pack_control(END, 44, 5)
    assert len(frame) == 12
    assert frame[3] == END
    assert int.from_bytes(frame[8:12], "little") == 5
