"""Declared companion containers. Tests control readiness and assertions."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Companion:
    image: str
    command: tuple[str, ...] = ()
    ready: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    user: str | None = None
