# curve_o_matic_ui.py
# Curve-O-Matic — standalone control curve library window.
__author__ = "Adrian Melian"

import traceback

import maya.cmds as cmds
import maya.api.OpenMaya as om
import shiboken6
from PySide6 import QtWidgets, QtCore, QtGui

from maya_tools.rigging.curve_o_matic import curve_o_matic_app as com
from maya_tools.utils.maya.gui import get_maya_window, close_windows_by_name
from maya_tools.utils.qt.mindmeld import mindmeld_style
from maya_tools.utils.qt.widgets import CollapsibleSection, LoggerWidget


class CurveOMaticWindow(QtWidgets.QDialog):
    WINDOW_TITLE = 'CtrlEditor'
    WINDOW_NAME  = 'CurveOMaticWindow'

    def __init__(self, parent=None, embedded: bool = False):
        """embedded=True trims the UI to just the shape list + Swap Curve
        button (everything else hidden). Used when the dialog is hosted
        inside another tool's panel — Fabricator's Animation-Mode Tools
        panel is the first such consumer. Standalone open keeps the full
        UI."""
        super().__init__(parent)
        mindmeld_style.apply(self)

        self._embedded = embedded
        self.setWindowTitle(self.WINDOW_TITLE)
        # Only the standalone window carries the singleton objectName. An
        # embedded instance (hosted inside another tool's panel, e.g.
        # Fabricator's Animation-Mode Tools) must NOT — otherwise the
        # standalone show_window's objectName sweep would find + close the
        # embedded one too (it's a descendant of the Maya main window).
        self.setObjectName(
            self.WINDOW_NAME if not embedded else f'{self.WINDOW_NAME}Embedded')
        self.setMinimumWidth(340)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowType.Tool)
        self.create_layout()
        self.connect_signals()
        self.populate()
        if embedded:
            self._apply_embedded_trim()

    # ── layout ────────────────────────────────────────────────────────────────

    def create_layout(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 12)

        # ── brand bar ── (wrap in QWidget so embedded mode can hide it)
        self._brand_widget = QtWidgets.QWidget()
        brand_row = QtWidgets.QHBoxLayout(self._brand_widget)
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(10)
        # Brand banner (Adrian, 2026-07-15): the FS CtrlEditor banner replaces
        # the text brand. 1330x256 source shown at 198x38 (aspect 5.2,
        # no stretch). Text brand returns if the file ever goes missing.
        from maya_tools.framework.toolbar.widgets.icon_button import load_icon
        _logo = load_icon('fs_ctrleditor_banner.png')
        if _logo is not None:
            self._brand_logo = QtWidgets.QLabel()
            self._brand_logo.setPixmap(_logo.pixmap(QtCore.QSize(198, 38)))
            brand_row.addWidget(self._brand_logo)
        else:
            brand_row.addWidget(mindmeld_style.brand_label('CtrlEditor'))
        brand_row.addWidget(mindmeld_style.caps_label('ctrl shape library'),
                            0, QtCore.Qt.AlignmentFlag.AlignBottom)
        brand_row.addStretch()
        layout.addWidget(self._brand_widget)
        self._rule_above_list = mindmeld_style.horizontal_rule()
        layout.addWidget(self._rule_above_list)

        # ── Shape list ── (right-click → Rename / Delete)
        self.shape_list = QtWidgets.QListWidget()
        self.shape_list.setMinimumHeight(220)
        self.shape_list.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        layout.addWidget(self.shape_list)

        # ── Swap / Build row ── (Swap left of Build at Origin)
        self._build_buttons_widget = QtWidgets.QWidget()
        btn_row = QtWidgets.QHBoxLayout(self._build_buttons_widget)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)
        self.swap_btn = mindmeld_style.button('Swap', kind='primary')
        self.swap_btn.setToolTip(
            "Replace selected control(s)' shape with the chosen library curve. "
            'Transform identity (and all its connections) preserved. '
            'Right-click → Swap (Worldspace): new curve stays flat in world '
            '(ignores the joint rotation), snapped to the joint position only.'
        )
        self.swap_btn.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.build_origin_btn = mindmeld_style.button('Build at Origin', kind='default')
        btn_row.addWidget(self.swap_btn)
        btn_row.addWidget(self.build_origin_btn)
        layout.addWidget(self._build_buttons_widget)

        # ── Edit / Mirror / Combine row ──
        self._emc_widget = QtWidgets.QWidget()
        emc_row = QtWidgets.QHBoxLayout(self._emc_widget)
        emc_row.setContentsMargins(0, 0, 0, 0)
        emc_row.setSpacing(8)
        self.edit_curve_btn = mindmeld_style.button('Edit', kind='default')
        self.edit_curve_btn.setToolTip(
            "Select the CVs of every nurbsCurve shape under the selected "
            "ctrl(s). Limits the CV selection to JUST the chosen ctrls — "
            "child ctrls aren't pulled in."
        )
        self.mirror_curve_btn = mindmeld_style.button('Mirror', kind='default')
        self.mirror_curve_btn.setToolTip(
            "Replace the opposite-side ctrl's curve with a YZ-mirrored "
            "copy of the selected ctrl's curve. Ctrl name must carry a "
            "side token (L/R, lf/rt, …). For Fabricator ctrls, the "
            "mirrored CVs persist to the component network node so the "
            "change survives unbuild/rebuild."
        )
        self.combine_curves_btn = mindmeld_style.button('Combine', kind='default')
        self.combine_curves_btn.setToolTip(
            "Merge multiple curve transforms into one — last-selected is "
            "the target. Each source's world TRS is frozen into its CVs, "
            "then its nurbsCurve shapes are re-parented under the target. "
            "Source transforms are deleted. Use to compose multi-shape "
            "ctrls (like the sphere) from individual curves."
        )
        emc_row.addWidget(self.edit_curve_btn)
        emc_row.addWidget(self.mirror_curve_btn)
        emc_row.addWidget(self.combine_curves_btn)
        layout.addWidget(self._emc_widget)

        # ── Color section ── (rule + label + color combo + apply)
        self._color_widget = QtWidgets.QWidget()
        color_outer = QtWidgets.QVBoxLayout(self._color_widget)
        color_outer.setContentsMargins(0, 0, 0, 0)
        color_outer.setSpacing(10)
        color_outer.addWidget(mindmeld_style.horizontal_rule())
        color_outer.addWidget(mindmeld_style.field_label('Color'))
        color_row = QtWidgets.QHBoxLayout()
        color_row.setSpacing(8)
        self.color_combo = QtWidgets.QComboBox()
        for cname in com.color_names():
            swatch = QtGui.QPixmap(14, 14)
            rgb = com.color_rgb(cname)
            swatch.fill(QtGui.QColor.fromRgbF(rgb[0], rgb[1], rgb[2]))
            self.color_combo.addItem(QtGui.QIcon(swatch), cname.capitalize(),
                                     userData=cname)
        self.apply_color_btn = mindmeld_style.button('Apply Color', kind='default')
        self.apply_color_btn.setToolTip(
            "Set the override color on the selected curve(s)' shapes. For "
            "Fabricator ctrls the owning component's ctrl_color is saved too, "
            "so the color survives unbuild/rebuild."
        )
        color_row.addWidget(self.color_combo, 1)
        color_row.addWidget(self.apply_color_btn)
        color_outer.addLayout(color_row)
        layout.addWidget(self._color_widget)

        # ── Save ── single orange button; name entered via popup on press.
        self.save_btn = mindmeld_style.button('Save', kind='danger')
        self.save_btn.setToolTip(
            'Save the selected curve(s) as a new library shape. '
            'Prompts for a name.'
        )
        layout.addWidget(self.save_btn)

        # ── Logger ──
        self.log_section = CollapsibleSection('// LOG')
        self.logger = LoggerWidget()
        log_body_layout = QtWidgets.QVBoxLayout()
        log_body_layout.setContentsMargins(0, 0, 0, 0)
        log_body_layout.addWidget(self.logger)
        self.log_section.set_body_layout(log_body_layout)
        self.log_section.set_expanded(False)
        layout.addWidget(self.log_section)

    def _apply_embedded_trim(self):
        """Hide non-essential sections for embedded use (Fabricator's
        Animation-Mode Tools panel). The curve-editing actions stay —
        Swap, Edit, Mirror, Combine, Color, plus the shape list. Build at
        Origin, Save, and the log are hidden (Swap shares the build row, so
        only the Build button is hidden, not the whole row)."""
        for w in (
            self.build_origin_btn,
            self.save_btn,
            self.log_section,
        ):
            w.setVisible(False)

    # ── signals ───────────────────────────────────────────────────────────────

    def connect_signals(self):
        self.build_origin_btn.clicked.connect(self._on_build_at_origin)
        self.swap_btn.clicked.connect(lambda: self._on_swap(worldspace=False))
        self.swap_btn.customContextMenuRequested.connect(
            self._on_swap_context_menu)
        self.edit_curve_btn.clicked.connect(self._on_edit_curve)
        self.mirror_curve_btn.clicked.connect(self._on_mirror_curve)
        self.combine_curves_btn.clicked.connect(self._on_combine_curves)
        self.apply_color_btn.clicked.connect(self._on_apply_color)
        self.save_btn.clicked.connect(self._on_save)
        self.shape_list.customContextMenuRequested.connect(
            self._on_shape_context_menu)

        # Maya viewport selection → auto-highlight matching library shape.
        # MEventMessage (not scriptJob). Cleanup runs from BOTH closeEvent
        # (standalone window close) AND the destroyed signal (child-widget
        # path when FSWindow's auto-reopen calls deleteLater on the parent
        # — closeEvent doesn't fire on embedded children). Capture the
        # callback id in closure so the destroyed slot doesn't dereference
        # `self` after the Python object's attrs are gone.
        self._sel_callback_id = None
        try:
            self._sel_callback_id = om.MEventMessage.addEventCallback(
                'SelectionChanged', self._on_maya_selection_changed,
            )
        except Exception:
            traceback.print_exc()
        if self._sel_callback_id is not None:
            cid = self._sel_callback_id
            def _on_destroyed(_obj=None, _cid=cid):
                try:
                    om.MMessage.removeCallback(_cid)
                except Exception:
                    pass
            self.destroyed.connect(_on_destroyed)

    def closeEvent(self, event):
        if self._sel_callback_id is not None:
            try:
                om.MMessage.removeCallback(self._sel_callback_id)
            except Exception:
                pass
            self._sel_callback_id = None
        super().closeEvent(event)

    # ── populate ──────────────────────────────────────────────────────────────

    def populate(self):
        current = (self.shape_list.currentItem().text()
                   if self.shape_list.currentItem() else None)
        self.shape_list.clear()
        for name in com.get_all_shapes():
            self.shape_list.addItem(name)
        if current:
            items = self.shape_list.findItems(current, QtCore.Qt.MatchFlag.MatchExactly)
            if items:
                self.shape_list.setCurrentItem(items[0])

    # ── handlers ─────────────────────────────────────────────────────────────

    def _on_build_at_origin(self):
        name = self._selected_shape()
        if not name:
            return
        try:
            com.build_shape(name, f'{name}_ctrl')
            self.logger.success(f'Built {name} at origin.')
        except Exception as e:
            self._handle_error(e)

    def _on_edit_curve(self):
        """Select the CVs of every nurbsCurve shape under the selected
        ctrl(s). Mirrors rigger_curves.edit_curves_cvs but cmds-only —
        limits the CV pick to JUST the chosen transforms (not their
        descendants), which keeps a parent ctrl's edit session free of
        child-ctrl CVs clutter."""
        sel = cmds.ls(selection=True, transforms=True) or []
        if not sel:
            self.logger.error(
                'Edit Curve: select one or more ctrls in the viewport first.'
            )
            return
        cv_targets = []
        for ctrl in sel:
            for shape in (cmds.listRelatives(ctrl, shapes=True,
                                             type='nurbsCurve',
                                             fullPath=True) or []):
                cv_targets.append(f'{shape}.cv[*]')
        if not cv_targets:
            self.logger.error(
                'Edit Curve: selection has no nurbsCurve shapes.'
            )
            return
        cmds.select(cv_targets, replace=True)

    def _on_mirror_curve(self):
        """Replace the opposite-side ctrl's curve with a YZ-mirrored
        copy of the selected ctrl's. Delegates to fabricator.curve_mirror
        — that module handles the side-token flip, the persist to the
        component network node for Fabricator ctrls, and selection-based
        source resolution when no arg is passed."""
        sel = cmds.ls(selection=True, transforms=True) or []
        if not sel:
            self.logger.error(
                'Mirror Curve: select one or more ctrls in the viewport first.'
            )
            return
        from maya_tools.rigging.fabricator import curve_mirror
        mirrored, failed = [], []
        for ctrl in sel:
            try:
                target = curve_mirror.mirror_curve(ctrl)
                mirrored.append(target)
            except Exception as e:
                traceback.print_exc()
                failed.append(f'{ctrl}: {e}')
        if mirrored:
            self.logger.success(
                f'Mirrored {len(mirrored)} ctrl(s) → {", ".join(mirrored)}'
            )
        for msg in failed:
            self.logger.error(msg)

    def _on_combine_curves(self):
        """Merge multiple curve transforms into a single transform.
        Last-selected is the target — freezes each source's world TRS,
        re-parents its nurbsCurve shapes under target, deletes the empty
        source."""
        try:
            target = com.combine_selected_curves()
            self.logger.success(f'Combined into: {target}')
            cmds.select(target, replace=True)
        except RuntimeError as exc:
            self.logger.error(str(exc))
        except Exception as exc:
            traceback.print_exc()
            self.logger.error(f'Combine Curves failed: {exc}')

    def _on_maya_selection_changed(self, *_):
        """Auto-highlight the library shape for the selected Fabricator
        ctrl by reading its component network node. Bails (no-op) for
        any ctrl without a fab_owner — this is rig-ctrl-only QoL."""
        # Defense: if the C++ widget has been destroyed (FSWindow's
        # auto-reopen path) but the MEventMessage callback hasn't been
        # removed yet, bail before touching shape_list.
        if not shiboken6.isValid(self) or not shiboken6.isValid(self.shape_list):
            return
        try:
            sel = cmds.ls(selection=True, transforms=True) or []
            if not sel:
                return
            name = _shape_name_for_rig_ctrl(sel[0])
            if not name:
                return
            items = self.shape_list.findItems(
                name, QtCore.Qt.MatchFlag.MatchExactly)
            if items:
                self.shape_list.setCurrentItem(items[0])
                self.shape_list.scrollToItem(items[0])
        except Exception:
            # Selection callbacks must not raise — Maya crash risk.
            traceback.print_exc()

    def _on_swap_context_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        ws_act = menu.addAction('Swap (Worldspace)')
        ws_act.setToolTip('New curve snaps to the joint position but stays '
                          'axis-aligned in world (flat in iso) — ignores the '
                          'joint rotation.')
        chosen = menu.exec(self.swap_btn.mapToGlobal(pos))
        if chosen == ws_act:
            self._on_swap(worldspace=True)

    def _on_swap(self, worldspace: bool = False):
        name = self._selected_shape()
        if not name:
            return
        sel = cmds.ls(selection=True, transforms=True) or []
        targets = [t for t in sel
                   if cmds.listRelatives(t, shapes=True, type='nurbsCurve') or []]
        if not targets:
            self.logger.error('Select one or more controls (transforms with nurbsCurve shapes) in Maya.')
            return
        swapped, failed = [], []
        for t in targets:
            try:
                com.swap_shape(name, t, worldspace=worldspace)
                swapped.append(t)
            except Exception as e:
                traceback.print_exc()
                failed.append(f'{t}: {e}')
        if swapped:
            # Re-select the swapped transforms — swap_shape deletes a temp
            # transform and the old shapes mid-op, which can clear Maya's
            # selection. Restoring it means the user can hit Edit Curve
            # immediately without re-picking the ctrls in the viewport.
            cmds.select(swapped, r=True)
            mode = ' (worldspace)' if worldspace else ''
            self.logger.success(f'Swapped{mode} {len(swapped)} ctrl(s) → {name}: {", ".join(swapped)}')
        for msg in failed:
            self.logger.error(msg)

    def _on_apply_color(self):
        color = self.color_combo.currentData()
        try:
            result = com.apply_curve_color(color)
            msg = f"Recolored {result['recolored']} curve(s) {result['color']}"
            if result['saved']:
                msg += f" — saved to {result['saved']} KS component(s)"
            self.logger.success(msg + '.')
        except Exception as e:
            self._handle_error(e)

    def _on_save(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, 'Save Curve', 'Curve name:')
        if not ok:
            return
        name = name.strip().replace(' ', '_')
        if not name:
            self.logger.error('Enter a curve name.')
            return
        try:
            path = com.save_shape_from_selection(name)
            self.populate()
            self.logger.success(f'Saved: {path.name}')
        except Exception as e:
            self._handle_error(e)

    # ── shape-list context menu (Rename / Delete) ──
    def _on_shape_context_menu(self, pos):
        item = self.shape_list.itemAt(pos)
        if item is None:
            return
        name = item.text()
        menu = QtWidgets.QMenu(self)
        rename_act = menu.addAction('Rename...')
        delete_act = menu.addAction('Delete')
        chosen = menu.exec(self.shape_list.mapToGlobal(pos))
        if chosen == rename_act:
            self._rename_shape(name)
        elif chosen == delete_act:
            self._delete_shape(name)

    def _rename_shape(self, name: str):
        new, ok = QtWidgets.QInputDialog.getText(
            self, 'Rename Shape', f'New name for {name}:', text=name)
        if not ok:
            return
        new = new.strip().replace(' ', '_')
        if not new:
            self.logger.error('Enter a new name.')
            return
        try:
            com.rename_shape(name, new)
            self.populate()
            self.logger.success(f'Renamed: {name} → {new}')
        except Exception as e:
            self._handle_error(e)

    def _delete_shape(self, name: str):
        confirm = QtWidgets.QMessageBox.question(
            self, 'Delete Shape',
            f"Delete '{name}'? This cannot be undone.",
            QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            com.delete_shape(name)
            self.populate()
            self.logger.success(f'Deleted: {name}')
        except Exception as e:
            self._handle_error(e)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _selected_shape(self) -> str:
        item = self.shape_list.currentItem()
        if not item:
            self.logger.error('Select a shape first.')
            return ''
        return item.text()

    def _handle_error(self, e: Exception) -> None:
        traceback.print_exc()
        self.logger.error(str(e))

    @staticmethod
    def show_window():
        global _win
        # Reload-proof singleton: the shelf reloads this module each press,
        # so close any prior window by objectName, not the lost _win ref.
        close_windows_by_name(CurveOMaticWindow.WINDOW_NAME)
        _win = CurveOMaticWindow(parent=get_maya_window())
        _win.show()
        _win.raise_()


_win = None


# ── helpers ───────────────────────────────────────────────────────────────

# fab_role → priority list of option keys to read from the component's
# options_json. First non-empty value wins. Multi-ctrl components like
# SimpleIK use different keys per ctrl role; single-ctrl components fall
# through to 'ctrl_shape'. 'ctrl_shape' is appended as the universal
# fallback regardless of role.
_ROLE_TO_SHAPE_OPTIONS: dict[str, list[str]] = {
    'ik_end_ctrl':        ['ik_ctrl_shape'],
    'master_switch_ctrl': ['switch_ctrl_shape'],
    'pv_ctrl':            ['pv_ctrl_shape'],
}


def _shape_name_for_rig_ctrl(ctrl: str) -> str:
    """Return the library shape name that owns this Fabricator ctrl, or
    '' for non-rig ctrls. Reads `fab_owner` → component network node
    → options_json, picks the right option key from the ctrl's
    fab_role. Bails the moment any required attr / connection / key
    is missing — this is rig-ctrl-only QoL, no fallback heuristics."""
    import json
    if not cmds.attributeQuery('fab_owner', node=ctrl, exists=True):
        return ''
    owners = cmds.listConnections(
        f'{ctrl}.fab_owner', source=True, destination=False,
    ) or []
    if not owners:
        return ''
    cnode = owners[0]
    if not cmds.attributeQuery('options_json', node=cnode, exists=True):
        return ''
    try:
        options = json.loads(cmds.getAttr(f'{cnode}.options_json') or '{}')
    except Exception:
        return ''
    role = ''
    if cmds.attributeQuery('fab_role', node=ctrl, exists=True):
        role = cmds.getAttr(f'{ctrl}.fab_role') or ''
    keys = list(_ROLE_TO_SHAPE_OPTIONS.get(role, [])) + ['ctrl_shape']
    for key in keys:
        shape = options.get(key, '')
        if shape:
            return str(shape)
    return ''
