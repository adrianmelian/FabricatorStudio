"""Reggie panel offscreen Qt tests. No Maya at module scope. Run:
PYTHONNOUSERSITE=1 QT_QPA_PLATFORM=offscreen mayapy maya_tools/framework/toolbar/_dev/test_reggie_ui.py
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "maya_tools" / "_vendor"))

FAILURES = []
_APP = None


def qt_app():
    global _APP
    from PySide6 import QtWidgets
    _APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APP


def _flush_qt():
    if _APP is not None:
        _APP.processEvents()


def check(name, fn):
    try:
        fn()
        print(f"  ok: {name}")
    except Exception as exc:
        import traceback
        FAILURES.append(f"{name}: {exc!r}")
        print(f"FAIL: {name}: {exc!r}")
        traceback.print_exc()
    finally:
        _flush_qt()


def test_split_blocks_pure():
    from maya_tools.framework.toolbar.reggie import panel
    text = ("Locked channel found.\n```python\nimport maya.cmds as cmds\n"
            "print('x')\n```\nRun it, then press Build Rig.")
    parts = panel._split_blocks(text)
    kinds = [k for k, _ in parts]
    assert kinds == ["text", "code", "text"], parts
    assert parts[1][1].startswith("import maya.cmds"), parts[1]
    # no fence: single text part; empty: no parts
    assert panel._split_blocks("plain") == [("text", "plain")]
    assert panel._split_blocks("") == []
    # non-alnum language tags (c++/c#/objective-c) get stripped too
    parts = panel._split_blocks("see:\n```c++\nint x = 0;\n```\ndone")
    assert parts == [("text", "see:"), ("code", "int x = 0;"),
                     ("text", "done")], parts
    # reply cut right after the fence tag: the tag is not code
    assert panel._split_blocks("mid ```python") == [("text", "mid")]


def test_panel_setup_card_when_no_key():
    qt_app()
    from maya_tools.framework.toolbar.reggie import keys, panel
    saved = os.environ.pop(keys.ENV_VAR, None)
    try:
        p = panel.ReggiePanel(bridge_autostart=False)
        assert p.setup_card.isVisibleTo(p)
        assert not p.input_row.isVisibleTo(p)
        p.deleteLater()
    finally:
        if saved is not None:
            os.environ[keys.ENV_VAR] = saved


def test_panel_chat_ui_when_key_present():
    qt_app()
    from PySide6 import QtWidgets
    from maya_tools.framework.toolbar.reggie import keys, panel
    saved = os.environ.get(keys.ENV_VAR)
    os.environ[keys.ENV_VAR] = "sk-ant-test-abcd1234"
    try:
        p = panel.ReggiePanel(bridge_autostart=False)
        assert not p.setup_card.isVisibleTo(p)
        assert p.input_row.isVisibleTo(p)
        # key_status (masked text label) is retired: a key_dot reflects
        # presence instead, and the key is never rendered as text.
        assert not hasattr(p, "key_status")
        assert hasattr(p, "key_dot")
        assert p.key_dot.property("state") == "ok"
        all_text = " ".join(
            l.text() for l in p.findChildren(QtWidgets.QLabel))
        assert "1234" not in all_text
        assert "sk-ant-test-abcd" not in all_text
        p.deleteLater()
    finally:
        if saved is None:
            os.environ.pop(keys.ENV_VAR, None)
        else:
            os.environ[keys.ENV_VAR] = saved


def test_key_dot_reflects_state():
    qt_app()
    from maya_tools.framework.toolbar.reggie import keys, panel
    saved = os.environ.pop(keys.ENV_VAR, None)
    try:
        p = panel.ReggiePanel(bridge_autostart=False)
        assert p.key_dot.property("state") == "warn"
        assert "no api key" in p.key_dot.toolTip().lower()

        os.environ[keys.ENV_VAR] = "sk-ant-test-abcd1234"
        p._refresh_key_state()
        assert p.key_dot.property("state") == "ok"
        assert "key detected" in p.key_dot.toolTip().lower()
        p.deleteLater()
    finally:
        if saved is not None:
            os.environ[keys.ENV_VAR] = saved
        else:
            os.environ.pop(keys.ENV_VAR, None)


def test_panel_single_flight_disables_send():
    qt_app()
    from maya_tools.framework.toolbar.reggie import keys, panel
    os.environ[keys.ENV_VAR] = "sk-ant-test-abcd1234"
    p = panel.ReggiePanel(bridge_autostart=False, spawn_threads=False)
    p.input_edit.setPlainText("hello")
    p._send()
    assert not p.send_btn.isEnabled()
    assert p.stop_btn.isVisibleTo(p)
    # simulate turn end
    p._on_done({"stopped": False, "checked": []})
    assert p.send_btn.isEnabled()
    p.deleteLater()


def test_full_turn_renders_transcript_with_code_block():
    qt_app()
    from PySide6 import QtWidgets
    from maya_tools.framework.toolbar.reggie import keys, panel
    os.environ[keys.ENV_VAR] = "sk-ant-test-abcd1234"
    p = panel.ReggiePanel(bridge_autostart=False, spawn_threads=False)
    p.input_edit.setPlainText("why is my rig broken?")
    p._send()
    # a live turn owns the session: both send and new-chat are dead
    assert not p.send_btn.isEnabled()
    assert not p.new_chat_btn.isEnabled()
    reply = ("Locked channel.\n```python\nimport maya.cmds as cmds\n"
             "cmds.setAttr('a.tx', lock=False)\n```\nThen press Build Rig.")
    for token in reply:
        p._on_token(token)
    p._on_done({"stopped": False, "checked": ["get_build_report"]})
    assert p.send_btn.isEnabled()
    assert p.new_chat_btn.isEnabled()
    assert "checked: get_build_report" in p.status_line.text()
    # the finished bubble re-rendered into labels + exactly one code block
    blocks = p._transcript_body.findChildren(panel._CodeBlock)
    assert len(blocks) == 1, blocks
    editor = blocks[0].findChild(QtWidgets.QPlainTextEdit)
    assert editor is not None
    assert editor.toPlainText() == ("import maya.cmds as cmds\n"
                                    "cmds.setAttr('a.tx', lock=False)"), (
        editor.toPlainText())
    copy_btns = [b for b in blocks[0].findChildren(QtWidgets.QPushButton)
                 if b.text().lower() == "copy"]
    assert copy_btns, "code block must carry a Copy button"
    # user bubble and both reggie text parts landed in the transcript
    labels = [l.text() for l in p._transcript_body.findChildren(QtWidgets.QLabel)]
    assert any("why is my rig broken?" in t for t in labels), labels
    assert any("Locked channel." in t for t in labels), labels
    assert any("Then press Build Rig." in t for t in labels), labels
    # part-ordering pin: the finished bubble renders text-code-text IN
    # ORDER — no fusing of the text parts, never code-first. The code
    # block's parentWidget IS the bubble frame (_render_finished_bubble
    # adds the parts straight to the bubble's own layout).
    bubble_lay = blocks[0].parentWidget().layout()
    kinds = [type(bubble_lay.itemAt(i).widget()).__name__
             for i in range(bubble_lay.count())
             if bubble_lay.itemAt(i).widget() is not None]
    assert kinds == ["QLabel", "_CodeBlock", "QLabel"], kinds
    p.deleteLater()


def test_panel_is_mindmeld_styled():
    qt_app()
    from maya_tools.framework.toolbar.reggie import panel
    p = panel.ReggiePanel(bridge_autostart=False)
    assert p.styleSheet(), "mindmeld stylesheet must be applied to the root"
    p.deleteLater()


def test_welcome_bubble_shown_on_open():
    qt_app()
    from PySide6 import QtWidgets
    from maya_tools.framework.toolbar.reggie import panel
    p = panel.ReggiePanel(bridge_autostart=False)
    labels = [l.text() for l in p._transcript_body.findChildren(QtWidgets.QLabel)]
    assert len(labels) == 1, labels
    assert labels[0].startswith("Hello, I'm Reggie"), labels[0]
    # UI furniture only: never reaches ChatSession history
    assert p._session is None or p._session.history == []
    p.deleteLater()


def test_welcome_bubble_returns_after_new_chat():
    qt_app()
    from PySide6 import QtWidgets
    from maya_tools.framework.toolbar.reggie import keys, panel
    os.environ[keys.ENV_VAR] = "sk-ant-test-abcd1234"
    p = panel.ReggiePanel(bridge_autostart=False, spawn_threads=False)
    p.input_edit.setPlainText("why is my rig broken?")
    p._send()
    for token in "hello there":
        p._on_token(token)
    p._on_done({"stopped": False, "checked": []})
    p._on_new_chat_clicked()
    labels = [l.text() for l in p._transcript_body.findChildren(QtWidgets.QLabel)]
    assert len(labels) == 1, labels
    assert labels[0].startswith("Hello, I'm Reggie"), labels[0]
    p.deleteLater()


def test_help_dialog_composes_and_sends():
    qt_app()
    from maya_tools.framework.toolbar.reggie import keys, panel
    os.environ[keys.ENV_VAR] = "sk-ant-test-abcd1234"
    p = panel.ReggiePanel(bridge_autostart=False, spawn_threads=False)
    assert hasattr(p, "help_btn")
    # Banner facelift: help_btn's label is now panel._FORM_BTN_LABEL (a
    # one-line swap while Adrian decides among candidates) - the handler
    # is unchanged, only the text and its container (banner, not
    # input_row) moved.
    assert p.help_btn.text().lower() == panel._FORM_BTN_LABEL.lower()

    p.help_btn.click()
    dlg = p._help_dialog
    assert dlg is not None
    dlg.what_edit.setText("rig popped off origin")
    dlg.steps_edit.setPlainText("1. open scene\n2. press Build Rig")
    dlg.send_btn.click()

    # the composed message went through the normal send path: a user
    # bubble landed in the transcript and single-flight engaged.
    from PySide6 import QtWidgets
    labels = [l.text() for l in p._transcript_body.findChildren(QtWidgets.QLabel)]
    combined = [t for t in labels if "What happened:" in t]
    assert combined, labels
    assert "rig popped off origin" in combined[0], combined[0]
    assert "Steps to reproduce:" in combined[0], combined[0]
    assert "press Build Rig" in combined[0], combined[0]
    assert not p.send_btn.isEnabled()
    assert p.stop_btn.isVisibleTo(p)
    # WA_DeleteOnClose hygiene: the panel drops its reference on finished
    assert p._help_dialog is None
    p.deleteLater()


def test_help_dialog_preserves_input_draft():
    qt_app()
    from PySide6 import QtWidgets
    from maya_tools.framework.toolbar.reggie import keys, panel
    os.environ[keys.ENV_VAR] = "sk-ant-test-abcd1234"
    p = panel.ReggiePanel(bridge_autostart=False, spawn_threads=False)
    p.input_edit.setPlainText("my draft")
    p.help_btn.click()
    dlg = p._help_dialog
    dlg.what_edit.setText("rig popped off origin")
    dlg.steps_edit.setPlainText("1. open scene")
    dlg.send_btn.click()
    # the draft was not clobbered: it leads the sent message, with the
    # composed report appended below it
    labels = [l.text() for l in p._transcript_body.findChildren(QtWidgets.QLabel)]
    sent = [t for t in labels if "What happened:" in t]
    assert sent, labels
    assert sent[0].startswith("my draft"), sent[0]
    assert "rig popped off origin" in sent[0], sent[0]
    assert "Steps to reproduce:" in sent[0], sent[0]
    p.deleteLater()


def test_help_dialog_busy_stages_instead_of_sending():
    qt_app()
    from PySide6 import QtWidgets
    from maya_tools.framework.toolbar.reggie import keys, panel
    os.environ[keys.ENV_VAR] = "sk-ant-test-abcd1234"
    p = panel.ReggiePanel(bridge_autostart=False, spawn_threads=False)
    p.input_edit.setPlainText("first question")
    p._send()                    # creates the session, engages send state
    p._session.busy = True       # simulate the live worker turn
    try:
        p.help_btn.click()
        dlg = p._help_dialog
        dlg.what_edit.setText("second problem")
        dlg.steps_edit.setPlainText("1. do thing")
        dlg.send_btn.click()
        # staged, not sent: the composed report sits in the input row
        # with an honest status-line explanation; no new user bubble
        staged = p.input_edit.toPlainText()
        assert "What happened: second problem" in staged, staged
        assert "busy" in p.status_line.text().lower(), p.status_line.text()
        labels = [l.text()
                  for l in p._transcript_body.findChildren(QtWidgets.QLabel)]
        assert not any("second problem" in t for t in labels), labels
    finally:
        p._session.busy = False
    p.deleteLater()


def test_help_dialog_cancel_no_send():
    qt_app()
    from maya_tools.framework.toolbar.reggie import keys, panel
    os.environ[keys.ENV_VAR] = "sk-ant-test-abcd1234"
    p = panel.ReggiePanel(bridge_autostart=False, spawn_threads=False)

    p.help_btn.click()
    dlg = p._help_dialog
    dlg.what_edit.setText("rig popped off origin")
    dlg.steps_edit.setPlainText("1. open scene\n2. press Build Rig")
    dlg.cancel_btn.click()

    from PySide6 import QtWidgets
    labels = [l.text() for l in p._transcript_body.findChildren(QtWidgets.QLabel)]
    # only the welcome bubble is present — nothing was sent
    assert len(labels) == 1, labels
    assert labels[0].startswith("Hello, I'm Reggie"), labels[0]
    assert p.send_btn.isEnabled()
    p.deleteLater()


def test_splitter_recovers_input_row_after_refresh():
    """Change 5 + setup_card interaction: input_row is a splitter pane
    that gets hidden when there's no key (setup_card, outside the
    splitter, takes over). Must not leave a stuck/zero-height pane
    once a key arrives via Refresh."""
    qt_app()
    from maya_tools.framework.toolbar.reggie import keys, panel
    saved = os.environ.pop(keys.ENV_VAR, None)
    try:
        p = panel.ReggiePanel(bridge_autostart=False)
        p.resize(520, 640)
        p.show()
        _flush_qt()
        assert p.setup_card.isVisibleTo(p)
        assert not p.input_row.isVisibleTo(p)
        assert p.splitter.sizes()[1] == 0

        os.environ[keys.ENV_VAR] = "sk-ant-test-abcd1234"
        p._refresh_key_state()
        _flush_qt()
        assert not p.setup_card.isVisibleTo(p)
        assert p.input_row.isVisibleTo(p)
        assert p.splitter.sizes()[1] >= p.input_row.minimumHeight()
        assert p.input_edit.isVisibleTo(p)
        assert p.send_btn.isVisibleTo(p)
        p.hide()
        p.deleteLater()
    finally:
        if saved is not None:
            os.environ[keys.ENV_VAR] = saved
        else:
            os.environ.pop(keys.ENV_VAR, None)


def test_banner_has_avatar_and_name():
    qt_app()
    from maya_tools.framework.toolbar.reggie import panel
    p = panel.ReggiePanel(bridge_autostart=False)
    assert hasattr(p, "avatar_label")
    pix = p.avatar_label.pixmap()
    assert pix is not None and not pix.isNull(), "avatar_label must carry a real pixmap"
    assert hasattr(p, "name_label")
    assert p.name_label.text() == "Reggie"
    # New Chat / Chat Form moved OUT of the composer, into the banner -
    # both still reachable under their old attribute names.
    assert hasattr(p, "new_chat_btn")
    assert hasattr(p, "help_btn")
    # they are NOT children of input_row anymore ...
    from PySide6 import QtWidgets
    input_row_buttons = p.input_row.findChildren(QtWidgets.QPushButton)
    assert p.new_chat_btn not in input_row_buttons, input_row_buttons
    assert p.help_btn not in input_row_buttons, input_row_buttons
    # ... but they ARE children of the banner.
    banner_buttons = p.banner.findChildren(QtWidgets.QPushButton)
    assert p.new_chat_btn in banner_buttons, banner_buttons
    assert p.help_btn in banner_buttons, banner_buttons
    p.deleteLater()


def test_status_dot_hovercard_configure():
    qt_app()
    from maya_tools.framework.toolbar import popovers
    from maya_tools.framework.toolbar.reggie import keys, panel
    saved = os.environ.pop(keys.ENV_VAR, None)
    calls = []
    orig = popovers.open_connect_ai_window
    popovers.open_connect_ai_window = lambda: calls.append(True)
    try:
        # no key -> warn state -> the card explains there's no key yet.
        p = panel.ReggiePanel(bridge_autostart=False)
        card = p._show_key_hovercard()
        assert card is p._key_hovercard
        assert "no anthropic api key" in card.message_label.text().lower()
        assert hasattr(card, "configure_btn")
        card.configure_btn.click()
        assert calls == [True], "Configure... must call popovers.open_connect_ai_window()"

        # key arrives -> ok state -> the card's text flips accordingly on
        # the next show (refresh_text reads the dot's live 'state' prop).
        os.environ[keys.ENV_VAR] = "sk-ant-test-abcd1234"
        p._refresh_key_state()
        card2 = p._show_key_hovercard()
        assert card2 is card, "hover-card is built once and reused"
        assert "connected" in card2.message_label.text().lower()
        assert "no anthropic api key" not in card2.message_label.text().lower()
        p.deleteLater()
    finally:
        popovers.open_connect_ai_window = orig
        if saved is not None:
            os.environ[keys.ENV_VAR] = saved
        else:
            os.environ.pop(keys.ENV_VAR, None)


def test_key_hovercard_teardown_no_dangling_widget():
    """Hover-card is a frameless Qt.Tool top-level parented to the panel.
    Panel teardown must take it down too - the prior teardown-traceback
    bug was exactly a live top-level widget outliving its owner."""
    qt_app()
    from PySide6 import QtCore
    from shiboken6 import isValid
    from maya_tools.framework.toolbar.reggie import keys, panel
    saved = os.environ.pop(keys.ENV_VAR, None)
    try:
        before = set(id(w) for w in _APP.topLevelWidgets())
        p = panel.ReggiePanel(bridge_autostart=False)
        card = p._show_key_hovercard()
        _flush_qt()
        assert isValid(card) and card.isVisible()
        assert card in _APP.topLevelWidgets()

        p.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(
            p, QtCore.QEvent.Type.DeferredDelete)   # force drain; a bare
        _flush_qt()                                  # processEvents() does
        #                                              not reliably drain
        #                                              DeferredDelete here
        assert not isValid(card), (
            "hover-card must be torn down WITH the panel, not left "
            "dangling as an orphan top-level Tool window")
        assert set(id(w) for w in _APP.topLevelWidgets()) <= before, (
            "a top-level widget leaked past panel teardown")
    finally:
        if saved is not None:
            os.environ[keys.ENV_VAR] = saved


def test_key_hovercard_grace_timer_survives_teardown():
    """The 250ms hide-grace QTimer's queued timeout must never crash if it
    lands on a Python wrapper whose C++ object is already gone -
    _on_hide_timeout's isValid(self) guard is what prevents that."""
    qt_app()
    from PySide6 import QtCore
    from shiboken6 import isValid
    from maya_tools.framework.toolbar.reggie import keys, panel
    saved = os.environ.pop(keys.ENV_VAR, None)
    try:
        p = panel.ReggiePanel(bridge_autostart=False)
        card = p._show_key_hovercard()
        _flush_qt()
        card.arm_hide()

        p.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(
            p, QtCore.QEvent.Type.DeferredDelete)
        _flush_qt()
        assert not isValid(card)

        card._on_hide_timeout()   # must not raise
    finally:
        if saved is not None:
            os.environ[keys.ENV_VAR] = saved


def main():
    check("test_split_blocks_pure", test_split_blocks_pure)
    check("test_panel_setup_card_when_no_key", test_panel_setup_card_when_no_key)
    check("test_panel_chat_ui_when_key_present", test_panel_chat_ui_when_key_present)
    check("test_key_dot_reflects_state", test_key_dot_reflects_state)
    check("test_panel_single_flight_disables_send",
          test_panel_single_flight_disables_send)
    check("test_full_turn_renders_transcript_with_code_block",
          test_full_turn_renders_transcript_with_code_block)
    check("test_panel_is_mindmeld_styled", test_panel_is_mindmeld_styled)
    check("test_welcome_bubble_shown_on_open", test_welcome_bubble_shown_on_open)
    check("test_welcome_bubble_returns_after_new_chat",
          test_welcome_bubble_returns_after_new_chat)
    check("test_help_dialog_composes_and_sends",
          test_help_dialog_composes_and_sends)
    check("test_help_dialog_preserves_input_draft",
          test_help_dialog_preserves_input_draft)
    check("test_help_dialog_busy_stages_instead_of_sending",
          test_help_dialog_busy_stages_instead_of_sending)
    check("test_help_dialog_cancel_no_send",
          test_help_dialog_cancel_no_send)
    check("test_splitter_recovers_input_row_after_refresh",
          test_splitter_recovers_input_row_after_refresh)
    check("test_banner_has_avatar_and_name", test_banner_has_avatar_and_name)
    check("test_status_dot_hovercard_configure",
          test_status_dot_hovercard_configure)
    check("test_key_hovercard_teardown_no_dangling_widget",
          test_key_hovercard_teardown_no_dangling_widget)
    check("test_key_hovercard_grace_timer_survives_teardown",
          test_key_hovercard_grace_timer_survives_teardown)

    if FAILURES:
        print(f"TESTS: {len(FAILURES)} FAILED")
        sys.exit(1)
    print("TESTS: OK")


if __name__ == "__main__":
    main()
