"""Engine DJ quickCues and beatData blob codec.

This module is isolated from all analysis dependencies.
If the Engine DJ blob format changes, only this module needs updating.

Format details are derived from reverse-engineering the Engine DJ
database blobs and cross-referencing with open-source parsers.
"""


def decode_quick_cues(blob: bytes) -> list[dict]:
    """Decode a quickCues blob into a list of cue dicts.

    Each cue dict contains:
        index: int — cue slot (0-7)
        position_samples: float — position in samples
        label: str — cue label
        color_r, color_g, color_b, color_a: int — RGBA color
    """
    raise NotImplementedError("Codec not yet implemented — awaiting format research")


def encode_quick_cues(cues: list[dict], original_blob: bytes) -> bytes:
    """Encode cues back into a quickCues blob.

    Must produce a byte-identical result when given unmodified cues
    decoded from the same original_blob.
    """
    raise NotImplementedError("Codec not yet implemented — awaiting format research")


def decode_beat_data(blob: bytes) -> dict:
    """Decode a beatData blob to extract downbeat positions."""
    raise NotImplementedError("Codec not yet implemented — awaiting format research")
