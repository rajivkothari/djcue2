"""Shared constants and helpers used by both the CLI and the web GUI."""

from autocue.codec import decode_beat_data, decode_track_data


# Engine DJ standard cue colors as (A, R, G, B) tuples.
ENGINE_COLORS = {
    "yellow":  (255, 0xEA, 0xC5, 0x32),
    "orange":  (255, 0xEA, 0x8F, 0x32),
    "purple":  (255, 0xB8, 0x55, 0xBF),
    "red":     (255, 0xBA, 0x2A, 0x41),
    "green":   (255, 0x86, 0xC6, 0x4B),
    "teal":    (255, 0x20, 0xC6, 0x7C),
    "cyan":    (255, 0x00, 0xA8, 0xB1),
    "blue":    (255, 0x15, 0x8E, 0xE2),
}

# Same colors as CSS hex strings for the web frontend.
ENGINE_COLORS_HEX = {
    name: f"#{r:02x}{g:02x}{b:02x}"
    for name, (a, r, g, b) in ENGINE_COLORS.items()
}

# Default color per cue slot (1-8) when a template doesn't specify one.
DEFAULT_CUE_COLORS = {
    1: "yellow", 2: "orange", 3: "purple", 4: "red",
    5: "green", 6: "teal", 7: "cyan", 8: "blue",
}


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def get_sample_rate(track: dict) -> float:
    """Get the sample rate a track's cue positions are expressed in."""
    if track["beat_data_blob"]:
        return decode_beat_data(track["beat_data_blob"])["sample_rate"]
    if track["track_data_blob"]:
        return decode_track_data(track["track_data_blob"])["sample_rate"]
    return 44100.0


def parse_time(time_str: str) -> float:
    """Parse time string like '1:32.5' or '92.5' into seconds."""
    if ':' in time_str:
        parts = time_str.split(':')
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    return float(time_str)


def format_time(seconds: float) -> str:
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}:{secs:05.2f}"
