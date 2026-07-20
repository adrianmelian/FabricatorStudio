# maya_tools/animation/pose_library/thumbnails.py
"""Viewport screenshot capture for pose thumbnails.

Uses cmds.playblast for a single-frame screen capture, sized to a
~256x256 thumbnail. Captures the currently-active modelPanel; no
panel switching, no scene mutation.

Public API:
- capture(out_png: Path) — single-shot
- recapture_for_pose(pose_path) — capture and write next to pose
- recapture_for_rig(rig_label) — apply each pose, capture, restore
"""
__author__ = "Adrian Melian"

from pathlib import Path
import shutil
import tempfile

import maya.cmds as cmds

from maya_tools.animation.pose_library import app as pose_app


THUMB_SIZE = 256


def _active_model_panel() -> str:
    """Return the currently-focused modelPanel name."""
    panel = cmds.getPanel(withFocus=True) or ''
    if cmds.getPanel(typeOf=panel) == 'modelPanel':
        return panel
    # Fall back to any visible modelPanel.
    panels = cmds.getPanel(type='modelPanel') or []
    for p in panels:
        if cmds.modelPanel(p, q=True, control=True):
            return p
    raise RuntimeError(
        'Pose Studio: no modelPanel in scene; cannot capture thumbnail.'
    )


def capture(out_png, panel: str = '') -> Path:
    """Capture a Maya modelPanel to a PNG at out_png.

    Uses playblast at the current frame; writes a single image.
    Returns the path written.

    panel: explicit modelPanel name to capture from. Empty string (default)
    means "use the active model panel" — same behavior as v1.
    Used by ThumbnailFramingDialog to capture from the embedded
    viewport rather than the main scene viewport.
    """
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)

    if not panel:
        panel = _active_model_panel()
    current = cmds.currentTime(q=True)

    # Snapshot background settings so we can restore. Maya's default
    # viewport background is dark — character silhouettes get lost
    # against it. Temporarily flip to a light neutral gray for the
    # capture, then put everything back.
    saved_gradient = cmds.displayPref(q=True, displayGradient=True)
    saved_bg = cmds.displayRGBColor('background', q=True)
    saved_bg_top = cmds.displayRGBColor('backgroundTop', q=True)
    saved_bg_bottom = cmds.displayRGBColor('backgroundBottom', q=True)

    # Snapshot per-panel viewport settings — we force textures on +
    # smoothShaded so the thumbnail shows the actual character, and hide
    # nurbsCurves so control rigs don't clutter the silhouette.
    saved_appearance = cmds.modelEditor(panel, q=True, displayAppearance=True)
    saved_textures   = cmds.modelEditor(panel, q=True, displayTextures=True)
    saved_curves     = cmds.modelEditor(panel, q=True, nurbsCurves=True)

    tmp_dir = Path(tempfile.mkdtemp(prefix='ks_pose_thumb_'))
    tmp_stem = tmp_dir / 'frame'
    try:
        # Light, gradient-free background for the thumbnail.
        cmds.displayPref(displayGradient=False)
        cmds.displayRGBColor('background', 0.6, 0.6, 0.6)

        # Textured shaded view, no ctrls.
        cmds.modelEditor(panel, edit=True,
                         displayAppearance='smoothShaded',
                         displayTextures=True,
                         nurbsCurves=False)

        cmds.playblast(
            filename=str(tmp_stem),
            format='image',
            compression='png',
            widthHeight=(THUMB_SIZE, THUMB_SIZE),
            frame=int(current),
            forceOverwrite=True,
            viewer=False,
            showOrnaments=False,
            offScreen=True,
            percent=100,
            quality=90,
            editorPanelName=panel,
        )
        # playblast appends .<frame>.png; find it and move to the target.
        # shutil.move handles cross-drive moves (Path.rename does NOT —
        # Windows tempdir typically lives on C:, pose libraries often
        # on a project drive like D:).
        produced = list(tmp_dir.glob('frame*.png'))
        if not produced:
            raise RuntimeError(
                'Pose Studio: playblast produced no image.'
            )
        shutil.move(str(produced[0]), str(out_png))
    finally:
        # Restore background settings + per-panel viewport state + clean up.
        cmds.displayRGBColor('background', *saved_bg)
        cmds.displayRGBColor('backgroundTop', *saved_bg_top)
        cmds.displayRGBColor('backgroundBottom', *saved_bg_bottom)
        cmds.displayPref(displayGradient=saved_gradient)
        try:
            cmds.modelEditor(panel, edit=True,
                             displayAppearance=saved_appearance,
                             displayTextures=saved_textures,
                             nurbsCurves=saved_curves)
        except Exception:
            pass  # panel may have been destroyed (framing dialog closed)
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return out_png


def thumb_path_for(pose_path) -> Path:
    """Compute the sibling .png path for a pose .pose.json."""
    p = Path(pose_path)
    return p.with_suffix('').with_suffix('.png')


def recapture_for_pose(pose_path) -> Path:
    """Capture the current viewport as the thumbnail for one pose
    (does NOT apply the pose; assumes the viewport already shows it).
    """
    return capture(thumb_path_for(pose_path))


def recapture_for_rig(rig_label: str) -> int:
    """For every pose belonging to rig_label, apply the pose, capture
    a fresh thumbnail, and restore the starting pose afterwards.

    Returns the count of thumbnails refreshed.
    """
    poses = pose_app.list_poses(rig_label)
    if not poses:
        return 0

    # Save snapshot WITHOUT a thumbnail — internal scratch pose only.
    snapshot = pose_app.save_pose('__pre_recapture_snapshot__',
                                   category='animation',
                                   _capture_thumbnail=False)
    refreshed = 0
    try:
        for pose_path in poses:
            try:
                pose_app.load_pose(pose_path)
                capture(thumb_path_for(pose_path))
                refreshed += 1
            except RuntimeError as exc:
                cmds.warning(
                    f'Pose Studio: recapture failed for {pose_path.name}: {exc}'
                )
    finally:
        # Restore.
        pose_app.load_pose(snapshot)
        pose_app.delete_pose(snapshot)
    return refreshed
