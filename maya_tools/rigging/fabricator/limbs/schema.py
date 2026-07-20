# maya_tools/rigging/fabricator/limbs/schema.py
"""Limb fragment schema — in-memory representation of a .limb.yaml.

A LimbFragment is structurally similar to a Blueprint but constrained:
  - No World component (the host rig provides world)
  - skeleton.joints' root has parent='<EXTERNAL>' (loader replaces)
  - Root component's parent_plug is '<EXTERNAL>.<plug_name>' (loader replaces)
  - JointSpec.world_translate is unused — limbs use local transforms only

Reuses JointSpec / ComponentSpec from blueprint.schema.
"""
__author__ = "Adrian Melian"

from dataclasses import dataclass, field

from maya_tools.rigging.fabricator.blueprint.schema import (
    JointSpec, ComponentSpec,
)


CURRENT_SCHEMA_VERSION = '1.0'
EXTERNAL_PLACEHOLDER = '<EXTERNAL>'


@dataclass
class ExternalAnchor:
    """Descriptor of what the limb expects at the drop target.

    plug_kind: 'matrix' or 'message' — what the host component's output
               plug must produce. Used at load to validate the host plug
               kind matches before rewriting.
    """
    plug_kind: str = 'matrix'


@dataclass
class LimbFragment:
    """Top-level limb fragment. Mirrors the YAML schema.

    name: human-readable label.
    description: free-form, optional.
    external_anchor: ExternalAnchor — what kind of host plug the limb attaches to.
    skeleton_joints: list[JointSpec] — subtree, root has parent='<EXTERNAL>'.
    components: list[ComponentSpec] — root has parent_plug='<EXTERNAL>.<plug>'.
             `[]` is a fully valid, intentional value — a SKELETON-ONLY
             fragment (PLAN.md 2026-07-08 Task 5.2 / SPEC 3.7): pure
             joints, no component nodes at all. This was always the
             dataclass default (a plain `list`, no minimum-length
             constraint here or anywhere in limbs/io.py's read_yaml), so
             no field-level relaxation was needed on THIS class; the
             actual gap was in limbs/builder.py's apply_limb_fragment
             (its host-plug resolution assumed a joints[0]-anchored,
             non-empty-components drop — see that function's docstring)
             and in fs_app._snapshot_limb_fragment_from_scene (the
             "Save Limb from a live scene" convenience path, which
             still requires >=1 owning component — untouched by this
             phase; a skeleton-only fragment is authored directly via
             LimbFragment(...) + limbs.io.write_yaml, not saved from a
             built rig). apply_limb_fragment's own
             `for c in fragment.components:` component-creation loop is
             already a no-op on `[]` with no code change needed.
    (Derived Limbs, spec 2026-07-11: the old `limb:` block / LimbBlock
    is retired — limb identity + membership re-derive on every drop;
    the reader ignores a legacy limb: key.)
    """
    name: str
    schema_version: str = CURRENT_SCHEMA_VERSION
    description: str = ''
    external_anchor: ExternalAnchor = field(default_factory=ExternalAnchor)
    skeleton_joints: list = field(default_factory=list)
    components: list = field(default_factory=list)
    # The FS build (version.py FABRICATOR_VERSION) that WROTE this file —
    # fragment parity with Blueprint.fabricator_version (2026-07-20).
    # '' = pre-stamping file: apply_limb_fragment treats its data as
    # pre-versioning-era, so the version-gated legacy TYPE map applies
    # ('IKArm' -> 'RibbonIKArm'). The LIVE scene's registry stamp can't
    # date a dropped file (the scene is current; the file may not be),
    # which is why fragments carry their own. Programmatic constructions
    # that carry current-era types should set this to FABRICATOR_VERSION;
    # the default '' only bites the one gated type string.
    fabricator_version: str = ''
    # armature_ctrls: {joint_name: {'translate': [x,y,z], 'rotate':
    # [x,y,z]}} — the Armature ctrls' LOCAL transforms at save time
    # (2026-07-10, Adrian: "the armature now controls the joints
    # position... save the Armature controls' local transforms, and
    # restore those"). The joint TRS attrs snapshot in skeleton_joints
    # is DRIVEN state — build_armature()'s re-derivation passes (aimer
    # bake, aim-axis normalization, follow evaluation) recompute it on
    # load, discarding authored ctrl placement. This block is the
    # AUTHORED state: apply_limb_fragment restores it onto the fresh
    # ctrls root-first AFTER build_armature(), so the ctrl poses win
    # over every derived pass exactly as they do in a live edit
    # session. `{}` is the legacy shape (no `armature_ctrls:` key in
    # the YAML) — load behaves exactly as before this field existed.
    armature_ctrls: dict = field(default_factory=dict)
