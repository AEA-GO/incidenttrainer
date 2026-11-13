"""The two clocks a player sees.

- Game time `t`: seconds since the page, shown as M:SS (e.g. "4:00").
- In-world time: the scene's start (`scene.starts_at`, HH:MM) plus game time,
  e.g. "14:35". Players type promise times in this clock.
"""


def clock_text(seconds: int) -> str:
    """Game time as M:SS."""
    return f"{seconds // 60}:{seconds % 60:02d}"


def scene_clock(starts_at: str, t: int) -> str:
    """In-world time: the scene's start (HH:MM) plus game time."""
    hours, minutes = (int(part) for part in starts_at.split(":"))
    total = (hours * 60 + minutes + t // 60) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"
