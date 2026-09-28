"""Cue template loading and validation."""

import importlib.resources
from pathlib import Path

import yaml


VALID_DETECT_KEYS = {
    "mix_in", "first_vocal",
    "first_chorus", "second_chorus", "third_chorus",
    "outro_start",
}

VALID_COLORS = {
    "yellow", "orange", "purple", "red",
    "green", "teal", "cyan", "blue",
}


PHRASE_PREFIX = "phrase-"
DEFAULT_PHRASE_CUES = 6
PHRASE_PRESETS = (8, 16, 32)

_PHRASE_COLORS = ["yellow", "orange", "purple", "red",
                  "green", "teal", "cyan", "blue"]


def phrase_template(bars: int, count: int = DEFAULT_PHRASE_CUES,
                    intro_bars: int = 0) -> dict:
    """A template that places cue k at bar 1 + (k-1)*bars.

    "Phrase-match" cueing: cue 1 is the first bar and every later cue is
    one phrase further on, so any two cues line up when mixing.

    With intro_bars, the edit is assumed to open with an intro of that
    length before its phrase structure starts: cue 1 stays on bar 1, cue 2
    goes to the first bar after the intro, and phrases count from there.
    """
    if bars < 1 or bars > 128:
        raise ValueError("Phrase length must be 1–128 bars")
    if count < 1 or count > 8:
        raise ValueError("Cue count must be 1–8")
    if intro_bars < 0 or intro_bars > 256:
        raise ValueError("Intro length must be 0–256 bars")
    cues = {}
    for k in range(1, count + 1):
        if k == 1:
            bar = 1
        elif intro_bars:
            bar = 1 + intro_bars + (k - 2) * bars
        else:
            bar = 1 + (k - 1) * bars
        cues[k] = {"detect": f"bar_{bar}",
                   "label": "Intro" if k == 1 else f"Bar {bar}",
                   "color": _PHRASE_COLORS[k - 1]}
    name = f"Every {bars} bars"
    if intro_bars:
        name += f" after a {intro_bars}-bar intro"
    return {
        "name": name,
        "description": f"{count} cues, one every {bars} bars from cue 1",
        "phrase_bars": bars,
        "intro_bars": intro_bars,
        "cues": cues,
    }


def with_intro(template: dict, intro_bars: int) -> dict:
    """Apply an intro offset to any template.

    Phrase templates are rebuilt so cue 2 lands right after the intro.
    Other templates keep cue 1 on bar 1 and shift every later bar_N cue
    by intro_bars, so their structure starts once the intro is over.
    """
    if not intro_bars:
        return template
    if "phrase_bars" in template:
        return phrase_template(template["phrase_bars"], len(template["cues"]), intro_bars)
    out = dict(template)
    out["cues"] = {}
    for slot, cue in template["cues"].items():
        cue = dict(cue)
        d = cue["detect"]
        if int(slot) != 1 and d.startswith("bar_") and d[4:].isdigit():
            cue["detect"] = f"bar_{int(d[4:]) + intro_bars}"
        out["cues"][slot] = cue
    out["intro_bars"] = intro_bars
    return out


def parse_phrase_name(name: str):
    """'phrase-16' -> (16, default count); 'phrase-16-8' -> (16, 8); else None."""
    if not name.startswith(PHRASE_PREFIX):
        return None
    parts = name[len(PHRASE_PREFIX):].split("-")
    try:
        bars = int(parts[0])
        count = int(parts[1]) if len(parts) > 1 else DEFAULT_PHRASE_CUES
    except (ValueError, IndexError):
        raise ValueError(
            f"Bad phrase template '{name}'. Use phrase-<bars> or "
            f"phrase-<bars>-<cues>, e.g. phrase-16 or phrase-16-8")
    return bars, count


def load_template(name: str, user_dir: str | None = None) -> dict:
    """Load a cue template by name or file path.

    Search order:
      1. phrase-<bars>[-<cues>] builds a phrase-match template on the fly.
      2. If name is a path to an existing .yaml file, load it directly.
      3. user_dir/<name>.yaml (if user_dir provided)
      4. Bundled templates in this package
    """
    phrase = parse_phrase_name(name)
    if phrase is not None:
        return phrase_template(*phrase)

    path = Path(name)
    if path.suffix in ('.yaml', '.yml') and path.exists():
        return _load_and_validate(path.read_text(encoding='utf-8'), name)

    if user_dir:
        user_path = Path(user_dir) / f"{name}.yaml"
        if user_path.exists():
            return _load_and_validate(
                user_path.read_text(encoding='utf-8'), name
            )

    try:
        ref = importlib.resources.files(
            "autocue.templates"
        ).joinpath(f"{name}.yaml")
        text = ref.read_text(encoding='utf-8')
        return _load_and_validate(text, name)
    except (FileNotFoundError, TypeError):
        pass

    available = list_templates() + [f"phrase-<bars>[-<cues>]"]
    raise FileNotFoundError(
        f"Template '{name}' not found. "
        f"Available: {', '.join(available)}"
    )


def list_templates() -> list[str]:
    """List names of bundled templates."""
    templates_dir = importlib.resources.files("autocue.templates")
    names = []
    for item in templates_dir.iterdir():
        if hasattr(item, 'name') and item.name.endswith('.yaml'):
            names.append(item.name.removesuffix('.yaml'))
    return sorted(names)


def validate_template(template: dict) -> list[str]:
    """Validate template structure. Returns list of error messages."""
    errors = []

    if "cues" not in template:
        errors.append("Missing 'cues' section")
        return errors

    cues = template["cues"]
    if not isinstance(cues, dict):
        errors.append("'cues' must be a mapping of slot numbers to cue definitions")
        return errors

    for slot, cue_def in cues.items():
        slot_num = int(slot)
        if slot_num < 1 or slot_num > 8:
            errors.append(f"Cue slot {slot} out of range (must be 1–8)")

        if not isinstance(cue_def, dict):
            errors.append(f"Cue {slot}: definition must be a mapping")
            continue

        detect = cue_def.get("detect")
        is_bar_key = isinstance(detect, str) and detect.startswith("bar_") and detect[4:].isdigit()
        if detect not in VALID_DETECT_KEYS and not is_bar_key:
            errors.append(
                f"Cue {slot}: unknown detect key '{detect}'. "
                f"Valid: {', '.join(sorted(VALID_DETECT_KEYS))}, bar_N"
            )

        color = cue_def.get("color", "").lower()
        if color and color not in VALID_COLORS:
            errors.append(
                f"Cue {slot}: unknown color '{color}'. "
                f"Valid: {', '.join(sorted(VALID_COLORS))}"
            )

    return errors


def _load_and_validate(text: str, name: str) -> dict:
    template = yaml.safe_load(text)
    errors = validate_template(template)
    if errors:
        raise ValueError(
            f"Invalid template '{name}': {'; '.join(errors)}"
        )
    return template
