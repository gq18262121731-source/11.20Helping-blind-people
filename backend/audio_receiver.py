"""ASGI-independent audio receiver facade used by ``/ws/audio`` and tests."""
from .audio_processor import AudioProcessor

audio_processor = AudioProcessor()
