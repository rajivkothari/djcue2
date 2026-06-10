"""Structural audio analysis for cue point detection.

Uses librosa for feature extraction. This module has no dependency on
the Engine DJ codec or database — the CLI orchestrates between them.

Detection strategy:
  1. Find structural boundaries via chroma/MFCC self-similarity novelty
  2. Compute per-section energy profiles
  3. Map sections to cue types based on energy dynamics and position
"""

try:
    import librosa
    import numpy as np
    from scipy.signal import find_peaks
    from scipy.spatial.distance import cdist
except ImportError:
    raise ImportError(
        "Analysis requires extra dependencies. "
        "Install with: pip install autocue[analysis]"
    )

STRUCTURAL_HOP = 4096


def analyze_structure(audio_path: str, sr: int | None = None,
                      **params) -> dict:
    y, actual_sr = librosa.load(audio_path, sr=sr, mono=True)
    return _analyze(y, actual_sr, **params)


def analyze_structure_from_array(y: np.ndarray, sr: int,
                                 **params) -> dict:
    return _analyze(y, sr, **params)


def _analyze(y, sr, **params):
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)

    boundaries = _find_structural_boundaries(y, sr, **params)
    sections = _build_sections(boundaries, rms, sr, hop)

    positions = {}
    confidences = {}

    pos, conf = _detect_mix_in(sections, y, sr, rms, hop, **params)
    positions["mix_in"] = pos
    confidences["mix_in"] = conf

    pos, conf = _detect_first_vocal(sections, y, sr, hop, **params)
    positions["first_vocal"] = pos
    confidences["first_vocal"] = conf

    pos, conf = _detect_first_drop(sections, y, sr, rms, hop, **params)
    positions["first_drop"] = pos
    confidences["first_drop"] = conf

    pos, conf = _detect_breakdown(sections, y, sr, rms, hop,
                                  positions.get("first_drop"), **params)
    positions["breakdown"] = pos
    confidences["breakdown"] = conf

    pos, conf = _detect_second_drop(sections, y, sr, rms, hop,
                                    positions.get("breakdown"), **params)
    positions["second_drop"] = pos
    confidences["second_drop"] = conf

    pos, conf = _detect_outro(sections, y, sr, rms, hop, **params)
    positions["outro_start"] = pos
    confidences["outro_start"] = conf

    return {
        "sample_rate": float(sr),
        "positions": positions,
        "confidences": confidences,
    }


# ---------------------------------------------------------------------------
# Structural boundary detection
# ---------------------------------------------------------------------------

def _affinity_matrix(features):
    """Compute a self-similarity affinity matrix using cosine distance.

    Replaces librosa.segment.recurrence_matrix to avoid the sklearn import.
    """
    # features shape: (n_features, n_frames) — cdist wants (n_frames, n_features)
    dist = cdist(features.T, features.T, metric='cosine')
    bandwidth = np.median(dist[dist > 0]) if np.any(dist > 0) else 1.0
    affinity = np.exp(-dist / bandwidth)
    return affinity


def _find_structural_boundaries(y, sr,
                                min_section_seconds=4.0, **_):
    """Find section boundaries using chroma/MFCC self-similarity novelty."""
    hop = STRUCTURAL_HOP

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, hop_length=hop)

    features = np.vstack([
        librosa.util.normalize(chroma, axis=1),
        librosa.util.normalize(mfcc, axis=1),
    ])

    rec = _affinity_matrix(features)

    kernel_size = min(64, max(8, rec.shape[0] // 8))
    novelty = _checkerboard_novelty(rec, kernel_size)

    min_distance = max(1, int(min_section_seconds * sr / hop))
    threshold = np.percentile(novelty[novelty > 0], 60) if np.any(novelty > 0) else 0

    peaks, _ = find_peaks(novelty, distance=min_distance, height=threshold)

    boundary_samples = librosa.frames_to_samples(peaks, hop_length=hop)
    boundaries = [0.0] + [float(b) for b in boundary_samples] + [float(len(y))]
    return boundaries


def _checkerboard_novelty(rec, kernel_size=64):
    """Novelty curve from a recurrence matrix using a checkerboard kernel."""
    n = rec.shape[0]
    half = kernel_size // 2
    if half < 1 or n < kernel_size:
        return np.zeros(n)

    # Integral image for O(1) area sums
    cumsum = np.zeros((n + 1, n + 1))
    cumsum[1:, 1:] = np.cumsum(np.cumsum(rec, axis=0), axis=1)

    def _area_mean(r1, c1, r2, c2):
        area = (r2 - r1) * (c2 - c1)
        if area <= 0:
            return 0.0
        s = cumsum[r2, c2] - cumsum[r1, c2] - cumsum[r2, c1] + cumsum[r1, c1]
        return s / area

    novelty = np.zeros(n)
    for i in range(half, n - half):
        tl = _area_mean(i - half, i - half, i, i)
        tr = _area_mean(i - half, i, i, i + half)
        bl = _area_mean(i, i - half, i + half, i)
        br = _area_mean(i, i, i + half, i + half)
        novelty[i] = (tl + br) - (tr + bl)

    return np.maximum(novelty, 0)


# ---------------------------------------------------------------------------
# Section analysis
# ---------------------------------------------------------------------------

def _build_sections(boundaries, rms, sr, hop):
    """Build a list of section dicts from boundaries and RMS energy."""
    sections = []
    for i in range(len(boundaries) - 1):
        start_sample = boundaries[i]
        end_sample = boundaries[i + 1]

        start_frame = int(start_sample / hop)
        end_frame = min(int(end_sample / hop), len(rms))

        if end_frame <= start_frame:
            continue

        section_rms = rms[start_frame:end_frame]
        sections.append({
            "index": i,
            "start_sample": start_sample,
            "end_sample": end_sample,
            "start_seconds": start_sample / sr,
            "end_seconds": end_sample / sr,
            "duration_seconds": (end_sample - start_sample) / sr,
            "mean_energy": float(np.mean(section_rms)),
            "max_energy": float(np.max(section_rms)),
            "energy_profile": section_rms,
        })

    if not sections:
        return sections

    max_energy = max(s["mean_energy"] for s in sections) or 1e-10
    for s in sections:
        s["relative_energy"] = s["mean_energy"] / max_energy

    return sections


# ---------------------------------------------------------------------------
# Cue detectors (boundary-aware)
# ---------------------------------------------------------------------------

def _detect_mix_in(sections, y, sr, rms, hop,
                   intro_energy_percentile=25, **_):
    """First section boundary where energy rises above the intro level."""
    if len(sections) < 2:
        return None, 0.0

    first_energy = sections[0]["mean_energy"]

    for s in sections[1:]:
        if s["mean_energy"] > first_energy * 1.3 or s["relative_energy"] > 0.4:
            return float(s["start_sample"]), min(1.0, s["relative_energy"] + 0.3)

    # Fallback: first section boundary
    return float(sections[1]["start_sample"]) if len(sections) > 1 else None, 0.5


def _detect_first_vocal(sections, y, sr, hop, **_):
    """First section with vocal-frequency spectral content."""
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop)[0]
    flatness = librosa.feature.spectral_flatness(y=y, hop_length=hop)[0]

    for s in sections:
        if s["start_seconds"] < 2.0:
            continue

        start_frame = int(s["start_sample"] / hop)
        end_frame = min(int(s["end_sample"] / hop), len(centroid))
        if end_frame <= start_frame:
            continue

        sec_centroid = centroid[start_frame:end_frame]
        sec_flatness = flatness[start_frame:end_frame]

        in_vocal = (sec_centroid >= 300) & (sec_centroid <= 3000)
        is_tonal = sec_flatness < np.percentile(flatness, 40)

        vocal_ratio = float(np.mean(in_vocal & is_tonal))
        if vocal_ratio > 0.4 and s["relative_energy"] > 0.2:
            return float(s["start_sample"]), min(1.0, vocal_ratio)

    return None, 0.0


def _detect_first_drop(sections, y, sr, rms, hop,
                       drop_energy_ratio=1.5, **_):
    """First section boundary with a large energy increase — chorus or drop.

    Looks for the biggest energy jump between consecutive sections,
    occurring in the first 60% of the track.
    """
    if len(sections) < 3:
        return None, 0.0

    track_duration = sections[-1]["end_seconds"]
    search_limit = track_duration * 0.6

    best_pos = None
    best_ratio = 0.0

    for i in range(1, len(sections)):
        if sections[i]["start_seconds"] > search_limit:
            break

        prev_energy = sections[i - 1]["mean_energy"] + 1e-10
        curr_energy = sections[i]["mean_energy"]
        ratio = curr_energy / prev_energy

        if ratio > best_ratio and sections[i]["relative_energy"] > 0.5:
            best_ratio = ratio
            best_pos = float(sections[i]["start_sample"])

    if best_pos is not None and best_ratio >= 1.1:
        confidence = min(1.0, best_ratio / drop_energy_ratio)
        return best_pos, confidence

    return None, 0.0


def _detect_breakdown(sections, y, sr, rms, hop,
                      drop_pos=None,
                      breakdown_min_duration_seconds=4.0, **_):
    """Energy dip after the drop, followed by recovery."""
    if drop_pos is None:
        return None, 0.0

    drop_idx = None
    for i, s in enumerate(sections):
        if s["start_sample"] >= drop_pos:
            drop_idx = i
            break
    if drop_idx is None:
        return None, 0.0

    drop_energy = sections[drop_idx]["mean_energy"]

    for i in range(drop_idx + 1, len(sections) - 1):
        s = sections[i]
        if (s["mean_energy"] < drop_energy * 0.6
                and s["duration_seconds"] >= breakdown_min_duration_seconds):
            # Check if there's an energy recovery after this section
            recovery = any(
                sections[j]["mean_energy"] > drop_energy * 0.7
                for j in range(i + 1, min(i + 3, len(sections)))
            )
            if recovery:
                dip = 1.0 - (s["mean_energy"] / (drop_energy + 1e-10))
                return float(s["start_sample"]), min(1.0, dip)

    # Fallback: look for any relative energy dip after the drop
    for i in range(drop_idx + 1, len(sections) - 1):
        s = sections[i]
        if s["mean_energy"] < drop_energy * 0.7:
            has_later_energy = any(
                sections[j]["relative_energy"] > 0.5
                for j in range(i + 1, len(sections))
            )
            if has_later_energy:
                dip = 1.0 - (s["mean_energy"] / (drop_energy + 1e-10))
                return float(s["start_sample"]), min(1.0, dip * 0.7)

    return None, 0.0


def _detect_second_drop(sections, y, sr, rms, hop,
                        breakdown_pos=None,
                        drop_energy_ratio=1.5, **_):
    """Energy spike after the breakdown."""
    if breakdown_pos is None:
        return None, 0.0

    breakdown_idx = None
    for i, s in enumerate(sections):
        if s["start_sample"] >= breakdown_pos:
            breakdown_idx = i
            break
    if breakdown_idx is None:
        return None, 0.0

    for i in range(breakdown_idx + 1, len(sections)):
        s = sections[i]
        prev_energy = sections[i - 1]["mean_energy"] + 1e-10
        ratio = s["mean_energy"] / prev_energy

        if ratio > 1.2 and s["relative_energy"] > 0.5:
            confidence = min(1.0, ratio / drop_energy_ratio)
            return float(s["start_sample"]), confidence

    return None, 0.0


def _detect_outro(sections, y, sr, rms, hop,
                  outro_energy_percentile=25, **_):
    """Last structural boundary before sustained energy decline."""
    if len(sections) < 3:
        return None, 0.0

    # Find the last section with high energy
    last_high = None
    for i in range(len(sections) - 1, -1, -1):
        if sections[i]["relative_energy"] > 0.4:
            last_high = i
            break

    if last_high is None or last_high >= len(sections) - 1:
        return None, 0.0

    # Outro starts at the section after the last high-energy section
    outro_section = sections[last_high + 1]

    # Only mark as outro if it's in the last 30% of the track
    track_duration = sections[-1]["end_seconds"]
    if outro_section["start_seconds"] < track_duration * 0.7:
        return None, 0.0

    return float(outro_section["start_sample"]), 0.7
