# Python/maya_tools/rigging/fabricator/ui/properties_panel.py
"""PropertiesPanel — right of canvas, context-sensitive joint/component editor.

Spec Section 3 'Right: Properties'. Renders Joint section + Component
section stacked when both selected. Wraps the entire form in a QScrollArea
so it survives small windows.

Scene-is-truth: panel reads directly from the Maya scene + network nodes
on every selection. No in-memory blueprint reference. Auto-save writes
directly to joint attrs / guide attrs / component network nodes.

Auto-save chain:
1. _loading suppression — skip during populate.
2. No-op skip — new value == current scene value, return.
3. Direct scene write via cmds.setAttr / nodes.set_component_*.
"""
__author__ = "Adrian Melian"

import traceback

from PySide6 import QtWidgets, QtCore

import maya.cmds as cmds

from maya_tools.rigging.fabricator import nodes
from maya_tools.rigging.fabricator import limb_node as ln
from maya_tools.rigging.fabricator.modules import _limb_common as hc
from maya_tools.rigging.fabricator.ui.option_widgets import (
    widget_for_option_field,
)
from maya_tools.rigging.fabricator.modules import get_component_class
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import CollapsibleSection


_ROTATE_ORDERS = ('xyz', 'yzx', 'zxy', 'xzy', 'yxz', 'zyx')


class PropertiesPanel(QtWidgets.QWidget):
    """Right-of-canvas panel showing joint and/or component properties.

    Scene-backed — knows the current joint name + component id and reads
    everything else on demand. No external state is passed in.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = True
        self._current_joint_name = ''
        self._current_component_id = ''

        self.create_layout()
        self._loading = False

    def create_layout(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        outer.addWidget(mindmeld_style.caps_label('// PROPERTIES'))

        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        outer.addWidget(self._scroll, stretch=1)

        body = QtWidgets.QWidget()
        self._scroll.setWidget(body)
        self._body_layout = QtWidgets.QVBoxLayout(body)
        self._body_layout.setContentsMargins(2, 2, 2, 2)
        self._body_layout.setSpacing(8)
        self._body_layout.addStretch(1)  # push content up; cleared on rebuild

        # Empty state (Adrian, 2026-07-05 brain dump item 9): with
        # nothing selected the panel dresses like the palette/canvas
        # list room instead of bare carbon — the attribute-editor
        # pattern. _rebuild swaps it with the scroll area.
        self._empty_state = QtWidgets.QFrame()
        self._empty_state.setProperty('mindmeld', 'empty-state')
        empty_lay = QtWidgets.QVBoxLayout(self._empty_state)
        empty_msg = QtWidgets.QLabel(
            'Select a rig component\nto view and edit its properties.')
        empty_msg.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        empty_msg.setWordWrap(True)
        empty_lay.addWidget(empty_msg)
        outer.addWidget(self._empty_state, stretch=1)
        self._scroll.setVisible(False)

        self._joint_section = None
        self._limb_section = None
        self._rule = None
        self._component_section = None
        self._options_section = None
        self._spaces_section = None

    # ─── Public API (host calls these) ──────────────────────────────────────

    def show_for_joint(self, joint_name: str):
        self._current_joint_name = joint_name or ''
        self._current_component_id = ''
        self._rebuild()

    def show_for_component(self, component_id: str):
        self._current_joint_name = ''
        self._current_component_id = component_id or ''
        self._rebuild()

    def show_for_both(self, joint_name: str, component_id: str):
        self._current_joint_name = joint_name or ''
        self._current_component_id = component_id or ''
        self._rebuild()

    def clear(self):
        self._current_joint_name = ''
        self._current_component_id = ''
        self._rebuild()

    # ─── Error handling ─────────────────────────────────────────────────────

    def _handle_error(self, e: Exception, brief: str = '') -> None:
        """Project-standard error reporting (CLAUDE.md 'UI error reporting'
        pillar) — full trace to the Script Editor, brief message surfaced
        via cmds.warning. Never swallow silently.

        Mirrors CanvasPanel._handle_error: PropertiesPanel is constructed
        bare by FSWindow (no owned status label, no logger reference), so
        cmds.warning is the panel's status/log surface, same as its sibling
        splitter panel."""
        traceback.print_exc()
        msg = brief or str(e)
        cmds.warning(f'[PropertiesPanel] {msg}')

    # ─── Rebuild logic ──────────────────────────────────────────────────────

    def _framed(self, title: str, inner: QtWidgets.QWidget
                ) -> CollapsibleSection:
        """Wrap a section body in a compact CollapsibleSection frame
        (Adrian, 2026-07-10 launch polish: every Properties section sits
        in its own thin collapsible frame). Expanded by default — the
        frames exist to organize and reclaim space on demand, never to
        hide options from a fresh selection."""
        frame = CollapsibleSection(title, compact=True)
        body = QtWidgets.QVBoxLayout()
        body.setContentsMargins(8, 6, 8, 6)
        body.setSpacing(6)
        body.addWidget(inner)
        frame.set_body_layout(body)
        frame.set_expanded(True)
        return frame

    def _rebuild(self):
        from maya_tools.rigging.fabricator.ui import state as ks_state
        is_anim_mode = ks_state.detect_mode() == ks_state.MODE_MODULES_BUILT
        self._loading = True

        def _insert(w):
            self._body_layout.insertWidget(self._body_layout.count() - 1, w)

        try:
            self._clear_body()
            # Animation Mode hides the Joint/Follow/Limb sections + all
            # Component identity rows + Spaces — animators only care
            # about Options. Edit Mode shows everything, one compact
            # collapsible frame per section.
            if self._current_joint_name and not is_anim_mode:
                jw = self._build_joint_section(self._current_joint_name)
                if jw is not None:
                    self._joint_section = self._framed('// JOINT', jw)
                    _insert(self._joint_section)
                # (Follow block retired 2026-07-12, Adrian: follow rules
                # are authored ONLY through the FollowJoint component
                # and the Twist dial now — no raw per-joint editor.)
                # LIMB dials (SPEC 2026-07-09 Limbs + Follower Joints
                # §3.3, Task 3.1): rendered ABOVE the component's own
                # properties whenever the selected joint belongs to a
                # limb (limb_node.find_limb_for_joint — not just its
                # exact top_joint). Edit-Mode-only, same gate as the
                # JOINT section itself (SPEC 5: "no live post-build
                # membership/twist changes").
                lw = self._build_limb_section(self._current_joint_name)
                if lw is not None:
                    self._limb_section = self._framed('// LIMB', lw)
                    _insert(self._limb_section)
            if self._current_component_id:
                if is_anim_mode:
                    ow = self._build_options_form_widget(
                        self._current_component_id)
                    if ow is not None:
                        self._component_section = self._framed(
                            '// CONTROLLER', ow)
                        _insert(self._component_section)
                else:
                    cw = self._build_component_section(
                        self._current_component_id)
                    if cw is not None:
                        self._component_section = self._framed(
                            '// COMPONENT', cw)
                        _insert(self._component_section)
                    ow = self._build_options_form_widget(
                        self._current_component_id)
                    if ow is not None:
                        self._options_section = self._framed(
                            '// OPTIONS', ow)
                        _insert(self._options_section)
                    sw = self._build_spaces_section(
                        self._current_component_id)
                    if sw is not None:
                        self._spaces_section = self._framed(
                            '// SPACES', sw)
                        _insert(self._spaces_section)
            # Empty state ↔ content swap (item 9).
            has_selection = bool(self._current_joint_name
                                 or self._current_component_id)
            self._scroll.setVisible(has_selection)
            self._empty_state.setVisible(not has_selection)
        finally:
            self._loading = False

    def _clear_body(self):
        # Remove every widget except the trailing stretch.
        # Layout last item is the stretch (addStretch(1) at construction).
        while self._body_layout.count() > 1:
            item = self._body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._joint_section = None
        self._limb_section = None
        self._rule = None
        self._component_section = None
        self._options_section = None
        self._spaces_section = None

    # ─── Joint section ──────────────────────────────────────────────────────

    def _build_joint_section(self, joint_name: str) -> QtWidgets.QWidget:
        """Joint properties: name (RO), rotate_order, radius.

        Resolves source — joint if it exists, else guide. Returns None if
        neither (selection points at a stale name).
        """
        from maya_tools.rigging.fabricator.ui.option_widgets import (
            _NoWheelDoubleSpinBox, _NoWheelComboBox,
        )
        source = self._resolve_joint_source(joint_name)
        if source is None:
            return None
        kind, source_node = source  # ('joint', name) | ('guide', guide_name)

        section = QtWidgets.QWidget()
        layout = QtWidgets.QFormLayout(section)
        layout.setLabelAlignment(QtCore.Qt.AlignRight)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # (Section headers live on the CollapsibleSection frames _rebuild
        # wraps each builder in — 2026-07-10 launch polish.)

        # name (read-only — rename via canvas inline / F2 / outliner)
        name_le = QtWidgets.QLineEdit(joint_name)
        name_le.setReadOnly(True)
        layout.addRow(mindmeld_style.field_label('name'), name_le)

        # rotate_order
        ro_combo = _NoWheelComboBox()
        ro_combo.addItems(_ROTATE_ORDERS)
        current_ro = self._read_rotate_order(kind, source_node)
        ro_combo.setCurrentIndex(_ROTATE_ORDERS.index(
            current_ro if current_ro in _ROTATE_ORDERS else 'xyz'))
        ro_combo.currentIndexChanged.connect(
            lambda _i, c=ro_combo:
            self._write_rotate_order(c.currentText()))
        layout.addRow(mindmeld_style.field_label('rotate_order'), ro_combo)

        # radius
        radius_sb = _NoWheelDoubleSpinBox()
        radius_sb.setDecimals(3)
        radius_sb.setRange(0.0, 1000.0)
        radius_sb.setValue(float(self._read_radius(kind, source_node)))
        radius_sb.valueChanged.connect(
            lambda v: self._write_radius(float(v)))
        layout.addRow(mindmeld_style.field_label('radius'), radius_sb)

        return section

    # ─── LIMB section (SPEC 2026-07-09 Limbs + Follower Joints §3.3,
    # Task 3.1) ────────────────────────────────────────────────────────────

    def _build_limb_section(self, joint_name: str) -> QtWidgets.QWidget | None:
        """LIMB block: derived identity (read-only) + the Twist dial.
        Returns None (no section rendered) when `joint_name` isn't part
        of any `fab_limb` — `limb_node.find_limb_for_joint` walks UP
        from the selected joint, so this renders for ANY joint under a
        limb, not only its exact top_joint.

        Derived Limbs (spec 2026-07-11): limb identity and finger
        membership are DERIVED (component contract + joint subtree,
        re-read at every seam), so the old limb_type editor, color
        combo, and Fingers add/remove dial are gone — fingers render
        read-only. The Twist dial stays: it authors real joints, which
        derivation then re-detects.
        """
        limb = ln.find_limb_for_joint(joint_name) if joint_name else None
        if not limb:
            return None

        section = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # (Header lives on the '// LIMB' frame _rebuild wraps this in.)
        type_row = QtWidgets.QHBoxLayout()
        type_row.setContentsMargins(0, 0, 0, 0)
        type_row.setSpacing(4)
        type_row.addWidget(mindmeld_style.field_label('limb'))
        type_row.addWidget(
            QtWidgets.QLabel(ln.limb_display_label(ln.get_limb_type(limb))),
            stretch=1)
        type_wrap = QtWidgets.QWidget()
        type_wrap.setLayout(type_row)
        layout.addWidget(type_wrap)

        # Feature-gated blocks (Derived Limbs, spec 2026-07-11 §1): a
        # leg limb never shows a fingers block; each block renders only
        # when some member component's contract declares the feature.
        if ln.limb_has_feature(limb, 'fingers'):
            layout.addWidget(self._build_fingers_dial(limb))
        if ln.limb_has_feature(limb, 'twists'):
            layout.addWidget(self._build_twist_dial(limb))

        return section

    def _build_fingers_dial(self, limb: str) -> QtWidgets.QWidget:
        """Fingers, read-only (Derived Limbs, spec 2026-07-11): one line
        per discovered finger root, with its curl-excluded joints noted.
        Fingers are authored as JOINTS in the Armature phase; membership
        re-derives at every seam (add, load, mirror, build preflight) —
        there is nothing to add or remove here."""
        roots = ln.list_finger_roots(limb)
        excluded = set(ln.list_curl_excluded(limb))

        wrapper = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        layout.addWidget(mindmeld_style.caps_label(
            f'// FINGERS — {len(roots)} (discovered)'))
        for root in roots:
            short = _short_name(root)
            chain = [j for j in hc.walk_finger_chain(root)]
            excl = [_short_name(j) for j in chain if j in excluded]
            text = f'   {short}'
            if excl:
                text += f'   (no curl: {", ".join(excl)})'
            layout.addWidget(QtWidgets.QLabel(text))
        if not roots:
            layout.addWidget(QtWidgets.QLabel(
                '   none — parent finger joints under the wrist; they '
                'join on the next build/load'))

        return wrapper

    def _build_twist_dial(self, limb: str) -> QtWidgets.QWidget:
        """Twist upper / Twist lower: N — one int spinbox per segment,
        wired to limb_node.limb_set_twist_count. A plain count (no
        per-item rows like the Fingers dial's own — a twist joint
        carries no per-joint editable metadata the way a finger's
        curl-exclusion checkboxes do), same '// <LABEL>' caps-header +
        QFormLayout idiom the JOINT section above uses for radius/
        rotate_order."""
        from maya_tools.rigging.fabricator.ui.option_widgets import _NoWheelSpinBox

        wrapper = QtWidgets.QWidget()
        layout = QtWidgets.QFormLayout(wrapper)
        layout.setLabelAlignment(QtCore.Qt.AlignRight)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addRow(mindmeld_style.caps_label('// TWIST'))

        for segment in ('upper', 'lower'):
            count = len(ln.list_twist_upper(limb) if segment == 'upper'
                        else ln.list_twist_lower(limb))
            sb = _NoWheelSpinBox()
            sb.setRange(0, 20)
            sb.setValue(count)
            sb.valueChanged.connect(
                lambda v, lb=limb, seg=segment:
                self._write_twist_count(lb, seg, v))
            layout.addRow(mindmeld_style.field_label(f'twist_{segment}'), sb)

        return wrapper

    def _write_twist_count(self, limb: str, segment: str, n: int) -> None:
        if self._loading:
            return
        current = len(ln.list_twist_upper(limb) if segment == 'upper'
                     else ln.list_twist_lower(limb))
        n = int(n)
        if n == current:
            return
        try:
            ln.limb_set_twist_count(limb, segment, n)
        except Exception as e:
            self._handle_error(
                e, brief=f'Twist {segment} count write failed: {e}')
            # limb_set_twist_count's own guard contract makes zero
            # mutations on a raise, but the spinbox already shows the
            # dragged-to value (valueChanged fires before this slot
            # runs) — rebuild unconditionally, same as the success path
            # below, so the widget re-reads the real (unchanged) count
            # instead of lying about scene state until reselection.
            self._rebuild()
            return
        self._rebuild()

    # (Derived Limbs, spec 2026-07-11: _on_add_finger /
    # _select_new_finger_ctrl / _on_remove_finger /
    # _on_unregister_finger / _write_limb_type / _write_limb_color /
    # _write_curl_excluded are retired with the Fingers dial.)

    def _resolve_joint_source(self, joint_name: str):
        """Return ('joint', name) if joint exists, ('guide', guide_name)
        if a guide carries fab_joint_name == joint_name, else None."""
        if not joint_name:
            return None
        if (cmds.objExists(joint_name)
                and cmds.nodeType(joint_name) == 'joint'):
            return ('joint', joint_name)
        guides_grp = nodes.get_guides_grp()
        if not guides_grp:
            return None
        guides = cmds.listRelatives(guides_grp, allDescendents=True,
                                    type='transform', fullPath=True) or []
        for g in guides:
            if not cmds.attributeQuery('fab_joint_name', node=g, exists=True):
                continue
            if cmds.getAttr(f'{g}.fab_joint_name') == joint_name:
                return ('guide', g)
        return None

    def _read_rotate_order(self, kind: str, source_node: str) -> str:
        if kind == 'joint':
            idx = cmds.getAttr(f'{source_node}.rotateOrder')
            return _ROTATE_ORDERS[idx] if 0 <= idx < len(_ROTATE_ORDERS) else 'xyz'
        # guide
        return cmds.getAttr(f'{source_node}.fab_rotate_order') or 'xyz'

    def _read_radius(self, kind: str, source_node: str) -> float:
        if kind == 'joint':
            return float(cmds.getAttr(f'{source_node}.radius') or 1.0)
        return float(cmds.getAttr(f'{source_node}.fab_radius') or 1.0)

    def _write_rotate_order(self, new_value: str) -> None:
        if self._loading:
            return
        source = self._resolve_joint_source(self._current_joint_name)
        if source is None:
            return
        kind, source_node = source
        if new_value not in _ROTATE_ORDERS:
            return
        if kind == 'joint':
            current = self._read_rotate_order(kind, source_node)
            if current == new_value:
                return
            cmds.setAttr(f'{source_node}.rotateOrder',
                         _ROTATE_ORDERS.index(new_value))
        else:
            current = cmds.getAttr(f'{source_node}.fab_rotate_order') or ''
            if current == new_value:
                return
            cmds.setAttr(f'{source_node}.fab_rotate_order',
                         new_value, type='string')

    def _write_radius(self, new_value: float) -> None:
        if self._loading:
            return
        source = self._resolve_joint_source(self._current_joint_name)
        if source is None:
            return
        kind, source_node = source
        if kind == 'joint':
            current = float(cmds.getAttr(f'{source_node}.radius') or 1.0)
            if abs(current - new_value) < 1e-9:
                return
            cmds.setAttr(f'{source_node}.radius', new_value)
        else:
            current = float(cmds.getAttr(f'{source_node}.fab_radius') or 1.0)
            if abs(current - new_value) < 1e-9:
                return
            cmds.setAttr(f'{source_node}.fab_radius', new_value)

    # (Derived authoring only: _read_follow_value/_write_follow_rule
    # retired with the Follow block, 2026-07-12.)

    def _build_component_section(self, component_id: str) -> QtWidgets.QWidget:
        from maya_tools.rigging.fabricator.ui.option_widgets import _NoWheelComboBox
        cnode = nodes.find_component_node_by_id(component_id)
        if not cnode:
            return None
        ctype = nodes.get_component_type(cnode)
        try:
            cls = get_component_class(ctype)
        except KeyError:
            return None
        contract = cls.CONTRACT

        # Read all scalar attrs from network node up front.
        cur_joints = [j.split('|')[-1].split(':')[-1]
                      for j in nodes.get_component_joints(cnode)]
        cur_parent_plug = nodes.get_component_parent_plug(cnode)
        cur_side = nodes.get_component_side(cnode) or 'md'
        cur_role = nodes.get_component_role(cnode)
        cur_region = nodes.get_component_region(cnode)
        cur_options = nodes.get_component_options(cnode)

        section = QtWidgets.QWidget()
        layout = QtWidgets.QFormLayout(section)
        layout.setLabelAlignment(QtCore.Qt.AlignRight)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Identity rows only (2026-07-10 launch polish): Options and
        # Spaces render as their own frames via _build_options_form_
        # widget / _build_spaces_section in _rebuild, which also owns
        # the Animation-Mode options-only ('// CONTROLLER') split.

        # id (read-only — downstream parent_plug refs key off this string,
        # so live edits would silently break wiring. Component IDs are
        # derived from the primary joint and rarely need hand-editing).
        id_le = QtWidgets.QLineEdit(component_id)
        id_le.setReadOnly(True)
        id_le.setToolTip(
            'Component ID is read-only — parent_plug references key off '
            'this string. Rename the primary joint to change the derived ID.'
        )
        layout.addRow(mindmeld_style.field_label('id'), id_le)

        # type (read-only)
        type_le = QtWidgets.QLineEdit(ctype)
        type_le.setReadOnly(True)
        layout.addRow(mindmeld_style.field_label('type'), type_le)

        # joints — role dropdowns when contract declares joint_roles, else
        # read-only summary as before.
        if contract.joint_roles:
            for role_idx, role in enumerate(contract.joint_roles):
                current = cur_joints[role_idx] if role_idx < len(cur_joints) else ''
                if role.descendant_of == '':
                    # First role (start) — read-only LineEdit, set at add time
                    le = QtWidgets.QLineEdit(current)
                    le.setReadOnly(True)
                    layout.addRow(mindmeld_style.field_label(role.label), le)
                else:
                    combo = _NoWheelComboBox()
                    combo.addItem('(unset)', '')
                    anchor_idx = next(
                        i for i, r in enumerate(contract.joint_roles)
                        if r.name == role.descendant_of
                    )
                    anchor_joint = (cur_joints[anchor_idx]
                                    if anchor_idx < len(cur_joints) else '')
                    if anchor_joint:
                        for desc in _descendants_of_in_scene(anchor_joint):
                            combo.addItem(desc, desc)
                    if current:
                        idx = combo.findData(current)
                        if idx >= 0:
                            combo.setCurrentIndex(idx)
                    combo.currentIndexChanged.connect(
                        lambda _i, cid=component_id, ri=role_idx, c=combo:
                        self._write_component_role(cid, ri, c.currentData()))
                    layout.addRow(mindmeld_style.field_label(role.label), combo)
        else:
            joints_le = QtWidgets.QLineEdit(', '.join(cur_joints))
            joints_le.setReadOnly(True)
            layout.addRow(mindmeld_style.field_label('joints'), joints_le)

        # parent_plug (combo of available <id>.<plug> from other components)
        plug_combo = _NoWheelComboBox()
        plug_combo.addItem('(none)', '')
        for other_cnode in nodes.get_all_component_nodes():
            other_id = nodes.get_component_id(other_cnode)
            if other_id == component_id:
                continue
            try:
                other_cls = get_component_class(nodes.get_component_type(other_cnode))
            except KeyError:
                continue
            for plug in other_cls.CONTRACT.outputs:
                if plug.kind == 'matrix':
                    plug_ref = f'{other_id}.{plug.name}'
                    plug_combo.addItem(plug_ref, plug_ref)
        idx = plug_combo.findData(cur_parent_plug or '')
        if idx >= 0:
            plug_combo.setCurrentIndex(idx)
        plug_combo.currentIndexChanged.connect(
            lambda _i, cid=component_id, c=plug_combo:
            self._write_component_parent_plug(cid, c.currentData()))
        layout.addRow(mindmeld_style.field_label('parent_plug'), plug_combo)

        # side
        side_combo = _NoWheelComboBox()
        side_combo.addItems(('md', 'lf', 'rt'))
        side_combo.setCurrentText(cur_side)
        side_combo.setEnabled(bool(contract.side_supported))
        side_combo.currentIndexChanged.connect(
            lambda _i, cid=component_id, c=side_combo:
            self._write_component_side(cid, c.currentText()))
        layout.addRow(mindmeld_style.field_label('side'), side_combo)

        # role
        role_le = QtWidgets.QLineEdit(cur_role)
        role_le.setPlaceholderText('shoulder, spine_03, …  (optional)')
        role_le.editingFinished.connect(
            lambda cid=component_id, w=role_le:
            self._write_component_role_string(cid, w.text()))
        layout.addRow(mindmeld_style.field_label('role'), role_le)

        # region
        region_combo = _NoWheelComboBox()
        region_combo.setEditable(True)
        region_combo.addItems(('', 'arm', 'leg', 'spine', 'head', 'hand', 'foot', 'face'))
        region_combo.setCurrentText(cur_region)
        region_combo.setToolTip(
            'Anatomical region for pose-library auto-sets. Leave blank to '
            'inherit the contract default (if any).')
        region_combo.currentTextChanged.connect(
            lambda _t, cid=component_id, c=region_combo:
            self._write_component_region(cid, c.currentText()))
        layout.addRow(mindmeld_style.field_label('region'), region_combo)

        return section

    def _build_options_form_widget(self, component_id: str
                                   ) -> QtWidgets.QWidget | None:
        """The component's option-schema rows as a standalone form widget
        (2026-07-10 launch polish: Options render in their own frame —
        '// OPTIONS' in Edit Mode, '// CONTROLLER' in Animation Mode —
        instead of trailing the identity rows). Returns None when the
        component is unresolvable or declares no options, so _rebuild
        skips the frame entirely."""
        cnode = nodes.find_component_node_by_id(component_id)
        if not cnode:
            return None
        try:
            cls = get_component_class(nodes.get_component_type(cnode))
        except KeyError:
            return None
        contract = cls.CONTRACT
        if not contract.options_schema:
            return None
        cur_options = nodes.get_component_options(cnode)

        section = QtWidgets.QWidget()
        layout = QtWidgets.QFormLayout(section)
        layout.setLabelAlignment(QtCore.Qt.AlignRight)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._populate_options_form(layout, contract, cur_options,
                                    component_id)
        return section

    def _populate_options_form(self, layout, contract, cur_options,
                               component_id):
        """Append every option-schema row to `layout`. Shared by Edit Mode
        (full component panel) and Animation Mode (Options-only panel)."""
        from maya_tools.rigging.fabricator.ui import state as ks_state
        current_mode = ks_state.detect_mode()
        extra_guide_option_keys = {
            f'{eg.name}_position' for eg in contract.extra_guides
        }
        # Hover annotations for each option widget (gif + the option's own
        # tooltip text). One controller per panel, created lazily; fully
        # guarded so a failure never blocks the options form.
        try:
            if getattr(self, '_anno', None) is None:
                from maya_tools.utils.qt.hover_annotation import AnnotationController
                self._anno = AnnotationController(self)
        except Exception:
            self._anno = None
        for opt_name, opt_field in contract.options_schema.items():
            current = cur_options.get(opt_name, opt_field.default)
            ow = widget_for_option_field(opt_field, current)
            if (opt_name in extra_guide_option_keys
                    and current_mode != ks_state.MODE_EMPTY):
                ow.widget.setEnabled(False)
                ow.widget.setToolTip(
                    'Drag the pivot locator in the viewport. '
                    'Position captures on Build Modules; respawns on Unbuild.'
                )
            else:
                ow.widget.setToolTip(opt_field.description or '')
            ow.change_signal.connect(
                lambda *_args, cid=component_id, key=opt_name, oo=ow:
                self._write_component_option(cid, key, oo.get_value()))
            layout.addRow(mindmeld_style.field_label(opt_name), ow.widget)
            try:
                if getattr(self, '_anno', None) is not None:
                    from maya_tools.framework import annotations
                    self._anno.attach_widget(
                        ow.widget,
                        lambda w=ow.widget, t=contract.type, k=opt_name:
                            annotations.for_option(t, k, w.toolTip()))
            except Exception:
                pass

    def _build_spaces_section(self, component_id: str):
        """Render the Spaces panel section for any component whose
        contract declares non-empty space_consumers. One subsection per
        SpaceConsumer.

        Section header is '// SPACES' when the contract has a single
        consumer (matches the AdvancedFK case visually), or
        '// SPACES — <ctrl_role>' per subsection when there are 2+.

        Returns the wrapper QWidget, or None if the component node
        can't be resolved.
        """
        cnode = nodes.find_component_node_by_id(component_id)
        if not cnode:
            return None
        ctype = nodes.get_component_type(cnode)
        try:
            cls = get_component_class(ctype)
        except KeyError:
            return None
        contract = cls.CONTRACT
        if not contract.space_consumers:
            return None

        section = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        multi = len(contract.space_consumers) > 1
        for consumer in contract.space_consumers:
            # The '// SPACES' frame _rebuild wraps this in carries the
            # section title (2026-07-10 launch polish); only the
            # multi-consumer case still needs per-role sub-headers.
            if multi:
                layout.addWidget(mindmeld_style.caps_label(
                    f'// SPACES — {consumer.ctrl_role}'))

            # Locked default rows — one per name in consumer.defaults.
            for default_name in consumer.defaults:
                row = QtWidgets.QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(4)
                label = QtWidgets.QLabel(f'   {default_name}    (default)')
                label.setEnabled(False)
                row.addWidget(label, stretch=1)
                row.addStretch()
                wrapper = QtWidgets.QWidget()
                wrapper.setLayout(row)
                layout.addWidget(wrapper)

            # User rows — one per joint connected to spaces_<ctrl_role>.
            for joint_name in nodes.get_ctrl_space_names(
                    cnode, consumer.ctrl_role):
                row = self._make_space_row(
                    component_id, consumer.ctrl_role, joint_name,
                )
                layout.addWidget(row)

            # Add-space input + button, per consumer.
            add_row = QtWidgets.QHBoxLayout()
            add_row.setContentsMargins(0, 0, 0, 0)
            add_row.setSpacing(4)
            add_input = QtWidgets.QLineEdit()
            add_input.setPlaceholderText('enter joint name...')
            add_row.addWidget(add_input, stretch=1)
            add_btn = mindmeld_style.button('+ Add Space', kind='ghost')
            add_btn.setFixedWidth(110)
            add_row.addWidget(add_btn)
            add_wrapper = QtWidgets.QWidget()
            add_wrapper.setLayout(add_row)
            layout.addWidget(add_wrapper)

            # Inline error label — hidden by default, ember on show.
            error_label = QtWidgets.QLabel('')
            error_label.setStyleSheet(f'color: {mindmeld_style.EMBER};')
            error_label.setVisible(False)
            layout.addWidget(error_label)

            def _commit_add(_role=consumer.ctrl_role,
                            _input=add_input,
                            _err=error_label,
                            _cid=component_id):
                text = (_input.text() or '').strip()
                if not text:
                    return
                if not (cmds.objExists(text)
                        and cmds.nodeType(text) == 'joint'):
                    _err.setText(f"Joint {text!r} not found — try again.")
                    _err.setVisible(True)
                    return
                _cnode = nodes.find_component_node_by_id(_cid)
                if not _cnode:
                    return
                if nodes.add_ctrl_space(_cnode, _role, text):
                    _err.setVisible(False)
                    _input.clear()
                    self._rebuild()

            add_btn.clicked.connect(_commit_add)
            add_input.returnPressed.connect(_commit_add)

        return section

    def _make_space_row(self, component_id: str, ctrl_role: str,
                        joint_name: str):
        """One user-added space row: joint name label + '−' remove button."""
        wrapper = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        label = QtWidgets.QLabel(f'   {joint_name}')
        row.addWidget(label, stretch=1)

        rm_btn = mindmeld_style.button('−', kind='ghost')
        rm_btn.setFixedWidth(28)
        rm_btn.setToolTip(f'Remove {joint_name} from spaces')

        def _on_remove():
            cnode = nodes.find_component_node_by_id(component_id)
            if cnode:
                nodes.remove_ctrl_space(cnode, ctrl_role, joint_name)
                self._rebuild()

        rm_btn.clicked.connect(_on_remove)
        row.addWidget(rm_btn)
        return wrapper

    # ─── Component writer methods ────────────────────────────────────────────

    def _write_component_id(self, old_id: str, new_id: str) -> None:
        """Renaming a component changes its id attr. Component network node
        name itself isn't renamed (the prefix is opaque to the user).

        Rebuilds the section after the rename so widget closures bind to
        the new id — otherwise subsequent field writes silently no-op
        against the now-stale old_id captured in their lambdas.
        """
        if self._loading:
            return
        new_id = (new_id or '').strip()
        if not new_id or new_id == old_id:
            return
        cnode = nodes.find_component_node_by_id(old_id)
        if not cnode:
            return
        cmds.setAttr(f'{cnode}.component_id', new_id, type='string')
        self._current_component_id = new_id
        self._rebuild()

    def _write_component_parent_plug(self, component_id: str, new_plug: str) -> None:
        if self._loading:
            return
        cnode = nodes.find_component_node_by_id(component_id)
        if not cnode:
            return
        current = nodes.get_component_parent_plug(cnode)
        if current == (new_plug or ''):
            return
        cmds.setAttr(f'{cnode}.parent_plug', new_plug or '', type='string')

    def _write_component_side(self, component_id: str, new_side: str) -> None:
        if self._loading:
            return
        cnode = nodes.find_component_node_by_id(component_id)
        if not cnode:
            return
        current = nodes.get_component_side(cnode)
        if current == new_side:
            return
        cmds.setAttr(f'{cnode}.side', new_side or 'md', type='string')

    def _write_component_role_string(self, component_id: str, new_role: str) -> None:
        if self._loading:
            return
        cnode = nodes.find_component_node_by_id(component_id)
        if not cnode:
            return
        current = nodes.get_component_role(cnode)
        if current == (new_role or ''):
            return
        nodes.set_component_role(cnode, new_role)

    def _write_component_region(self, component_id: str, new_region: str) -> None:
        if self._loading:
            return
        cnode = nodes.find_component_node_by_id(component_id)
        if not cnode:
            return
        current = nodes.get_component_region(cnode)
        if current == (new_region or ''):
            return
        nodes.set_component_region(cnode, new_region)

    def _write_component_option(self, component_id: str, option_name: str,
                                new_value) -> None:
        if self._loading:
            return
        cnode = nodes.find_component_node_by_id(component_id)
        if not cnode:
            return
        current = nodes.get_component_options(cnode)
        if current.get(option_name) == new_value:
            return
        current[option_name] = new_value
        nodes.set_component_options(cnode, current)

    def _write_component_role(self, component_id: str, role_idx: int,
                              new_joint: str) -> None:
        """A role dropdown changed. Update the component's joints list at
        the given index. Later roles whose descendancy chain is now broken
        are blanked. Writes back via replace_message_multi."""
        if self._loading:
            return
        cnode = nodes.find_component_node_by_id(component_id)
        if not cnode:
            return
        cur_joints = [j.split('|')[-1].split(':')[-1]
                      for j in nodes.get_component_joints(cnode)]
        while len(cur_joints) <= role_idx:
            cur_joints.append('')
        if cur_joints[role_idx] == (new_joint or ''):
            return
        cur_joints[role_idx] = new_joint or ''

        # Invalidate later roles that descended from this one (transitively)
        try:
            cls = get_component_class(nodes.get_component_type(cnode))
        except KeyError:
            return
        roles = cls.CONTRACT.joint_roles
        invalidated = {roles[role_idx].name}
        for i in range(role_idx + 1, len(roles)):
            if roles[i].descendant_of in invalidated:
                if i < len(cur_joints):
                    cur_joints[i] = ''
                invalidated.add(roles[i].name)

        # Persist: filter to joints that actually exist; replace message-multi.
        # Also update the joint_names string mirror so canonical names survive
        # joint deletion/recreation.
        from maya_tools.utils.maya import network_nodes as nn
        live = [j for j in cur_joints if j and cmds.objExists(j)]
        nn.replace_message_multi(cnode, 'joints', live)
        nodes.set_component_joint_names(cnode, cur_joints)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _short_name(name: str) -> str:
    return (name or '').split('|')[-1].split(':')[-1]


def _derive_id(cdata) -> str:
    """Fallback id derivation, mirrors Component.default_id without the class lookup."""
    if not cdata.joints:
        return cdata.type.lower()
    first = cdata.joints[0].split('|')[-1].split(':')[-1]
    snake = ''
    t = cdata.type
    for i, ch in enumerate(t):
        if i > 0 and ch.isupper() and t[i - 1].islower():
            snake += '_'
        snake += ch.lower()
    return f'{first}_{snake}'


def _descendants_of_in_scene(anchor_joint: str) -> list:
    """Return all joint names nested below anchor_joint, in top-down order.

    Resolves dual source — if anchor_joint exists as a Maya joint, walk
    the scene's joint hierarchy. If not, walk the guides_grp looking for
    a guide whose fab_joint_name == anchor_joint and return descendant
    guides' joint-name attrs. The latter path supports pre-Build-Skeleton
    role assignment where joints don't exist yet but guides do.
    """
    if not anchor_joint:
        return []

    # Joint case (post-Build-Skeleton or runtime-added joint).
    if cmds.objExists(anchor_joint) and cmds.nodeType(anchor_joint) == 'joint':
        desc = cmds.listRelatives(anchor_joint, allDescendents=True,
                                  type='joint', fullPath=False) or []
        # Maya returns reverse-traversal order; reverse for top-down.
        return list(reversed(desc))

    # Guide case (pre-Build-Skeleton). Find the guide whose
    # fab_joint_name matches anchor_joint, then walk its descendant
    # guides and return their fab_joint_name values.
    guides_grp = nodes.get_guides_grp()
    if not guides_grp:
        return []
    all_under = cmds.listRelatives(guides_grp, allDescendents=True,
                                   type='transform', fullPath=True) or []
    anchor_guide = None
    for g in all_under:
        if not cmds.attributeQuery('fab_joint_name', node=g, exists=True):
            continue
        if cmds.getAttr(f'{g}.fab_joint_name') == anchor_joint:
            anchor_guide = g
            break
    if not anchor_guide:
        return []

    desc_guides = cmds.listRelatives(anchor_guide, allDescendents=True,
                                     type='transform', fullPath=True) or []
    out = []
    # cmds.listRelatives reverse-traversal — reverse for top-down.
    for g in reversed(desc_guides):
        if not cmds.attributeQuery('fab_joint_name', node=g, exists=True):
            continue
        out.append(cmds.getAttr(f'{g}.fab_joint_name'))
    return out
