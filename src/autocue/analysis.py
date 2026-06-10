"""Structural audio analysis for cue point detection.

Uses librosa for feature extraction. This module has no dependency on
the Engine DJ codec or database — the CLI orchestrates between them.

Detection strategy:
  1. Find structural boundaries via chroma/MFCC self-similarity novelty
  2. Compute per-section energy and feature centroids
  3. Identify chorus sections via repetition (feature similarity)
  4. Map sections to cue types
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

    struct_hop = STRUCTURAL_HOP
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=struct_hop)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, hop_length=struct_hop)
    features = np.vstack([
        librosa.util.normalize(chroma, axis=1),
        librosa.util.normalize(mfcc, axis=1),
    ])

    boundaries = _find_structural_boundaries(features, y, sr, struct_hop,
                                             **params)
    sections = _build_sections(boundaries, rms, sr, hop)

    section_centroids = _compute_section_centroids(sections, features,
                                                   struct_hop)
    chorus_indices = _find_chorus_sections(sections, section_centroids,
                                           **params)

    positions = {}
    confidences = {}

    pos, conf = _detect_mix_in(sections, y, sr, rms, hop, **params)
    positions["mix_in"] = pos
    confidences["mix_in"] = conf

    pos, conf = _detect_first_vocal(sections, y, sr, hop, **params)
    positions["first_vocal"] = pos
    confidences["first_vocal"] = conf

    for i, key in enumerate(["first_chorus", "second_chorus", "third_chorus"]):
        if i < len(chorus_indices):
            idx = chorus_indices[i]
            s = sections[idx]
            positions[key] = float(s["start_sample"])
            energy_conf = min(1.0, s["relative_energy"] + 0.3)
            confidences[key] = energy_conf
        else:
            positions[key] = None
            confidences[key] = 0.0

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
    """Self-similarity via cosine distance + Gaussian kernel."""
    dist = cdist(features.T, features.T, metric='cosine')
    bandwidth = np.median(dist[dist > 0]) if np.any(dist > 0) else 1.0
    return np.exp(-dist / bandwidth)


def _find_structural_boundaries(features, y, sr, hop,
                                min_section_seconds=4.0, **_):
    rec = _affinity_matrix(features)

    kernel_size = min(64, max(8, rec.shape[0] // 8))
    novelty = _checkerboard_novelty(rec, kernel_size)

    min_distance = max(1, int(min_section_seconds * sr / hop))
    threshold = (np.percentile(novelty[novelty > 0], 60)
                 if np.any(novelty > 0) else 0)

    peaks, _ = find_peaks(novelty, distance=min_distance, height=threshold)

    boundary_samples = librosa.frames_to_samples(peaks, hop_length=hop)
    return [0.0] + [float(b) for b in boundary_samples] + [float(len(y))]


def _checkerboard_novelty(rec, kernel_size=64):
    n = rec.shape[0]
    half = kernel_size // 2
    if half < 1 or n < kernel_size:
        return np.zeros(n)

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
        })

    if sections:
        max_e = max(s["mean_energy"] for s in sections) or 1e-10
        for s in sections:
            s["relative_energy"] = s["mean_energy"] / max_e

    return sections


def _compute_section_centroids(sections, features, struct_hop):
    """Feature centroid per section for similarity comparison."""
    centroids = []
    for s in sections:
        start_frame = int(s["start_sample"] / struct_hop)
        end_frame = min(int(s["end_sample"] / struct_hop), features.shape[1])
        if end_frame > start_frame:
            centroids.append(np.mean(features[:, start_frame:end_frame],
                                     axis=1))
        else:
            centroids.append(np.zeros(features.shape[0]))
    return np.array(centroids)


def _find_chorus_sections(sections, centroids, drop_energy_ratio=1.5, **_):
    """Identify chorus sections by finding repeated high-energy sections.

    Strategy:
      1. Find the first high-energy section with an energy jump (first chorus)
      2. Find later sections with similar features, spaced across the track
    """
    if len(sections) < 3 or len(centroids) < 3:
        return []

    track_duration = sections[-1]["end_seconds"]
    min_chorus_spacing = max(20.0, track_duration / 8)

    # Find first chorus: biggest energy jump in first 50% of track
    first_chorus_idx = None
    best_jump = 0.0

    for i in range(1, len(sections)):
        if sections[i]["start_seconds"] > track_duration * 0.5:
            break
        if sections[i]["relative_energy"] < 0.4:
            continue

        prev_energy = sections[i - 1]["mean_energy"] + 1e-10
        jump = sections[i]["mean_energy"] / prev_energy

        if jump > best_jump:
            best_jump = jump
            first_chorus_idx = i

    if first_chorus_idx is None:
        return []

    chorus_centroid = centroids[first_chorus_idx]
    all_dists = np.linalg.norm(centroids - chorus_centroid, axis=1)
    median_dist = np.median(all_dists[all_dists > 0]) if np.any(all_dists > 0) else 1.0

    # Score every section by similarity to first chorus
    candidates = []
    for i in range(first_chorus_idx + 1, len(sections)):
        dist = all_dists[i]
        similarity = 1.0 - (dist / (median_dist * 2 + 1e-10))
        if similarity > 0.2 and sections[i]["relative_energy"] > 0.3:
            candidates.append((i, similarity))

    # Pick candidates spaced apart, preferring highest similarity
    candidates.sort(key=lambda x: -x[1])
    chorus_indices = [first_chorus_idx]
    last_time = sections[first_chorus_idx]["start_seconds"]

    for idx, sim in candidates:
        t = sections[idx]["start_seconds"]
        if all(abs(t - sections[ci]["start_seconds"]) >= min_chorus_spacing
               for ci in chorus_indices):
            chorus_indices.append(idx)

    chorus_indices.sort(key=lambda i: sections[i]["start_seconds"])
    return chorus_indices


# ---------------------------------------------------------------------------
# Cue detectors
# ---------------------------------------------------------------------------

def _detect_mix_in(sections, y, sr, rms, hop, **_):
    """First bar — start of the track's musical content."""
    if not sections:
        return None, 0.0

    # Find where energy first rises meaningfully
    threshold = np.percentile(rms, 15)
    above = np.where(rms > threshold)[0]

    if len(above) == 0:
        return 0.0, 0.5

    first_frame = above[0]
    position = librosa.frames_to_samples(int(first_frame), hop_length=hop)
    return float(position), 0.8


def _detect_first_vocal(sections, y, sr, hop, **_):
    """First sustained vocal region."""
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr,
                                                 hop_length=hop)[0]
    flatness = librosa.feature.spectral_flatness(y=y, hop_length=hop)[0]
    rms_arr = librosa.feature.rms(y=y, hop_length=hop)[0]

    in_vocal = (centroid >= 300) & (centroid <= 3000)
    is_tonal = flatness < np.percentile(flatness, 40)
    median_e = np.median(rms_arr)
    if median_e < 1e-10:
        return None, 0.0
    has_energy = rms_arr > median_e * 1.2

    vocal_frames = in_vocal & is_tonal & has_energy

    min_frames = int(1.5 * sr / hop)
    count = 0
    for i in range(len(vocal_frames)):
        if vocal_frames[i]:
            count += 1
            if count >= min_frames:
                start = i - min_frames + 1
                pos = librosa.frames_to_samples(start, hop_length=hop)
                return float(pos), 0.6
        else:
            count = 0

    return None, 0.0


def _detect_outro(sections, y, sr, rms, hop, **_):
    """Last structural boundary before the track fades."""
    if len(sections) < 3:
        return None, 0.0

    track_duration = sections[-1]["end_seconds"]

    # Find the last section that still has significant energy
    last_high = None
    for i in range(len(sections) - 1, -1, -1):
        if sections[i]["relative_energy"] > 0.4:
            last_high = i
            break

    if last_high is None or last_high >= len(sections) - 1:
        return None, 0.0

    outro = sections[last_high + 1]
    if outro["start_seconds"] < track_duration * 0.7:
        return None, 0.0

    return float(outro["start_sample"]), 0.7
