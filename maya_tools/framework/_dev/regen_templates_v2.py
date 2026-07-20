"""One-shot: regenerate the packaged template pairs as schema v2.
Run: PYTHONNOUSERSITE=1 mayapy maya_tools/framework/_dev/regen_templates_v2.py

Idempotent. Per the AMENDED v2 spec sec 6 (2026-07-18): engine_up_axis is
KEPT (live exporter truth), and the Unreal-family templates (_unreal5,
archangel) regenerate with engine_up_axis "z" so a fresh Unreal project
never recreates the pre-2026-07-14 swimming-skeleton behavior; the
_status marker on experimental templates is re-applied after
regeneration (template-dir metadata; user saves drop it by design)."""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from maya_tools.framework import project_config_io as pio
from maya_tools.framework import project_config_paths as pcp

UNREAL_FAMILY = ("_unreal5", "archangel")


def main():
    for d in sorted(pcp.packaged_templates_dir().iterdir()):
        if (not d.is_dir() or d.name.startswith("__")
                or not (d / "session.json").is_file()
                or not (d / "export_mapping.json").is_file()):
            continue
        export_path = d / "export_mapping.json"
        old_export = json.loads(export_path.read_text(encoding="utf-8"))
        status = old_export.get("_status", "")
        session = json.loads((d / "session.json").read_text(encoding="utf-8"))

        if old_export.get("schema_version", 1) < 2:
            authored = pio._upgrade_v1(session, old_export)
        else:
            authored = {f: session.get(f) for f in pio.SESSION_FIELDS}
            for key, default in pio._EXPORT_AUTHORED_KEYS:
                authored[key] = old_export.get(key, default)

        if d.name in UNREAL_FAMILY:
            authored["engine_up_axis"] = "z"
        elif authored.get("engine_up_axis") == "y":
            # Explicit "y" and "" (follow the exporter default, y) are
            # identical semantics; templates ship the spec's "" form.
            authored["engine_up_axis"] = ""

        new_session, new_export = pio.split_authored(authored)
        if status:
            new_export["_status"] = status
        pio.write_json_atomic(d / "session.json", new_session)
        pio.write_json_atomic(export_path, new_export)
        axis = authored.get("engine_up_axis", "")
        print(f"regenerated v2: {d.name}"
              + (f"  (engine_up_axis={axis})" if axis else "")
              + (f"  (_status={status})" if status else ""))


if __name__ == "__main__":
    main()
