"""Toolset auto-updater — the face. Core logic lives in updater_core.

Startup: maybe_check_on_startup() runs the manifest fetch on a worker
thread (Maya's launch is never blocked or slowed) and, when a newer
version exists for an updatable install, fades one quiet toast. All the
levers live in Settings: the startup toggle, Check Now, and the update
dialog itself.
"""
__author__ = "Adrian Melian"

import threading

from maya_tools.framework.updater import updater_core


def maybe_check_on_startup() -> None:
    """Fire-and-forget startup check. Batch-safe, pref-gated, silent on
    every failure path — offline is a normal state."""
    import maya.cmds as cmds
    if cmds.about(batch=True):
        return
    try:
        if not updater_core.check_on_startup_enabled():
            return
        if not updater_core.detect_install()["ok"]:
            return
    except Exception:
        return

    def worker():
        try:
            offer, _status = updater_core.check_for_update()
        except Exception:
            return
        if offer is None:
            return
        import maya.utils

        def announce():
            try:
                from maya_tools.utils.qt import toast
                toast.show_toast(
                    "Fabricator %s is available — Settings > Check Now "
                    "to update" % offer["version"])
            except Exception:
                pass
        maya.utils.executeDeferred(announce)

    threading.Thread(target=worker, name="fs_update_check",
                     daemon=True).start()


class UpdateDialog(object):
    """A small Mindmeld dialog for one offer: what it is, one Update
    button, live status, and the unforced restart prompt on success.
    Plain composition (not a QDialog subclass) is deliberate: the class
    builds lazily so importing this module stays Qt-free until shown."""

    def __init__(self, offer, parent=None):
        from PySide6 import QtCore, QtWidgets
        from maya_tools.utils.qt.mindmeld import mindmeld_style

        self.offer = offer
        self.dialog = QtWidgets.QDialog(parent)
        mindmeld_style.apply(self.dialog)
        self.dialog.setWindowTitle("Fabricator Update")
        self.dialog.setMinimumWidth(420)
        self.dialog.setWindowFlags(
            self.dialog.windowFlags() | QtCore.Qt.WindowType.Tool)

        lay = QtWidgets.QVBoxLayout(self.dialog)
        lay.setSpacing(10)
        lay.setContentsMargins(14, 14, 14, 12)
        lay.addWidget(mindmeld_style.brand_label(
            "Fabricator %s" % offer["version"]))
        lay.addWidget(QtWidgets.QLabel(
            "Installed: %s\nReleased: %s" % (offer.get("installed", "?"),
                                             offer.get("date", "?"))))
        link = QtWidgets.QLabel(
            '<a href="%s">What changed (changelog)</a>'
            % updater_core.CHANGELOG_URL)
        link.setOpenExternalLinks(True)
        lay.addWidget(link)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        row = QtWidgets.QHBoxLayout()
        row.addStretch()
        self.update_btn = mindmeld_style.button("Update", kind="primary")
        self.close_btn = mindmeld_style.button("Later")
        row.addWidget(self.close_btn)
        row.addWidget(self.update_btn)
        lay.addLayout(row)

        self.update_btn.clicked.connect(self._on_update)
        self.close_btn.clicked.connect(self.dialog.reject)

    def show(self):
        self.dialog.show()
        return self

    def _on_update(self):
        from PySide6 import QtWidgets
        import maya.utils

        self.update_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.status.setText("Downloading and installing... Maya stays "
                            "usable; this window reports when it is done.")

        def worker():
            try:
                new_version = updater_core.download_and_install(
                    self.offer, log=lambda *a: None)
            except Exception as exc:
                message = str(exc)

                def failed():
                    self.status.setText(message)
                    self.update_btn.setEnabled(True)
                    self.close_btn.setEnabled(True)
                maya.utils.executeDeferred(failed)
                return

            def done():
                self.status.setText(
                    "Updated to Fabricator %s." % new_version)
                self.close_btn.setEnabled(True)
                self.close_btn.setText("Close")
                QtWidgets.QMessageBox.information(
                    self.dialog, "Restart Maya",
                    "Fabricator %s is installed. Restart Maya to finish — "
                    "until then this session keeps running the previous "
                    "version." % new_version)
            maya.utils.executeDeferred(done)

        threading.Thread(target=worker, name="fs_update_install",
                         daemon=True).start()
