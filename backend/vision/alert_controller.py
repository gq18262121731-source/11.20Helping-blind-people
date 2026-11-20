"""Small NORMAL → APPROACH → URGENT alert state machine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .approach_detector import ApproachObservation


class AlertLevel(str, Enum):
    NORMAL = "NORMAL"
    APPROACH = "APPROACH"
    URGENT = "URGENT"


@dataclass(frozen=True)
class AlertDecision:
    source_id: str
    track_key: str
    level: AlertLevel
    previous_level: AlertLevel
    direction: str
    vibration: str
    voice_text: str | None
    object_class: str
    track_id: int | None
    changed: bool
    reason: str

    def as_dict(self):
        return {
            "alert_state": self.level.value,
            "previous_alert_state": self.previous_level.value,
            "track_id": self.track_id,
            "approach_reason": self.reason,
        }


@dataclass
class _State:
    level: AlertLevel = AlertLevel.NORMAL
    positive_frames: int = 0
    urgent_frames: int = 0
    quiet_frames: int = 0
    last_alert_at: float = 0.0
    last_seen: int = 0


class AlertController:
    def __init__(self, *, cooldown_seconds: float = 2.0,
                 approach_frames: int = 2, urgent_frames: int = 3,
                 clear_frames: int = 3):
        self.cooldown_seconds = cooldown_seconds
        self.approach_frames = approach_frames
        self.urgent_frames = urgent_frames
        self.clear_frames = clear_frames
        self._states: dict[tuple[str, str], _State] = {}

    def reset(self, source_id: str | None = None):
        if source_id is None:
            self._states.clear()
        else:
            for key in [key for key in self._states if key[0] == source_id]:
                self._states.pop(key, None)

    def _vibration(self, level: AlertLevel, direction: str) -> str:
        if level == AlertLevel.URGENT:
            return "both"
        if direction in ("left", "right"):
            return direction
        return "both"

    def update(self, source_id: str, observations: list[ApproachObservation], now: float,
               frame_index: int) -> list[AlertDecision]:
        decisions = []
        seen = set()
        for observation in observations:
            key = (source_id, observation.track_key)
            seen.add(key)
            state = self._states.setdefault(key, _State())
            state.last_seen = frame_index
            if observation.urgent_candidate:
                state.urgent_frames += 1
                state.positive_frames += 1
                state.quiet_frames = 0
            elif observation.approaching:
                state.positive_frames += 1
                state.urgent_frames = 0
                state.quiet_frames = 0
            else:
                state.positive_frames = 0
                state.urgent_frames = 0
                state.quiet_frames += 1

            previous = state.level
            if state.urgent_frames >= self.urgent_frames:
                state.level = AlertLevel.URGENT
            elif state.positive_frames >= self.approach_frames:
                state.level = AlertLevel.APPROACH
            elif state.quiet_frames >= self.clear_frames:
                state.level = AlertLevel.NORMAL

            changed = state.level != previous
            should_emit = changed and state.level != AlertLevel.NORMAL
            if state.level == AlertLevel.NORMAL and previous != AlertLevel.NORMAL:
                should_emit = True
            if state.level == AlertLevel.URGENT and previous != AlertLevel.URGENT:
                should_emit = True
            if should_emit and not changed:
                should_emit = now - state.last_alert_at >= self.cooldown_seconds
            if should_emit:
                state.last_alert_at = now
                voice = None
                if state.level == AlertLevel.URGENT:
                    side = {"left": "左侧", "right": "右侧"}.get(observation.direction, "前方")
                    name = {"person": "行人"}.get(observation.object_class, observation.object_class)
                    voice = f"{side}有{name}，请注意"
                decisions.append(AlertDecision(
                    source_id=source_id,
                    track_key=observation.track_key,
                    level=state.level,
                    previous_level=previous,
                    direction=observation.direction,
                    vibration="stop" if state.level == AlertLevel.NORMAL else self._vibration(state.level, observation.direction),
                    voice_text=voice,
                    object_class=observation.object_class,
                    track_id=observation.track_id,
                    changed=changed,
                    reason=("target_lost_or_trend_stopped" if state.level == AlertLevel.NORMAL
                            else "central_target_area_increasing" if state.level == AlertLevel.URGENT
                            else "focus_target_area_increasing"),
                ))
        for key, state in list(self._states.items()):
            if key[0] == source_id and frame_index - state.last_seen > self.clear_frames:
                if state.level != AlertLevel.NORMAL:
                    decisions.append(AlertDecision(
                        source_id=source_id,
                        track_key=key[1],
                        level=AlertLevel.NORMAL,
                        previous_level=state.level,
                        direction="front",
                        vibration="stop",
                        voice_text=None,
                        object_class="unknown",
                        track_id=None,
                        changed=True,
                        reason="target_lost_or_frame_stale",
                    ))
                self._states.pop(key, None)
        return decisions
