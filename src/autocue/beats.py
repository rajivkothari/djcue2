"""AI beat / downbeat detection and beat-grid construction.

Detection is Beat This! (CPJKU, ISMIR 2024). Isolated the same way
analysis.py is: nothing else in the package imports torch. Install the
extra with:  pip install autocue[beats]

The model checkpoint (~78 MB) is downloaded on first use. Results are
cached per file for the life of the process so the editor can ask for
the first downbeat and then the full grid without re-running inference.

fit_grid() is pure numpy and is what the tests exercise.
"""

import math
import os

import numpy as np

# A downbeat reported earlier than this many seconds before the audio
# starts is an edge-effect hallucination in the leading silence, not bar 1.
LEADING_SILENCE_TOLERANCE = 0.06

# Anything quieter than this (dBFS) counts as silence when locating the
# start of the audio.
SILENCE_THRESHOLD_DB = -45.0

# A fitted grid whose beats deviate from the detected beats by more than
# this (RMS, as a fraction of one beat) is flagged as variable tempo.
VARIABLE_TEMPO_RMS_FRACTION = 0.06

BEATS_PER_BAR = 4

_model = None
_cache: dict = {}


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
    """First downbeat that isn't in the leading silence, or None."""
    for d in downbeats:
        if d >= audio_start - tolerance:
            return float(d)
    return None


def _cache_key(audio_path: str):
    st = os.stat(audio_path)
    return (os.path.abspath(audio_path), st.st_mtime_ns, st.st_size)


def detect_beats(audio_path: str) -> dict:
    """Raw model output plus the audio's duration and start of sound.

    {"beats": [s…], "downbeats": [s…], "duration": s, "audio_start": s}
    """
    key = _cache_key(audio_path)
    if key in _cache:
        return _cache[key]
    beats, downbeats = _load_model()(str(audio_path))
    y, sr = _load_mono(audio_path)
    result = {
        "beats": [float(b) for b in beats],
        "downbeats": [float(d) for d in downbeats],
        "duration": len(y) / sr,
        "audio_start": audio_start_seconds(y, sr),
    }
    _cache[key] = result
    return result


def detect_first_downbeat(audio_path: str) -> float | None:
    """Seconds to the first real downbeat, ignoring any the model places in
    the leading silence before the track starts. None if nothing found."""
    d = detect_beats(audio_path)
    return first_downbeat_after(d["downbeats"], d["audio_start"])


# ---------------------------------------------------------------------------
# Grid fitting (pure numpy)
# ---------------------------------------------------------------------------

def _fold_to_octave(period: float, bpm_hint: float | None) -> float:
    """Beat trackers occasionally answer at half or double tempo. If the
    DJ software already has a BPM, pick the octave that agrees with it."""
    if not bpm_hint or bpm_hint <= 0:
        return period
    best = period
    for mult in (0.5, 1.0, 2.0):
        cand = period * mult
        if abs(60 / cand - bpm_hint) < abs(60 / best - bpm_hint):
            best = cand
    return best


def fit_grid(beats, downbeats, duration: float, audio_start: float = 0.0,
             bpm_hint: float | None = None) -> dict | None:
    """Fit a constant-tempo grid to detected beats and anchor bar 1 on the
    first real downbeat.

    Returns None if there is too little to work with. Otherwise:
      tempo_bpm, seconds_per_beat, first_downbeat, beats (whole track),
      downbeats (whole track), rms_residual_ms, variable_tempo, n_detected
    """
    beats = np.asarray(sorted(float(b) for b in beats))
    if len(beats) < 8:
        return None

    period = float(np.median(np.diff(beats)))
    if period <= 0:
        return None
    period = _fold_to_octave(period, bpm_hint)

    # Which integer beat is each detection? Count gaps one at a time rather
    # than dividing the total elapsed time: the median period is only
    # approximate (the model quantizes to 20 ms frames), and any error in
    # it would accumulate across the track and mislabel later beats.
    steps = np.maximum(1, np.round(np.diff(beats) / period))
    idx = np.concatenate([[0.0], np.cumsum(steps)])
    A = np.column_stack([idx, np.ones_like(idx)])
    (slope, intercept), *_ = np.linalg.lstsq(A, beats, rcond=None)
    if slope <= 0:
        return None
    period, phase = float(slope), float(intercept)
    residual = beats - (phase + idx * period)
    rms = float(np.sqrt(np.mean(residual ** 2)))

    fd = first_downbeat_after(downbeats, audio_start)
    if fd is None:
        # No usable downbeat: take the first detected beat at/after the
        # audio start as bar 1 and say so via low confidence.
        after = beats[beats >= audio_start - LEADING_SILENCE_TOLERANCE]
        fd = float(after[0]) if len(after) else float(beats[0])
        downbeat_confidence = 0.3
    else:
        downbeat_confidence = 1.0
    k0 = round((fd - phase) / period)
    first_downbeat = phase + k0 * period

    kmin = math.ceil((0.0 - first_downbeat) / period)
    kmax = math.floor((duration - first_downbeat) / period)
    ks = range(kmin, kmax + 1)
    grid = [first_downbeat + k * period for k in ks]
    grid_downbeats = [first_downbeat + k * period for k in ks
                      if k % BEATS_PER_BAR == 0]

    return {
        "tempo_bpm": 60.0 / period,
        "seconds_per_beat": period,
        "first_downbeat": first_downbeat,
        "first_beat_index": kmin,
        "beats": grid,
        "downbeats": grid_downbeats,
        "rms_residual_ms": rms * 1000.0,
        "variable_tempo": rms > VARIABLE_TEMPO_RMS_FRACTION * period,
        "n_detected": int(len(beats)),
        "downbeat_confidence": downbeat_confidence,
    }


def build_grid(audio_path: str, bpm_hint: float | None = None) -> dict | None:
    """Run detection and fit a grid for a file. See fit_grid for the shape."""
    d = detect_beats(audio_path)
    return fit_grid(d["beats"], d["downbeats"], d["duration"],
                    d["audio_start"], bpm_hint=bpm_hint)
