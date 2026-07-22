# maya_tools/rigging/fabricator/ui/skeleton_helpers_bar.py
"""SkeletonHelpersBar — the Armature toolbar (right-click-menu era,
2026-07-19).

Four MenuButtons — SKELETON / AIMERS / MIRROR / DUPLICATE — plus the
checkable Symmetry toggle (plasma green; ember orange while active).
Right-click drops the group's menu (the hover popover panels are
retired, Adrian 2026-07-19: "just the basic right click menu", the
Bridge strip's MenuButton grammar); left-click does nothing — a click
meant to reveal options must never fire an operation (Adrian,
2026-07-05). Hover now shows the standard annotation card instead
(the coach-card HoverAnnotation the Bridge strip buttons wear), so
discoverability survives the panels' retirement.

  Skeleton   rows: armature_popovers.joints_rows
  Aimers     rows: aimers_rows
  Mirror     rows: mirror_rows (icon: fs_mirror_small — Fabricator
             only; RigBot keeps fs_mirror)
  Duplicate  rows: duplicate_rows

The bar is the single SIGNAL source (menu rows emit through it) and
FSWindow is the single handler home.

Mode-driven visibility mirrors the palette: hidden on
MODE_MODULES_BUILT; Unbuild brings the bar back.
"""
__author__ = "Adrian Melian"

from functools import partial

from PySide6 import QtCore, QtWidgets

from maya_tools.framework.toolbar import dpi
from maya_tools.framework.toolbar.widgets.icon_button import load_icon
from maya_tools.framework.toolbar.widgets.menu_button import MenuButton
from maya_tools.rigging.fabricator.ui import armature_popovers as pops
from maya_tools.rigging.fabricator.ui import state
from maya_tools.utils.qt.mindmeld import mindmeld_style


class SkeletonHelpersBar(QtWidgets.QWidget):
    """Armature skeleton toolbar — menu group buttons + Sym toggle."""

    # Joints group
    add_joint_requested        = QtCore.Signal()
    insert_between_requested   = QtCore.Signal()
    build_engine_ik_requested  = QtCore.Signal()
    straighten_mid_requested   = QtCore.Signal()
    solo_visibility_toggle_requested = QtCore.Signal()
    solo_reset_requested       = QtCore.Signal()

    # Aimers group
    aim_all_at_child_requested          = QtCore.Signal()
    aim_joints_requested                = QtCore.Signal()
    aimers_mirror_selected_requested    = QtCore.Signal()
    aimers_rebuild_all_requested        = QtCore.Signal()
    aimers_reset_all_requested          = QtCore.Signal()
    aimers_visibility_toggle_requested  = QtCore.Signal()

    # Mirror group (smart branch mirror — selection-based)
    mirror_limb_requested   = QtCore.Signal()
    mirror_joints_requested = QtCore.Signal()
    mirror_module_requested = QtCore.Signal()

    # Duplicate group (smart region-rename duplicate)
    duplicate_limb_requested   = QtCore.Signal()
    duplicate_joints_requested = QtCore.Signal()

    # Live Mirror — selection-based symmetry toggle.
    live_mirror_toggled = QtCore.Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._anno = None
        self.create_layout()
        self.connect_signals()

    def _menu_button(self, label: str, tooltip: str, rows_factory,
                     icon_name: str = '') -> MenuButton:
        """Right-click MenuButton, command-less: right-click drops the
        group's menu; left-click does nothing (no primary action on the
        button itself, Adrian 2026-07-05). Hover shows the annotation
        card (attached separately in create_layout).

        Icon sits LEFT of the label — set directly on the QPushButton
        rather than through IconButton's icon kwarg, which would
        replace the text. Missing PNG → text-only."""
        btn = MenuButton(label, tooltip=tooltip, title=label,
                         menu_factory=partial(rows_factory, self),
                         width=112, height=28)
        if icon_name:
            qicon = load_icon(icon_name)
            if qicon is not None:
                btn.setIcon(qicon)
                btn.setIconSize(QtCore.QSize(18, 18))
        return btn

    def _annotate(self, widget: QtWidgets.QWidget, button_id: str,
                  title: str, text: str) -> None:
        """Attach the standard hover annotation card (the same
        coach-card the Bridge strip buttons wear, Adrian 2026-07-19).
        Content comes from annotations.for_toolbar, so a gif dropped at
        docs/media/toolbar/<button_id>.gif shows up for free. Fully
        guarded: a failure degrades to the plain tooltip."""
        try:
            from maya_tools.framework import annotations
            from maya_tools.utils.qt.hover_annotation import (
                AnnotationController,
            )
            if self._anno is None:
                self._anno = AnnotationController(self)
            self._anno.attach_widget(
                widget,
                lambda i=button_id, t=title, x=text:
                    annotations.for_toolbar(i, t, x))
        except Exception:
            import traceback
            traceback.print_exc()

    def create_layout(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self.joints_btn = self._menu_button(
            'Skeleton', 'Right-click for options.',
            pops.joints_rows, icon_name='fs_skeleton.png')
        root.addWidget(self.joints_btn)
        self._annotate(
            self.joints_btn, 'fab_skeleton', 'Skeleton',
            'Add a joint, insert joints between two, or build the '
            'UE5 engine-reference joints. Right-click for options.')

        self.aimers_btn = self._menu_button(
            'Aimers', 'Right-click for options.',
            pops.aimers_rows, icon_name='fs_aimer.png')
        root.addWidget(self.aimers_btn)
        self._annotate(
            self.aimers_btn, 'fab_aimers', 'Aimers',
            'Bake joint orientations to their aimers, mirror or '
            'rebuild aimers, or toggle their visibility. Right-click '
            'for options.')

        self.mirror_btn = self._menu_button(
            'Mirror', 'Right-click for options.',
            pops.mirror_rows, icon_name='fs_mirror_small.png')
        root.addWidget(self.mirror_btn)
        self._annotate(
            self.mirror_btn, 'fab_mirror', 'Mirror',
            'Smart mirror the selected limb, its joints only, or a '
            'single module to the other side. Right-click for '
            'options.')

        self.duplicate_btn = self._menu_button(
            'Duplicate', 'Right-click for options.',
            pops.duplicate_rows, icon_name='fs_duplicate.png')
        root.addWidget(self.duplicate_btn)
        self._annotate(
            self.duplicate_btn, 'fab_duplicate', 'Duplicate',
            'Duplicate the selected limb (joints + modules) or its '
            'joints only, with a smart region rename. Right-click '
            'for options.')

        # Symmetry toggle — a MODE, not an action: sibling-sized
        # labeled button, PLASMA green to read as something different
        # from the action groups, EMBER orange while active (Adrian,
        # 2026-07-05). Tokens only; the local stylesheet exists because
        # no Mindmeld factory covers a checked-state accent swap —
        # Mindmeld 2.0 absorbs this in the beauty pass.
        self.live_mirror_btn = QtWidgets.QPushButton('Symmetry')
        self.live_mirror_btn.setCheckable(True)
        self.live_mirror_btn.setFixedSize(dpi.scaled(112),
                                          dpi.scaled(28))
        self.live_mirror_btn.setToolTip(
            'Symmetry — selection-based live mirror for Armature '
            'ctrls and aimers. Grab either side; the counterpart '
            'follows. Toggle off to work asymmetrically.')
        sym_icon = load_icon('fs_symmetry.png')
        if sym_icon is not None:
            self.live_mirror_btn.setIcon(sym_icon)
            self.live_mirror_btn.setIconSize(QtCore.QSize(18, 18))
        self.live_mirror_btn.setStyleSheet(
            f'QPushButton {{'
            f' color: {mindmeld_style.PLASMA};'
            f' border: 1px solid {mindmeld_style.PLASMA_DIM};'
            f' border-radius: 3px;'
            f' background: transparent;'
            f'}}'
            f'QPushButton:hover {{'
            f' border-color: {mindmeld_style.PLASMA};'
            f'}}'
            f'QPushButton:checked {{'
            f' color: {mindmeld_style.EMBER};'
            f' border-color: {mindmeld_style.EMBER};'
            f'}}')
        root.addWidget(self.live_mirror_btn)
        self._annotate(
            self.live_mirror_btn, 'fab_symmetry', 'Symmetry',
            'Selection-based live mirror for Armature ctrls and '
            'aimers. Grab either side; the counterpart follows. '
            'Toggle off to work asymmetrically.')

        # Trailing stretch keeps the groups left-aligned.
        root.addStretch(1)

    def connect_signals(self):
        self.live_mirror_btn.toggled.connect(self.live_mirror_toggled.emit)

    def refresh_state(self, mode: str) -> None:
        """Hide on MODE_MODULES_BUILT — rig-creation tools are irrelevant
        in animation mode. Mirrors PalettePanel.refresh_state(mode)."""
        self.setVisible(mode != state.MODE_MODULES_BUILT)
