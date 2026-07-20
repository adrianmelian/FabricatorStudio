# Fabricator — Component Authoring Guide

Practical guide for writing a new Fabricator component. Targeted at devs already comfortable with Maya / Python rigging — no Maya 101.

If you only read one section, read [Joint chains and the IK twist trap](#joint-chains-and-the-ik-twist-trap) — that's where every IK component will hit subtle bugs.

---

## 1. Anatomy

A component is a Python class in `maya_tools/rigging/fabricator/modules/<name>.py` with a class-attribute `CONTRACT`. Auto-discovery walks `modules/` via pkgutil and registers any `Component` subclass with `CONTRACT` set. Drop the file, it shows up in the palette.

```python
from maya_tools.rigging.fabricator.modules.component import (
    Component, Contract, Plug, OptionField, JointRole,
)

CONTRACT = Contract(
    type='MyComponent',
    display_name='My Component',
    description='What it does, briefly.',
    min_joints=1, max_joints=1,
    parent_strategy='walk_up',
    inputs=(Plug(name='parent_in', kind='matrix', required=True),),
    outputs=(Plug(name='ctrl_out', kind='matrix', space_target=True),),
    options_schema={
        'ctrl_shape': OptionField(type='shape_enum', default='circle'),
        'ctrl_color': OptionField(type='color_enum', default='yellow'),
    },
    side_supported=True,
    color='#7AC4FF',
    joint_roles=(JointRole('main', 'The single joint'),),
)


class MyComponent(Component):
    CONTRACT = CONTRACT

    @classmethod
    def build(cls, instance, context) -> None:
        ...

    @classmethod
    def unbuild(cls, instance) -> dict:
        ...
        return {}  # state to persist on the component network node
```

That's it for discovery. The orchestrator (`fs_app.build_modules`) handles the rest — topo-sort, network node creation, build dispatch, scene organisation.

## 2. The build context

`context` is a `BuildContext`. Two things you'll use constantly:

- `context.resolve_plug(instance.parent_plug)` — returns the live Maya plug for the parent component's output. The parent ctrl is `parent_matrix_plug.split('.')[0]`. Falls back to `controls_grp` if the parent ctrl doesn't exist (defensive — validator should catch).
- `context.register_output(instance.id, plug_name, maya_plug)` — call this for every output declared in your contract. Downstream components calling `resolve_plug('<your_id>.<plug>')` read this. Register BEFORE returning from build.

Topo-sort by `parent_plug` edges is automatic — your component is always built after its parents.

## 3. Containers + ctrl tagging

Every component must:

- Get its containers via `nodes.ensure_controls_grp()` and `nodes.ensure_nulls_grp()` — both have `inheritsTransform=False`, so anything parented under them lives in world space (no double-scale on global rig scale).
- Tag every ctrl with `fab_role` (string) and `fab_owner` (message connection to the component network node). Without these, the marking menu can't find your ctrls and animation actions silently fail. Use the inline `_tag_ctrl` helper pattern from `simple_ik.py:331-341` — copy/paste it; it's the canonical 11 lines.

```python
def _tag_ctrl(ctrl_name: str, role: str):
    cmds.addAttr(ctrl_name, ln='fab_role', dt='string')
    cmds.setAttr(f'{ctrl_name}.fab_role', role, type='string')
    if component_node:
        if not cmds.attributeQuery('fab_owner', node=ctrl_name, exists=True):
            cmds.addAttr(ctrl_name, ln='fab_owner', at='message')
        cmds.connectAttr(f'{component_node}.message',
                         f'{ctrl_name}.fab_owner', force=True)
```

Resolve `component_node` early in `build()` by walking `nodes.get_all_component_nodes()` and matching `nodes.get_component_id(cn) == instance.id`. You'll also need it for tracking owned DG nodes (see §6).

## 4. Joint chains and the IK twist trap

This is the section that bites people. If your component duplicates joints (FK chain, IK chain, BLEND chain), follow this recipe exactly. See `_create_chain_duplicate` in `simple_ik.py` for the canonical implementation.

**Recipe:**

```python
new_jnt = cmds.duplicate(bind, parentOnly=True, name=new_name)[0]

# (A) Disconnect message connections — see Gotcha 1.
for dest in cmds.listConnections(f'{new_jnt}.message',
                                 source=False, destination=True,
                                 plugs=True) or []:
    cmds.disconnectAttr(f'{new_jnt}.message', dest)

cmds.parent(new_jnt, parent_jnt)

# (B) matchTransform after parent — see Gotcha 2.
cmds.matchTransform(new_jnt, bind,
                    position=True, rotation=True, scale=True)

# (C) Capture preferredAngle from the local bend BEFORE makeIdentity
#     zeros it — see Gotcha 4.
cmds.joint(new_jnt, edit=True, setPreferredAngles=True)

# (D) makeIdentity to clean rest pose — see Gotcha 3.
cmds.makeIdentity(new_jnt, apply=True,
                  translate=False, rotate=True, scale=False, normal=0)
```

**Gotcha 1 — `cmds.duplicate` propagates outgoing message connections.** When you duplicate a joint whose `.message` is connected into a multi-attr (e.g. `fab_<id>.joints[N]`), the duplicate auto-extends that multi-attr. `get_component_joints()` then returns 12 entries instead of 3, and any caller relying on the joint count silently early-returns. *Always* disconnect.

**Gotcha 2 — `cmds.parent` on joints rebases parent rotation into jointOrient with FP noise.** Mid-chain joints can drift by visible amounts. `matchTransform` after the reparent is matrix-based and rotateOrder-agnostic — it nails the world transform exactly. The IK chain hides this drift (the IK solver realigns it); the FK chain has no solver, so the drift gets locked in by `parentConstraint mo=True`.

**Gotcha 3 — `rotate ≠ 0` at bind makes the IK solver compute twisted chains on aimed joints.** UE5 convention puts rotation in `rotate` and keeps jointOrient=0. When the IK solver runs, it picks a `rotate` value that achieves the same world *position* but a different world *rotation* — twist mismatch around the bone axis. `makeIdentity(rotate=True)` bakes rotation into jointOrient so the chain has `rotate=0` at rest. The IK solver then leaves the chain undisturbed. This is what the legacy v1 `make_jnts` did — v2 originally skipped it and inherited the bug.

**Gotcha 4 — `setPreferredAngles=True` MUST run BEFORE `makeIdentity`.** `setPreferredAngles=True` reads the joint's *current* `rotate` and bakes it into `preferredAngle`. After `makeIdentity` zeros `rotate`, that read returns 0 and you wipe the bend hint to nothing — the IK solver then has no idea which way to flex and longer chains (legs in particular) lock dead straight. Capture preferredAngle at step (C) above, when `rotate` still carries the local bend angle. The legacy v1 `make_jnts` order had it baked into jointOrient AND the chain pose still had non-zero rotate at that point — easy to miss when porting.

## 5. IK/FK blend constraints

The blend pattern (parallel FK + IK + BLEND chains, weighted parentConstraint on BLEND) is what SimpleIK uses. If your component has IK/FK switching, follow it. Two constraint-level gotchas:

**Use `interpType=2` (Shortest / quaternion blend) on every BLEND parentConstraint at creation.** Default `interpType=1` (Average) Euler-blends weight-wise — produces wrap-around twist mid-transition when IK and FK chains have different Euler representations of the same world rotation (unavoidable on aimed joints). Set it immediately after `parentConstraint(...)`:

```python
pcon = cmds.parentConstraint(ik_jnt, fk_jnt, blend_jnt, mo=False)[0]
cmds.setAttr(f'{pcon}.interpType', 2)
```

**`parentConstraint(blend, bind, mo=True)` captures the blend chain's current pose at creation.** If the IK chain doesn't reproduce bind exactly at constraint time, the offset is non-identity. In IK mode the offset cancels; in FK mode it doesn't, and pose drifts. The recipe in §4 (matchTransform + makeIdentity) ensures IK reproduces bind, so mo=True banks identity offset. If you skip the recipe, this constraint will silently break FK mode.

## 6. Network node + tracking owned DG nodes

Component state lives on the component network node (`fab_<id>`). The orchestrator creates it; you don't. What you DO own:

- **Owned DG nodes** — anything you create that needs cleanup on unbuild. Constraint nodes, condition nodes, multiplyDivide nodes, IK handles, sub-joints, pivot transforms — track them via `nn.connect_message_multi(node, component_node, '<bucket_name>')`. SimpleIK uses `bind_blend_nodes` and `space_switch_nodes`; IKLeg adds `reverse_foot_nodes`. Pick descriptive bucket names.
- **Captured state** — return a dict from `unbuild()` that the orchestrator persists into `persisted_json`. The next build reads `instance.persisted` and restores. Use this for CV data, scalar attrs animator tweaked, anything you don't want to lose on rebuild.

`unbuild()` deletes the tracked DG nodes:

```python
component_node = ...  # resolve as in §3
for n in nn.get_message_targets(component_node, 'bind_blend_nodes'):
    if cmds.objExists(n):
        cmds.delete(n)
```

The orchestrator deletes `rig_grp` (cascade) at the end of unbuild_modules, which catches every transform parented under it. Joints and constraints that live OUTSIDE rig_grp (constraints on the bind chain itself, sub-joints under the IK chain root) won't cascade — track them, delete them yourself.

### Deformer-driven components (curve/surface skin)

Components that skin a curve or surface (SplineFK; Ribbon) hit two traps that joint-only components never see.

**A — read the DEFORMED shape, not the `Orig`.** A skinCluster (any deformer) splits the geometry into the live deformed shape plus a static intermediate `…ShapeOrig`. `cmds.listRelatives(geo, shapes=True, type='nurbsCurve')[0]` may hand back the Orig, whose `worldSpace` never moves — so a `pointOnCurveInfo` / `follicle` reading it sees the rest shape and the riding joints never follow the deformation (the symptom: "joints don't follow the curve at all"). Always filter to the non-intermediate shape:

```python
shapes = cmds.listRelatives(geo, shapes=True, type='nurbsCurve', fullPath=True) or []
live = [s for s in shapes if not cmds.getAttr(f'{s}.intermediateObject')]
shape = (live or shapes)[0]
```

**B — unbuild the FULL skin node-set, not just the cluster.** `cmds.skinCluster` spawns `tweak` + `groupParts` + `groupId` + an `objectSet` + a `bindPose` alongside the cluster. The cluster is a DG node with no DAG parent, so the `rig_grp` cascade misses it AND its ancillaries — track them all or they orphan every build/unbuild cycle. Delete the `bindPose` at build (CLAUDE.md convention 6 forbids bindPose/dagPose nodes):

```python
nn.connect_message_multi(skin, component_node, '<skin_bucket>')
for n in cmds.listHistory(skin, pruneDagObjects=True) or []:   # pruneDagObjects keeps joints out
    if cmds.nodeType(n) in ('tweak', 'groupParts', 'groupId'):
        nn.connect_message_multi(n, component_node, '<skin_bucket>')
for s in cmds.listConnections(skin, type='objectSet') or []:
    nn.connect_message_multi(s, component_node, '<skin_bucket>')
for bp in cmds.listConnections(skin, type='dagPose') or []:
    cmds.delete(bp)   # no bindPose nodes
```

## 7. Action handlers (marking menu integration)

Declare actions in the contract:

```python
actions=(
    Action('Match to FK', 'master_switch_ctrl',
           'maya_tools.rigging.fabricator.actions.simple_ik.switch_to_fk_match', section='Mode'),
),
```

Implement handlers in `maya_tools/rigging/fabricator/actions/<your_module>.py` with signature `(component_id: str, ctrl: str, **kwargs) -> None`. The marking menu's `_dispatch` imports the module and calls the function — anything you raise will be caught and logged with traceback.

**For matching ctrls to bind pose, always use `cmds.matchTransform`, never raw rotate-copy:**

```python
cmds.matchTransform(fk_ctrl, bind_jnt, position=False, rotation=True, scale=False)
```

Reading `bind.rotate` and writing to a nested FK ctrl double-applies the bone orientation when the ctrl's parent (`FK_ctrl_offset`) already carries bind world rotation. matchTransform is matrix-based and parent-aware. Apply parent-first when matching a chain, so each match sees its parent's just-applied rotation.

## 8. Resolving ctrls in actions

Walk the scene by `fab_owner` connection back to the component network node:

```python
def _resolve_my_component_ctrls(component_id: str) -> dict:
    component_node = next(
        (cn for cn in nodes.get_all_component_nodes()
         if nodes.get_component_id(cn) == component_id),
        None,
    )
    if not component_node:
        return {}
    out = {}
    for ctrl in cmds.ls('*', type='transform') or []:
        if not cmds.attributeQuery('fab_owner', node=ctrl, exists=True):
            continue
        owner = cmds.listConnections(f'{ctrl}.fab_owner', s=True, d=False) or []
        if component_node not in owner:
            continue
        role = cmds.getAttr(f'{ctrl}.fab_role')
        out[role] = ctrl  # or out.setdefault(role, []).append(ctrl) for multi
    return out
```

Diagnostic if a handler "does nothing": call `_resolve_*_ctrls(component_id)` directly in the script editor and inspect the dict. Missing keys mean tagging issues at build time.

## 9. Reload during development

The marking menu uses `importlib.import_module` to load action handlers, which caches modules. If you edit `actions/<your_module>.py`, you must `importlib.reload` it explicitly — adding it to your reload script. Editing without reloading silently runs stale code.

## 10. Common gotchas — one-line checklist

Before shipping a new component, verify each of these:

- [ ] `CONTRACT` is a class attribute (not set in `__init__`).
- [ ] Every ctrl is `_tag_ctrl`'d with role + owner connection.
- [ ] All joint duplicates use the §4 recipe: duplicate → disconnect message → parent → matchTransform → setPreferredAngles → makeIdentity. (`setPreferredAngles` MUST come before `makeIdentity`.)
- [ ] BLEND parentConstraints set `interpType=2`.
- [ ] All output plugs are `context.register_output(...)`'d during build.
- [ ] Owned DG nodes are message-tracked on the component node; `unbuild()` deletes them.
- [ ] Actions in the contract use the dotted handler path; handlers take `(component_id, ctrl, **kwargs)`.
- [ ] Match handlers use `cmds.matchTransform`, never `cmds.setAttr(ctrl.rotate, bind.rotate)`.
- [ ] Captured persisted state is round-trippable through unbuild → build.
- [ ] Deformer-driven: `pointOnCurveInfo` / follicle reads the DEFORMED shape (filter `intermediateObject`), not the `Orig`.
- [ ] Deformer-driven: unbuild deletes the full skin node-set (cluster + `tweak`/`groupParts`/`groupId`/`objectSet`); the `bindPose` is killed at build.

---

## Reference implementations

- `simple_fk.py` — minimal single-joint component. Read first.
- `world.py` — root component, peer of SimpleFK.
- `simple_ik.py` — three-chain IK/FK with PV. The IK pattern reference.
- `ik_leg.py` — extends SimpleIK with reverse foot. Pattern for component subclassing + adding pivots/sub-IKs.
- `actions/simple_ik.py` — match action handlers using matchTransform.

When in doubt, copy the closest reference and adapt. The patterns are load-bearing — straying from them is how the bugs documented above got introduced.
