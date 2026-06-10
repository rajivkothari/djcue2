"""Structural audio analysis for cue point detection.

Uses librosa for feature extraction. This module has no dependency on
the Engine DJ codec or database — the CLI orchestrates between them.
"""

try:
    import librosa
    import numpy as np
    from scipy.signal import find_peaks
except ImportError:
    raise ImportError(
        "Analysis requires extra dependencies. "
        "Install with: pip install autocue[analysis]"
    )


def analyze_structure(audio_path: str, sr: int | None = None,
                      **params) -> dict:
    """Analyze audio and return detected structural positions.

    Returns dict with:
        sample_rate: float
        positions: dict mapping detection key -> sample position (or None)
        confidences: dict mapping detection key -> 0.0–1.0
    """
    y, actual_sr = librosa.load(audio_path, sr=sr, mono=True)

    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    onset_env = librosa.onset.onset_strength(y=y, sr=actual_sr,
                                             hop_length=hop)

    positions = {}
    confidences = {}

    pos, conf = _detect_mix_in(y, actual_sr, rms, onset_env, hop, **params)
    positions["mix_in"] = pos
    confidences["mix_in"] = conf

    pos, conf = _detect_first_vocal(y, actual_sr, hop, **params)
    positions["first_vocal"] = pos
    confidences["first_vocal"] = conf

    pos, conf = _detect_first_drop(y, actual_sr, rms, onset_env, hop,
                                   positions.get("mix_in"), **params)
    positions["first_drop"] = pos
    confidences["first_drop"] = conf

    pos, conf = _detect_breakdown(y, actual_sr, rms, hop,
                                  positions.get("first_drop"), **params)
    positions["breakdown"] = pos
    confidences["breakdown"] = conf

    pos, conf = _detect_second_drop(y, actual_sr, rms, onset_env, hop,
                                    positions.get("breakdown"), **params)
    positions["second_drop"] = pos
    confidences["second_drop"] = conf

    pos, conf = _detect_outro(y, actual_sr, rms, hop, **params)
    positions["outro_start"] = pos
    confidences["outro_start"] = conf

    return {
        "sample_rate": float(actual_sr),
        "positions": positions,
        "confidences": confidences,
    }


def analyze_structure_from_array(y: np.ndarray, sr: int,
                                 **params) -> dict:
    """Same as analyze_structure but accepts a pre-loaded array."""
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)

    positions = {}
    confidences = {}

    pos, conf = _detect_mix_in(y, sr, rms, onset_env, hop, **params)
    positions["mix_in"] = pos
    confidences["mix_in"] = conf

    pos, conf = _detect_first_vocal(y, sr, hop, **params)
    positions["first_vocal"] = pos
    confidences["first_vocal"] = conf

    pos, conf = _detect_first_drop(y, sr, rms, onset_env, hop,
                                   positions.get("mix_in"), **params)
    positions["first_drop"] = pos
    confidences["first_drop"] = conf

    pos, conf = _detect_breakdown(y, sr, rms, hop,
                                  positions.get("first_drop"), **params)
    positions["breakdown"] = pos
    confidences["breakdown"] = conf

    pos, conf = _detect_second_drop(y, sr, rms, onset_env, hop,
                                    positions.get("breakdown"), **params)
    positions["second_drop"] = pos
    confidences["second_drop"] = conf

    pos, conf = _detect_outro(y, sr, rms, hop, **params)
    positions["outro_start"] = pos
    confidences["outro_start"] = conf

    return {
        "sample_rate": float(sr),
        "positions": positions,
        "confidences": confidences,
    }


def _detect_mix_in(y, sr, rms, onset_env, hop,
                   intro_energy_percentile=25, **_):
    """First strong onset after the intro ends (energy crosses threshold)."""
    threshold = np.percentile(rms, intro_energy_percentile)
    above = np.where(rms > threshold)[0]
    if len(above) == 0:
        return None, 0.0

    first_above = above[0]

    search_start = max(0, first_above - 4)
    search_end = min(len(onset_env), first_above + 16)
    region = onset_env[search_start:search_end]
    if len(region) == 0:
        return None, 0.0

    peak_frame = search_start + int(np.argmax(region))
    position = librosa.frames_to_samples(peak_frame, hop_length=hop)

    confidence = min(1.0, float(rms[first_above] / (threshold + 1e-10)))
    return float(position), confidence


def _detect_first_vocal(y, sr, hop, **_):
    """First sustained vocal region using harmonic separation + spectral centroid."""
    y_harmonic, _ = librosa.effects.hpss(y)
    centroid = librosa.feature.spectral_centroid(
        y=y_harmonic, sr=sr, hop_length=hop
    )[0]
    rms_harm = librosa.feature.rms(y=y_harmonic, hop_length=hop)[0]

    vocal_low = 300.0
    vocal_high = 3000.0
    in_vocal_range = (centroid >= vocal_low) & (centroid <= vocal_high)

    median_energy = np.median(rms_harm)
    if median_energy < 1e-10:
        return None, 0.0
    has_energy = rms_harm > median_energy * 1.5

    vocal_frames = in_vocal_range & has_energy

    min_frames = int(1.0 * sr / hop)
    count = 0
    for i in range(len(vocal_frames)):
        if vocal_frames[i]:
            count += 1
            if count >= min_frames:
                start = i - min_frames + 1
                position = librosa.frames_to_samples(start, hop_length=hop)
                return float(position), 0.5
        else:
            count = 0

    return None, 0.0


def _detect_first_drop(y, sr, rms, onset_env, hop,
                       mix_in_pos=None, drop_energy_ratio=1.5, **_):
    """First energy spike after a build — chorus or drop."""
    window = max(1, int(2.0 * sr / hop))
    rms_smooth = np.convolve(rms, np.ones(window) / window, mode='same')

    peaks, _ = find_peaks(
        onset_env,
        height=np.percentile(onset_env, 75),
        distance=int(2.0 * sr / hop),
    )

    start_frame = 0
    if mix_in_pos is not None:
        start_frame = (librosa.samples_to_frames(int(mix_in_pos),
                                                  hop_length=hop)
                       + int(4.0 * sr / hop))

    for peak in peaks:
        if peak < start_frame or peak >= len(rms_smooth):
            continue
        before_start = max(0, peak - window)
        after_end = min(len(rms_smooth), peak + window)
        energy_before = float(np.mean(rms_smooth[before_start:peak])) + 1e-10
        energy_after = float(np.mean(rms_smooth[peak:after_end])) + 1e-10
        ratio = energy_after / energy_before

        if ratio >= drop_energy_ratio:
            position = librosa.frames_to_samples(peak, hop_length=hop)
            confidence = min(1.0, ratio / drop_energy_ratio)
            return float(position), float(confidence)

    return None, 0.0


def _detect_breakdown(y, sr, rms, hop,
                      drop_pos=None,
                      breakdown_min_duration_seconds=4.0, **_):
    """Sustained energy dip after the first drop."""
    if drop_pos is None:
        return None, 0.0

    start_frame = librosa.samples_to_frames(int(drop_pos), hop_length=hop)
    median_rms = float(np.median(rms))
    if median_rms < 1e-10:
        return None, 0.0

    min_frames = int(breakdown_min_duration_seconds * sr / hop)
    below_count = 0

    for i in range(start_frame, len(rms)):
        if rms[i] < median_rms * 0.7:
            below_count += 1
            if below_count >= min_frames:
                breakdown_start = i - min_frames + 1
                position = librosa.frames_to_samples(
                    breakdown_start, hop_length=hop
                )
                region_mean = float(np.mean(rms[breakdown_start:i]))
                confidence = min(1.0, 1.0 - (region_mean / median_rms))
                return float(position), confidence
        else:
            below_count = 0

    return None, 0.0


def _detect_second_drop(y, sr, rms, onset_env, hop,
                        breakdown_pos=None,
                        drop_energy_ratio=1.5, **_):
    """Second major energy spike after the breakdown."""
    if breakdown_pos is None:
        return None, 0.0

    window = max(1, int(2.0 * sr / hop))
    rms_smooth = np.convolve(rms, np.ones(window) / window, mode='same')

    peaks, _ = find_peaks(
        onset_env,
        height=np.percentile(onset_env, 75),
        distance=int(2.0 * sr / hop),
    )

    start_frame = librosa.samples_to_frames(int(breakdown_pos),
                                             hop_length=hop)

    for peak in peaks:
        if peak < start_frame or peak >= len(rms_smooth):
            continue
        before_start = max(0, peak - window)
        after_end = min(len(rms_smooth), peak + window)
        energy_before = float(np.mean(rms_smooth[before_start:peak])) + 1e-10
        energy_after = float(np.mean(rms_smooth[peak:after_end])) + 1e-10
        ratio = energy_after / energy_before

        if ratio >= drop_energy_ratio:
            position = librosa.frames_to_samples(peak, hop_length=hop)
            confidence = min(1.0, ratio / drop_energy_ratio)
            return float(position), float(confidence)

    return None, 0.0


def _detect_outro(y, sr, rms, hop, outro_energy_percentile=25, **_):
    """Point where energy begins its final sustained decline."""
    threshold = np.percentile(rms, outro_energy_percentile)
    above = np.where(rms > threshold)[0]
    if len(above) == 0:
        return None, 0.0

    last_above = above[-1]

    min_outro_frame = int(len(rms) * 0.5)
    if last_above < min_outro_frame:
        return None, 0.0

    position = librosa.frames_to_samples(int(last_above), hop_length=hop)
    confidence = 0.7
    return float(position), confidence
