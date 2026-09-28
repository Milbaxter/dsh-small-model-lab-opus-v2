"""Harness arm registry.

An arm is a DSH profile name plus an ordered list of patch files. Built-in arms
live in harness/arms/. An *external* arm is any directory containing an
`arm.yaml` ({profile: sdk, patches: [a.patch.yml, ...]}, paths relative to that
directory) -- this is how another team's packaged DSH profile is evaluated on
our task bank (`--arms ext:/path/to/bundle`).

Every arm gets the shared model layer (lab-provider.patch.yml) applied last so
model/provider/sampling are identical across arms and cannot be overridden.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ARMS_DIR = ROOT / "harness" / "arms"
PROVIDER_PATCH = ARMS_DIR / "lab-provider.patch.yml"
STANDARD_BASE = [ARMS_DIR / "standard.patch.yml"]

BUILTIN = {
    "minimal": ("sdk-minimal", [ARMS_DIR / "minimal.patch.yml"]),
    "standard": ("sdk", STANDARD_BASE),
    "autonomy": ("sdk", STANDARD_BASE + [ARMS_DIR / "autonomy.patch.yml"]),
}


@dataclass
class Arm:
    name: str
    profile: str
    patches: list[Path]

    def all_patches(self) -> list[str]:
        return [str(p) for p in self.patches] + [str(PROVIDER_PATCH)]


def resolve(spec: str) -> Arm:
    """`standard`, `candidates/<name>` (harness/candidates/<name>/arm.yaml), or `ext:<dir>`."""
    if spec in BUILTIN:
        prof, patches = BUILTIN[spec]
        return Arm(spec, prof, list(patches))
    if spec.startswith("ext:"):
        d = Path(spec[4:]).expanduser().resolve()
        name = "ext-" + d.name
    else:
        d = ROOT / "harness" / spec
        name = spec.replace("/", "-")
    meta = yaml.safe_load((d / "arm.yaml").read_text())
    prof = meta.get("profile", "sdk")
    patches: list[Path] = []
    if meta.get("base", "standard") == "standard" and prof == "sdk":
        patches += STANDARD_BASE
    patches += [(d / p).resolve() for p in meta.get("patches", [])]
    return Arm(meta.get("name", name), prof, patches)
