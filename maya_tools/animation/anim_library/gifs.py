# maya_tools/animation/anim_library/gifs.py
"""Playblast → PNG sequence → Pillow GIF for anim library hover previews.

The camera follows the rig's root joint during playblast (translation
only, rotation stays where the framing dialog placed it) so characters
that move forward over the clip stay in frame.

Public API:
- playblast_and_gif(panel, time_range, root_joint, out_gif, out_png,
                     width=256, height=256) -> tuple[Path, Path]

Maya 2025 ships Pillow — `from PIL import Image` works without extra setup.
"""
__author__ = "Adrian Melian"

import shutil
import tempfile
from pathlib import Path

import maya.cmds as cmds


def playblast_and_gif(panel: str, time_range: tuple, root_joint: str,
                       out_gif: Path, out_png: Path,
                       width: int = 256, height: int = 256,
                       duration_per_frame_ms: int = 50) -> tuple:
    """Playblast `panel` over `time_range`, follow `root_joint` with the
    panel's camera (translation only), assemble a GIF via Pillow, plus
    a static PNG from frame 0.

    Returns (out_gif, out_png). Either may be None if generation failed.

    `time_range` is (start, end). `root_joint` may be empty/None — then
    the camera doesn't follow.
    """
    out_gif = Path(out_gif)
    out_png = Path(out_png)
    out_gif.parent.mkdir(parents=True, exist_ok=True)

    start, end = float(time_range[0]), float(time_range[1])
    if start >= end:
        cmds.warning(
            f'Animation Studio GIF: invalid time range [{start}, {end}].'
        )
        return (None, None)

    # Validate panel — playblast's editorPanelName requires a modelPanel.
    # A script editor / outliner / graph editor panel would fail with a
    # cryptic "Object 'X' not found" deep inside playblast.
    panel_type = cmds.getPanel(typeOf=panel) if panel else ''
    if panel_type != 'modelPanel':
        cmds.warning(
            f'Animation Studio GIF: panel {panel!r} is not a modelPanel '
            f'(got type {panel_type!r}). Pass a viewport panel — '
            f'e.g. cmds.getPanel(type="modelPanel")[0].'
        )
        return (None, None)

    tmp_dir = Path(tempfile.mkdtemp(prefix='ks_anim_gif_'))
    tmp_stem = tmp_dir / 'frame'

    # Camera follow constraint (translation only).
    camera_xform = _camera_for_panel(panel)
    constraint = None
    if root_joint and camera_xform and cmds.objExists(root_joint):
        try:
            constraint = cmds.pointConstraint(
                root_joint, camera_xform, mo=True,
            )[0]
            # Sanity: warn if the root joint has no translate keys in [start, end] —
            # means the rig's root motion likely lives on a control further up the
            # hierarchy and the follow camera won't appear to do anything.
            keyed = False
            for axis in ('translateX', 'translateY', 'translateZ'):
                plug = f'{root_joint}.{axis}'
                try:
                    if (cmds.keyframe(plug, q=True, keyframeCount=True) or 0) > 0:
                        keyed = True
                        break
                except RuntimeError:
                    pass
            if not keyed:
                cmds.warning(
                    f'Animation Studio GIF: {root_joint} has no translate keys — '
                    f'camera follow will be a no-op. Root motion likely lives '
                    f'on a controller, not the root joint.'
                )
        except RuntimeError as exc:
            cmds.warning(
                f'Animation Studio GIF: pointConstraint failed: {exc}. '
                f'Playblasting with a static camera.'
            )
            constraint = None

    written_seq = []
    try:
        # Playblast the time range to a PNG sequence.
        try:
            cmds.playblast(
                filename=str(tmp_stem),
                format='image', compression='png',
                widthHeight=(width, height),
                startTime=start, endTime=end,
                forceOverwrite=True, viewer=False,
                showOrnaments=False,
                percent=100, quality=90,
                editorPanelName=panel,
            )
        except RuntimeError as exc:
            cmds.warning(f'Animation Studio GIF: playblast failed: {exc}')
            return (None, None)

        written_seq = sorted(tmp_dir.glob('frame*.png'))
        if not written_seq:
            cmds.warning('Animation Studio GIF: playblast produced no frames.')
            return (None, None)

        # Static PNG = first frame, copied to the sibling path.
        try:
            shutil.copy2(str(written_seq[0]), str(out_png))
        except Exception as exc:
            cmds.warning(f'Animation Studio GIF: static PNG copy failed: {exc}')
            out_png = None

        # Pillow GIF.
        try:
            from PIL import Image
            frames = [Image.open(p).convert('P', palette=Image.Palette.ADAPTIVE)
                      for p in written_seq]
            first, rest = frames[0], frames[1:]
            first.save(
                str(out_gif),
                save_all=True,
                append_images=rest,
                duration=duration_per_frame_ms,
                loop=0,
                optimize=False,
                disposal=2,
            )
        except Exception as exc:
            cmds.warning(f'Animation Studio GIF: Pillow assemble failed: {exc}')
            out_gif = None
    finally:
        if constraint and cmds.objExists(constraint):
            try:
                cmds.delete(constraint)
            except Exception:
                pass
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return (out_gif, out_png)


def _camera_for_panel(panel: str) -> str:
    """Return the transform of the camera currently looking through `panel`,
    or '' if none."""
    if not panel or not cmds.objExists(panel):
        return ''
    try:
        cam_shape = cmds.modelPanel(panel, q=True, camera=True)
    except RuntimeError:
        return ''
    if not cam_shape:
        return ''
    if cmds.nodeType(cam_shape) == 'camera':
        parents = cmds.listRelatives(cam_shape, parent=True, fullPath=True) or []
        return parents[0] if parents else cam_shape
    return cam_shape
