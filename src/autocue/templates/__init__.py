"""Cue template loading and validation."""

import importlib.resources
from pathlib import Path

import yaml


VALID_DETECT_KEYS = {
    "mix_in", "first_vocal", "first_drop",
    "breakdown", "second_drop", "outro_start",
}

VALID_COLORS = {
    "yellow", "orange", "purple", "red",
    "green", "teal", "cyan", "blue",
}


def load_template(name: str, user_dir: str | None = None) -> dict:
    """Load a cue template by name or file path.

    Search order:
      1. If name is a path to an existing .yaml file, load it directly.
      2. user_dir/<name>.yaml (if user_dir provided)
      3. Bundled templates in this package
    """
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

    available = list_templates()
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
        if detect not in VALID_DETECT_KEYS:
            errors.append(
                f"Cue {slot}: unknown detect key '{detect}'. "
                f"Valid: {', '.join(sorted(VALID_DETECT_KEYS))}"
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
