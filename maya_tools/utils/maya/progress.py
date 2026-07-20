# utils/maya/progress.py
# Maya progress window helpers.
import maya.cmds as cmds

_WIN = 'AMProgress'
_BAR = 'AMProgress_bar'
_TXT = 'AMProgress_status'


def start_progress(title: str = '') -> None:
    """Open a progress window with the given title."""
    if cmds.window(_WIN, q=True, exists=True):
        cmds.deleteUI(_WIN)

    cmds.window(_WIN, title=title, maximizeButton=False, minimizeButton=False)
    form = cmds.formLayout()
    txt  = cmds.text(_TXT, label='', align='left', font='boldLabelFont')
    bar  = cmds.progressBar(_BAR, progress=0)

    cmds.formLayout(form, edit=True,
        attachForm=[
            (txt, 'top', 15), (txt, 'left', 10), (txt, 'right', 150),
            (bar, 'left', 10), (bar, 'right', 10),
        ],
        attachControl=[(bar, 'top', 5, txt)],
    )
    cmds.window(_WIN, edit=True, width=300, height=75)
    cmds.showWindow(_WIN)


def set_max(max_value: int) -> None:
    """Set the progress bar maximum value."""
    if cmds.window(_WIN, q=True, exists=True):
        cmds.progressBar(_BAR, edit=True, maxValue=max_value)


def update_progress(label: str = '') -> None:
    """Advance the progress bar by one step and update the status label."""
    if cmds.window(_WIN, q=True, exists=True):
        cmds.progressBar(_BAR, edit=True, step=1)
        cmds.text(_TXT, edit=True, label=label)


def end_progress() -> None:
    """Close the progress window."""
    if cmds.window(_WIN, q=True, exists=True):
        cmds.deleteUI(_WIN)
