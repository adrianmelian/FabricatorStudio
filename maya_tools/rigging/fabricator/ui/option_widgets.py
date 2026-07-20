# Python/maya_tools/rigging/fabricator/ui/option_widgets.py
"""Generated form widgets for OptionField types + shared Vector3Field + ChannelsField.

Spec Section 3 'Right: Properties → Options form'. One factory per
OptionField.type returns an OptionWidget — a thin (widget, get_value,
set_value, change_signal) tuple-shaped object the PropertiesPanel binds
to blueprint state.

Auto-save trigger frequency rules (per spec):
- QComboBox / QCheckBox / QSpinBox / QDoubleSpinBox → emit on every value change.
- QLineEdit → emit on editingFinished (focus-out / Enter), NOT on textChanged.
- Vector3 spinboxes during drag → debounced via QTimer.singleShot(300, …).

The PropertiesPanel _loading flag suppresses these during populate.
"""
__author__ = "Adrian Melian"

from dataclasses import dataclass
from typing import Callable, Any

from PySide6 import QtWidgets, QtCore

from maya_tools.utils.qt.mindmeld import mindmeld_style


# Color palette used by `color_enum` widgets — same names World/SimpleFK accept.
CTRL_COLORS = ('yellow', 'blue', 'red', 'green', 'orange', 'white', 'cyan', 'pink')

# Default keyable transform attrs the ChannelsField surfaces.
_DEFAULT_KEYABLE_ATTRS = ('tx', 'ty', 'tz', 'rx', 'ry', 'rz', 'sx', 'sy', 'sz', 'visibility')


# ─── Wheel-event-blocking widgets ────────────────────────────────────────────
# Adrian: 'middle mouse roll while hovering over them changes values… let's
# customize that widget so it does not take middle mouse input.' Spinboxes
# and combos consume wheel events by default; in a scrolling Properties
# panel that's a footgun. event.ignore() lets the wheel propagate to the
# parent QScrollArea so the panel scrolls normally.

class _NoWheelDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class _NoWheelSpinBox(QtWidgets.QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class _NoWheelComboBox(QtWidgets.QComboBox):
    def wheelEvent(self, event):
        event.ignore()


@dataclass
class OptionWidget:
    """Tuple-shaped binding the PropertiesPanel reads from / writes to."""
    widget: QtWidgets.QWidget
    get_value: Callable[[], Any]
    set_value: Callable[[Any], None]
    change_signal: object  # QtCore.SignalInstance — typed as object for dataclass compat


def widget_for_option_field(field, current_value=None) -> OptionWidget:
    """Construct an OptionWidget appropriate for an OptionField. The current_value
    is applied via set_value before returning (so the widget shows its initial
    state); change_signal stays inert until populate is over (PropertiesPanel
    manages _loading)."""
    factories = {
        'shape_enum':     _build_shape_enum,
        'color_enum':     _build_color_enum,
        'enum':           _build_enum,
        'bool':           _build_bool,
        'int':            _build_int,
        'float':          _build_float,
        'string':         _build_string,
        'string_choices': _build_string_choices,
        'channels':       _build_channels,
        'joint_picker':   _build_joint_picker,
        'xyz_axes':       _build_xyz_axes,
        # 'finger_list' (RETIRED Task 2.3, SPEC 2026-07-09 Limbs +
        # Follower Joints §3.4): RibbonIKArm's 'fingers' option and its
        # FingerListField editor are gone — finger membership lives on
        # the fab_limb node now, edited via the limb dials landing in
        # P3 (interim editing is script-level). No entry here means the
        # generic fallback below (a disabled '?' field) handles a
        # 'finger_list'-typed OptionField, should one ever be authored
        # again — see test_ribbon_ik_arm_ui.py's absence test.
    }
    factory = factories.get(field.type)
    if factory is None:
        # Fallback: a disabled QLineEdit with a "?" — surfaces unknown types
        # without crashing the form.
        line = QtWidgets.QLineEdit('?')
        line.setEnabled(False)
        return OptionWidget(line, lambda: '?', lambda _v: None,
                            line.textChanged)
    ow = factory(field)
    if current_value is not None:
        try:
            ow.set_value(current_value)
        except Exception:
            pass  # malformed persisted value — leave the default
    return ow


# ─── Per-type factories ─────────────────────────────────────────────────────

def _build_shape_enum(field) -> OptionWidget:
    # Lazy import — avoid forcing curve_o_matic into module-load time.
    from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
    combo = _NoWheelComboBox()
    try:
        shapes = com.get_all_shapes()
    except Exception:
        shapes = []
    combo.addItems(sorted(shapes) if shapes else [])
    if field.default and (not shapes or field.default in shapes):
        idx = combo.findText(field.default)
        if idx >= 0:
            combo.setCurrentIndex(idx)
    return OptionWidget(
        widget=combo,
        get_value=lambda: combo.currentText(),
        set_value=lambda v: _set_combo_text(combo, v),
        change_signal=combo.currentIndexChanged,
    )


def _build_color_enum(field) -> OptionWidget:
    combo = _NoWheelComboBox()
    combo.addItems(CTRL_COLORS)
    return OptionWidget(
        widget=combo,
        get_value=lambda: combo.currentText(),
        set_value=lambda v: _set_combo_text(combo, v),
        change_signal=combo.currentIndexChanged,
    )


def _build_enum(field) -> OptionWidget:
    """Generic enum combo — populates from field.choices. Use when the
    set of valid values is small and component-specific (e.g. axis picker
    for IKLeg's foot_roll_axis: choices=('x','y','z'))."""
    combo = _NoWheelComboBox()
    combo.addItems(list(field.choices) if field.choices else [])
    if field.default is not None:
        _set_combo_text(combo, field.default)
    return OptionWidget(
        widget=combo,
        get_value=lambda: combo.currentText(),
        set_value=lambda v: _set_combo_text(combo, v),
        change_signal=combo.currentIndexChanged,
    )


def _build_bool(field) -> OptionWidget:
    cb = QtWidgets.QCheckBox()
    cb.setChecked(bool(field.default))
    return OptionWidget(
        widget=cb,
        get_value=cb.isChecked,
        set_value=lambda v: cb.setChecked(bool(v)),
        change_signal=cb.toggled,
    )


def _build_int(field) -> OptionWidget:
    sb = _NoWheelSpinBox()
    sb.setRange(-1_000_000, 1_000_000)
    sb.setValue(int(field.default or 0))
    return OptionWidget(
        widget=sb,
        get_value=sb.value,
        set_value=lambda v: sb.setValue(int(v)),
        change_signal=sb.valueChanged,
    )


def _build_float(field) -> OptionWidget:
    sb = _NoWheelDoubleSpinBox()
    sb.setDecimals(4)
    rng = getattr(field, 'range', ()) or ()
    if len(rng) == 2:
        lo, hi = float(rng[0]), float(rng[1])
        sb.setRange(lo, hi)
        # Snap step to a sensible fraction of the range — for the common
        # 0.0–1.0 alpha case, that's 0.05.
        sb.setSingleStep(max((hi - lo) / 20.0, 1e-6))
    else:
        sb.setRange(-1e9, 1e9)
        sb.setSingleStep(0.1)
    sb.setValue(float(field.default or 0.0))
    return OptionWidget(
        widget=sb,
        get_value=sb.value,
        set_value=lambda v: sb.setValue(float(v)),
        change_signal=sb.valueChanged,
    )


def _build_string(field) -> OptionWidget:
    le = QtWidgets.QLineEdit()
    le.setText(str(field.default or ''))
    # editingFinished only — spec mandates NOT textChanged (per-keystroke save
    # would trigger a full rebuild per character).
    return OptionWidget(
        widget=le,
        get_value=le.text,
        set_value=lambda v: le.setText(str(v or '')),
        change_signal=le.editingFinished,
    )


def _build_string_choices(field) -> OptionWidget:
    """Editable QComboBox — user can type freely OR pick from a dropdown
    populated by calling field.suggestions_source at populate time.

    Storage: plain string (just like 'string'). The dropdown is purely a
    convenience layer to surface existing values across the scene (e.g.
    the FKAim link_group option uses this to suggest already-named
    groups so the user doesn't typo a fresh string for a member of an
    existing group).

    Auto-save semantics: emits on editingFinished (match the plain
    'string' field — per-keystroke would trigger a full panel rebuild
    per character) AND on currentIndexChanged (so picking from the
    dropdown commits immediately).
    """
    combo = _NoWheelComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
    # Editable QComboBox defaults to sizeAdjustPolicy=AdjustToContents,
    # which shrinks the widget to fit the current text — typing 'eyes'
    # collapses the combo to a few characters wide. Pin a minimum
    # contents length and let the size policy stretch to fill the
    # form column the way non-editable enum combos do.
    combo.setSizeAdjustPolicy(
        QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(16)
    combo.setSizePolicy(
        QtWidgets.QSizePolicy.Expanding,
        QtWidgets.QSizePolicy.Fixed,
    )

    # Populate suggestions from the dotted-path function. Failures
    # (missing module, raise during call, etc.) collapse to an empty
    # dropdown so a broken suggestion source never blocks editing the
    # underlying string.
    src = getattr(field, 'suggestions_source', '') or ''
    if src:
        try:
            import importlib
            mod_path, _, fn_name = src.rpartition('.')
            module = importlib.import_module(mod_path)
            fn = getattr(module, fn_name)
            for s in (fn() or []):
                combo.addItem(str(s))
        except Exception:
            pass  # broken source — fall back to empty dropdown

    # Seed with the field default so the widget shows a non-empty
    # current value when no persisted state exists. set_value (called
    # by widget_for_option_field) overrides this if a current_value is
    # provided.
    default_text = str(field.default or '')
    if default_text and combo.findText(default_text) < 0:
        combo.addItem(default_text)
    if default_text:
        _set_combo_text(combo, default_text)

    # Bridge editingFinished off the embedded QLineEdit + the combo's
    # own currentIndexChanged into one signal so the PropertiesPanel
    # connection in widget_for_option_field works the same way as
    # other widgets (single change_signal).
    bridge = _StringChoicesSignalBridge(combo)

    def set_value(v):
        text = str(v or '')
        # If the current text isn't in the dropdown, add it so picking
        # it later from the dropdown is possible.
        if text and combo.findText(text) < 0:
            combo.addItem(text)
        combo.setCurrentText(text)

    return OptionWidget(
        widget=combo,
        get_value=lambda: combo.currentText(),
        set_value=set_value,
        change_signal=bridge.changed,
    )


class _StringChoicesSignalBridge(QtCore.QObject):
    """Combines editingFinished + currentIndexChanged into a single
    `changed` signal for the string_choices widget. PropertiesPanel binds
    against `changed`; whichever fires first commits the new value.
    """
    changed = QtCore.Signal()

    def __init__(self, combo: QtWidgets.QComboBox):
        super().__init__(combo)
        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.editingFinished.connect(self.changed)
        combo.currentIndexChanged.connect(lambda _i: self.changed.emit())


def _build_joint_picker(field) -> OptionWidget:
    """Combo populated with all joints in the current Maya scene
    (alphabetical). Index 0 is the sentinel '(none)' — its get_value
    returns the empty string, meaning "no target picked".

    Used by FollowJoint's follow_target option. Built fresh each time
    the Properties panel renders, so newly-added scene joints appear
    on the next panel-show.
    """
    import maya.cmds as cmds  # lazy — keep Maya out of module-load
    combo = _NoWheelComboBox()
    combo.addItem('(none)')
    try:
        joints = sorted(cmds.ls(type='joint') or [])
    except Exception:
        joints = []
    combo.addItems(joints)

    def get_value():
        text = combo.currentText()
        return '' if text == '(none)' else text

    def set_value(v):
        if not v:
            combo.setCurrentIndex(0)
        else:
            _set_combo_text(combo, str(v))

    return OptionWidget(
        widget=combo,
        get_value=get_value,
        set_value=set_value,
        change_signal=combo.currentIndexChanged,
    )


def _build_channels(field) -> OptionWidget:
    cf = ChannelsField(field.default or {'keyable': list(_DEFAULT_KEYABLE_ATTRS)})
    return OptionWidget(
        widget=cf,
        get_value=cf.get_value,
        set_value=cf.set_value,
        change_signal=cf.changed,
    )


def _build_xyz_axes(field) -> OptionWidget:
    """3-bool master+XYZ compound. Default value is a 3-tuple/list."""
    default = field.default if field.default else (True, True, True)
    xa = _XyzAxesField(initial=tuple(bool(v) for v in default))
    return OptionWidget(
        widget=xa,
        get_value=xa.get_value,
        set_value=xa.set_value,
        change_signal=xa.changed,
    )


# ─── ChannelsField — K/L/H exclusive radio grid (Spec 1's only custom widget) ─

class ChannelsField(QtWidgets.QWidget):
    """3-column K(eyable) / L(ocked) / H(idden) exclusive radio grid.

    Storage form (read/write):
        {'keyable': ['rx','ry','rz'], 'hidden': []}
    Anything not in keyable + not in hidden → locked. The hidden list is
    only emitted when non-empty.

    UI form: one row per attr, three radios in a button group per row.
    Defaults: tx ty tz rx ry rz sx sy sz visibility — all in keyable[]
    by default unless the OptionField default says otherwise.
    """

    changed = QtCore.Signal()

    def __init__(self, initial: dict = None, parent=None):
        super().__init__(parent)
        self._loading = True
        self._row_groups = {}  # attr_name -> {'k': QRadioButton, 'l': ..., 'h': ...}
        self._build_layout()
        self.set_value(initial or {'keyable': list(_DEFAULT_KEYABLE_ATTRS)})
        self._loading = False

    def _build_layout(self):
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(2)

        # Header row
        layout.addWidget(QtWidgets.QLabel(''), 0, 0)
        for col, glyph in enumerate(('K', 'L', 'H'), start=1):
            hdr = mindmeld_style.caps_label(glyph)
            hdr.setAlignment(QtCore.Qt.AlignCenter)
            layout.addWidget(hdr, 0, col)

        # Default attr rows
        for row, attr in enumerate(_DEFAULT_KEYABLE_ATTRS, start=1):
            self._add_attr_row(layout, row, attr)

    def _add_attr_row(self, layout: QtWidgets.QGridLayout, row: int, attr: str):
        layout.addWidget(QtWidgets.QLabel(attr), row, 0)
        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        radios = {}
        for col, key in enumerate(('k', 'l', 'h'), start=1):
            r = QtWidgets.QRadioButton()
            group.addButton(r)
            layout.addWidget(r, row, col, alignment=QtCore.Qt.AlignCenter)
            r.toggled.connect(self._on_radio_toggled)
            radios[key] = r
        self._row_groups[attr] = radios

    def _on_radio_toggled(self, checked: bool):
        if self._loading or not checked:
            # toggled fires twice (off the old, on the new) — react once.
            return
        self.changed.emit()

    def set_value(self, data: dict):
        self._loading = True
        try:
            keyable = set(data.get('keyable') or [])
            hidden = set(data.get('hidden') or [])
            for attr, radios in self._row_groups.items():
                if attr in keyable:
                    radios['k'].setChecked(True)
                elif attr in hidden:
                    radios['h'].setChecked(True)
                else:
                    radios['l'].setChecked(True)
        finally:
            self._loading = False

    def get_value(self) -> dict:
        keyable = []
        hidden = []
        for attr, radios in self._row_groups.items():
            if radios['k'].isChecked():
                keyable.append(attr)
            elif radios['h'].isChecked():
                hidden.append(attr)
            # locked = neither — implicit
        out = {'keyable': keyable}
        if hidden:
            out['hidden'] = hidden
        return out


# ─── _XyzAxesField — master + XYZ checkbox row with bidirectional sync ──────

class _XyzAxesField(QtWidgets.QWidget):
    """One row: [All] [X] [Y] [Z] — four QCheckBoxes with two-way sync.

    Storage form (read/write): `(bool, bool, bool)` — the three axis
    booleans, in X/Y/Z order. The master checkbox is purely UI state:
    derived from the axis bools on populate (all-on → checked,
    any-off → unchecked) and toggling it sets all three axes at once.

    Sync rules:
    - Master toggled ON  → all three axes ON.
    - Master toggled OFF → all three axes OFF.
    - Any axis toggled OFF → master becomes OFF.
    - All three axes ON  → master becomes ON.

    Emits `changed` whenever the stored value (the 3 axis bools) changes.
    Master-driven sync fires `changed` once for the resulting axis state,
    not per individual axis check during the sync cascade.
    """

    changed = QtCore.Signal()

    def __init__(self, initial=(True, True, True), parent=None):
        super().__init__(parent)
        self._syncing = False  # re-entrancy guard for the master ↔ axes cascade

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.cb_master = QtWidgets.QCheckBox('All')
        self.cb_x = QtWidgets.QCheckBox('X')
        self.cb_y = QtWidgets.QCheckBox('Y')
        self.cb_z = QtWidgets.QCheckBox('Z')
        layout.addWidget(self.cb_master)
        layout.addSpacing(4)
        layout.addWidget(self.cb_x)
        layout.addWidget(self.cb_y)
        layout.addWidget(self.cb_z)
        layout.addStretch(1)

        self.cb_master.toggled.connect(self._on_master_toggled)
        self.cb_x.toggled.connect(self._on_axis_toggled)
        self.cb_y.toggled.connect(self._on_axis_toggled)
        self.cb_z.toggled.connect(self._on_axis_toggled)

        self.set_value(initial)

    def _on_master_toggled(self, on: bool):
        if self._syncing:
            return
        self._syncing = True
        try:
            for cb in (self.cb_x, self.cb_y, self.cb_z):
                cb.setChecked(on)
        finally:
            self._syncing = False
        self.changed.emit()

    def _on_axis_toggled(self, _on: bool):
        if self._syncing:
            return
        self._syncing = True
        try:
            all_on = (self.cb_x.isChecked() and
                      self.cb_y.isChecked() and
                      self.cb_z.isChecked())
            self.cb_master.setChecked(all_on)
        finally:
            self._syncing = False
        self.changed.emit()

    def get_value(self):
        return (
            bool(self.cb_x.isChecked()),
            bool(self.cb_y.isChecked()),
            bool(self.cb_z.isChecked()),
        )

    def set_value(self, value):
        try:
            x, y, z = (bool(v) for v in value)
        except Exception:
            x, y, z = True, True, True
        self._syncing = True
        try:
            self.cb_x.setChecked(x)
            self.cb_y.setChecked(y)
            self.cb_z.setChecked(z)
            self.cb_master.setChecked(x and y and z)
        finally:
            self._syncing = False


# FingerListField — RETIRED (Task 2.3, SPEC 2026-07-09 Limbs + Follower
# Joints §3.4). RibbonIKArm's 'fingers' option this widget used to edit is
# gone; finger/twist membership lives on the fab_limb node now, edited
# via the limb dials landing in P3 (interim editing is script-level,
# acceptable per the SPEC's own phasing). See test_ribbon_ik_arm_ui.py
# for the absence coverage (module no longer exports FingerListField;
# widget_for_option_field falls back to the generic disabled-field
# factory for a 'finger_list'-typed OptionField, should one ever be
# authored again).


# (FollowRuleField retired 2026-07-12, Adrian: follow rules are authored
# ONLY through the FollowJoint component and the Twist dial — the raw
# per-joint editor is gone. follow_rules.py itself is unchanged.)

# ─── Vector3Field — joint translate / rotate / joint_orient ─────────────────

class Vector3Field(QtWidgets.QWidget):
    """Three QDoubleSpinBox side-by-side with X/Y/Z prefix labels.

    Emits `changed` 300ms after the last edit (debounced via QTimer.singleShot
    per spec — drag scrubbing fires valueChanged repeatedly; we coalesce).
    """

    changed = QtCore.Signal()

    def __init__(self, initial=None, decimals=4, single_step=0.1, parent=None):
        super().__init__(parent)
        self._loading = True
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self.changed)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._spins = []
        for axis in ('X', 'Y', 'Z'):
            lbl = mindmeld_style.caps_label(axis)
            sb = _NoWheelDoubleSpinBox()
            sb.setDecimals(decimals)
            sb.setRange(-1e9, 1e9)
            sb.setSingleStep(single_step)
            sb.valueChanged.connect(self._on_value_changed)
            layout.addWidget(lbl)
            layout.addWidget(sb, stretch=1)
            self._spins.append(sb)

        self.set_value(initial if initial is not None else [0.0, 0.0, 0.0])
        self._loading = False

    def _on_value_changed(self, _value):
        if self._loading:
            return
        # Debounce — restarts on every edit. After 300ms quiet, emit.
        self._timer.start()

    def set_value(self, vec):
        self._loading = True
        try:
            for i, sb in enumerate(self._spins):
                sb.setValue(float(vec[i]) if i < len(vec) else 0.0)
        finally:
            self._loading = False

    def get_value(self) -> list:
        return [sb.value() for sb in self._spins]


# ─── Helpers ────────────────────────────────────────────────────────────────

def _set_combo_text(combo: QtWidgets.QComboBox, text):
    if text is None:
        return
    idx = combo.findText(str(text))
    if idx >= 0:
        combo.setCurrentIndex(idx)
