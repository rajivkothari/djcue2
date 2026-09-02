"""AI beat and downbeat detection via Beat This! (CPJKU, ISMIR 2024).

Isolated the same way analysis.py is: nothing else in the package
imports torch. Install the extra with:  pip install autocue[beats]

The model checkpoint (~78 MB) is downloaded on first use.
"""

import numpy as np

# A downbeat reported earlier than this many seconds before the audio
# starts is an edge-effect hallucination in the leading silence, not bar 1.
LEADING_SILENCE_TOLERANCE = 0.06

# Anything quieter than this (dBFS) counts as silence when locating the
# start of the audio.
SILENCE_THRESHOLD_DB = -45.0

_model = None


def _load_model(device: str | None = None):
    global _model
    if _model is None:
        try:
            from beat_this.inference import File2Beats
            import torch
        except ImportError:
            raise ImportError(
                "AI beat detection requires extra dependencies. "
                "Install with: pip install autocue[beats]"
            )
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        _model = File2Beats(checkpoint_path="final0", device=device, dbn=False)
    return _model


def _load_mono(audio_path: str):
    """Return (samples as float32 mono ndarray, sample_rate)."""
    try:
        import soundfile as sf
        y, sr = sf.read(str(audio_path), dtype="float32", always_2d=True)
        return y.mean(axis=1), sr
    except Exception:
        import torchaudio
        y, sr = torchaudio.load(str(audio_path))
        return y.mean(dim=0).numpy(), sr


def audio_start_seconds(y, sr: int,
                        threshold_db: float = SILENCE_THRESHOLD_DB) -> float:
    """Time at which the signal first rises above threshold_db (dBFS)."""
    thresh = 10 ** (threshold_db / 20)
    above = np.abs(y) > thresh
    if not above.any():
        return 0.0
    return float(np.argmax(above)) / sr


def first_downbeat_after(downbeats, audio_start: float,
                         tolerance: float = LEADING_SILENCE_TOLERANCE):
    """First downbeat that isn't in the leading silence, or None.

    Pure helper so the gating rule can be unit-tested without the model.
    """
    for d in downbeats:
        if d >= audio_start - tolerance:
            return float(d)
    return None


def detect_beats(audio_path: str) -> dict:
    """Return {"beats": [...], "downbeats": [...]} as times in seconds.

    Raw model output; see detect_first_downbeat for the silence-gated
    bar-1 estimate.
    """
    beats, downbeats = _load_model()(str(audio_path))
    return {
        "beats": [float(b) for b in beats],
        "downbeats": [float(d) for d in downbeats],
    }


def detect_first_downbeat(audio_path: str) -> float | None:
    """Seconds to the first real downbeat, ignoring any the model places in
    the leading silence before the track starts. None if nothing found."""
    downbeats = detect_beats(audio_path)["downbeats"]
    if not downbeats:
        return None
    y, sr = _load_mono(audio_path)
    return first_downbeat_after(downbeats, audio_start_seconds(y, sr))
