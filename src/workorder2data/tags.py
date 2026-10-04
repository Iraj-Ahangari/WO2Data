"""Equipment tags: find a tag in text and map its prefix to an equipment class (optional per-plant file)."""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_TAG_PATTERN = r"\b([A-Z]{1,4})-?(\d{2,5}[A-Z]?)\b"


@dataclass
class TagMap:
    pattern: re.Pattern = field(default_factory=lambda: re.compile(DEFAULT_TAG_PATTERN))
    prefixes: dict[str, str] = field(default_factory=dict)  # tag prefix -> class_id

    def find(self, text: str) -> tuple[str, str] | None:
        """First (tag, prefix) whose prefix is mapped, else the first tag-like token, else None."""
        first = None
        for m in self.pattern.finditer(text):
            tag, prefix = m.group(0), m.group(1)
            if prefix in self.prefixes:
                return tag, prefix
            first = first or (tag, prefix)
        return first

    def class_for(self, prefix: str | None) -> str | None:
        return self.prefixes.get(prefix) if prefix else None


def load_tag_map(path: Path | None, valid_class_ids: set[str] | None = None) -> TagMap:
    """YAML: optional `tag_pattern` (regex, group 1 = prefix) and `prefixes: {P: <class_id>, ...}`."""
    if path is None:
        return TagMap()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    prefixes = {str(k): str(v) for k, v in (data.get("prefixes") or {}).items()}
    if valid_class_ids is not None:
        bad = {k: v for k, v in prefixes.items() if v not in valid_class_ids}
        if bad:
            raise ValueError(f"tag map {path} names unknown equipment class ids: {bad}")
    pattern = re.compile(data["tag_pattern"]) if data.get("tag_pattern") else re.compile(DEFAULT_TAG_PATTERN)
    return TagMap(pattern=pattern, prefixes=prefixes)
