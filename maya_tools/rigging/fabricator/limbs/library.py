"""Limb library scan. Returns LimbEntry list from the studio templates
folder (maya_tools/rigging/fabricator/templates/).

V1: hardcoded to the studio folder shared with blueprint templates.
V2 will add a per-project limb path resolved from project_config.
"""
__author__ = "Adrian Melian"

from dataclasses import dataclass
from pathlib import Path


_STUDIO_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / 'templates'


@dataclass
class LimbEntry:
    name: str          # file stem without '.limb' suffix, e.g. 'face_basic'
    path: Path         # absolute path to the .limb.yaml file


def scan(folder: Path | None = None) -> list[LimbEntry]:
    """Walk <folder>/*.limb.yaml. Returns sorted LimbEntry list.

    folder defaults to the studio templates folder. Pass an explicit
    path once V2 introduces per-project limb destinations.
    """
    folder = Path(folder) if folder else _STUDIO_TEMPLATES_DIR
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.glob('*.limb.yaml')):
        out.append(LimbEntry(
            name=p.name.removesuffix('.limb.yaml'),
            path=p,
        ))
    return out
