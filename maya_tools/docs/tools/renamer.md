---
title: Name Tools
summary: <b>Sequential Renamer</b><br>Each # becomes a zero-padded number, so joint_## renames the selection to joint_01, joint_02... in selection order.<br><br><b>Search / Replace</b><br>Finds text in the selected names and swaps it for your replacement.<br><br><b>Prefix / Suffix</b><br>Adds or strips a prefix or suffix on every selected name.<br><br><b>Select by Name</b><br>Type a wildcard like *_jnt and press Enter to select every match in the scene.
category: framework
gif: ../media/renamer.gif
video:
---

# Renamer

## What it does

Renamer batch-renames the current Maya selection using one of four operations: hash-pattern renaming (`#` as a zero-padded digit placeholder, so `joint_##` becomes `joint_01`, `joint_02`...), find/replace on short names, prefix/suffix add or remove, and renumber, which strips a trailing separator-plus-digits from each name and resequences the selection in order. Every operation runs inside a single Maya undo chunk, so a whole batch reverts with one Ctrl+Z. The tool doesn't enforce a naming convention on its own; it's the manual way to get objects into whatever convention your pipeline expects (the code's own placeholder text uses `L_finger_###_jnt` as an example) before you move on to rigging or export.

## Quick start

1. Select the objects to rename. For Hash and Renumber, numbering follows Maya's selection order, not Outliner order.
2. Open the tool. Two ways in:
   - Maya menu: **FabricatorStudio > Scene & Selection > Renamer**, then pick Hash Renamer, Find / Replace, Prefix / Suffix, or Renumber. Each entry opens the Renamer window straight to that tab.
   - Bridge toolbar: click the compact **REN** widget docked in the toolbar's left zone (next to the project chooser).
3. Fill in the fields for that mode.
   - In the window: type your values and click **Rename** (or press Enter in the Hash or Find field).
   - In the toolbar strip: type in the field and press Enter. There is no Rename button here.
4. Check the result in the viewport or Outliner. In the window, expand the collapsible **// LOG** section for a one-line success or error report.

## Workflow

Renamer ships as two separate surfaces that currently coexist in the codebase:

- **The Renamer window** (`renamer_ui.py`, class `FSRenamerUI`) is a tabbed dialog: HASH, FIND / REPLACE, PREFIX / SUFFIX, RENUMBER, plus Rename/Cancel buttons and a collapsible LOG section. It's reached from the Maya main menu under **FabricatorStudio > Scene & Selection > Renamer**, and each submenu item opens the window directly on the matching tab.
- **The Bridge toolbar strip** has its own inline widget (`renamer_inline.py`, `RenamerInline`, toolbar id `renamer`, glyph `REN`) sitting in the toolbar's left zone. Clicking the mode button (glyph shows `##`, `a>b`, `a+`, or `*?`) opens a four-way mode menu: **Hash Rename**, **Search / Replace**, **Prefix / Suffix**, and **Select by Name**. There's no Rename button anywhere on the strip; you press Enter in the field to apply. The field area is compact at rest and expands on hover or focus, then contracts when idle.

Two differences to know about between the two surfaces:
- The toolbar's **Prefix / Suffix** mode only adds a prefix or suffix. To remove one from the toolbar, use **Search / Replace** instead (put the text to strip in Find and leave Replace empty). The window's Prefix/Suffix tab supports both Add and Remove.
- The toolbar has no **Renumber** mode. Renumber is only available from the Renamer window's RENUMBER tab. In its place, the toolbar has **Select by Name**, which is not a rename at all: it runs a wildcard `cmds.ls()` select (space-separated patterns union, so `*_l *_r` selects both sides).

A source comment in `renamer_inline.py` states that "the full Renamer window and its popover are RETIRED: this strip is the whole tool," but the Maya menu (`fs_menu.json`) still wires all four window entries live as of this read. Both paths work today; see the accuracy flag below.

## Gotchas

- Hash and Renumber both key off Maya's selection order, not Outliner order. Reordering how you click-select changes the resulting numbers.
- An empty Hash pattern raises "Name pattern is empty."; a pattern with no `#` in it raises "Pattern must contain at least one '#' character."
- Find/Replace, Add Prefix, and Add Suffix all raise a "field is empty" error if you leave the corresponding text field blank.
- Remove Prefix/Remove Suffix (window only) don't hard-fail on a bad match: any selected object whose name doesn't actually start/end with the given text is skipped with a warning, not renamed, and the batch continues. An object is also skipped if stripping the prefix/suffix would leave an empty name.
- Renumber strips a trailing `<separator><digits>` match using the exact separator you typed. If your names use a different separator than what you enter, the whole name is treated as the base and a new number is appended rather than replacing an old one.
- Hash renaming applies to selection order but executes the actual `cmds.rename` calls deepest-path-first, specifically so renaming a parent never invalidates a child's already-computed long path.
- None of the four operations pre-check for name collisions. If a desired name already exists on another node, Maya's own automatic uniquify suffix (e.g. `name1`) is what you'll see in the result, not an error.
- Every mode requires an active selection; running any of them with nothing selected raises "Nothing selected." (or, for Renumber, "Nothing to renumber - list is empty.").
- A standalone `HashRenamerUI` dialog also exists in `hash_renamer_ui.py` (window title "Hash Renamer"), but it is not wired into the Maya menu or the Bridge toolbar anywhere in the code read for this doc; treat it as not currently reachable by a normal user.

## Troubleshooting

**"Nothing selected."** No objects were selected when the rename ran. Select the target objects first, then try again.

**"Name pattern is empty."** The Hash pattern field was left blank. Type a pattern, e.g. `joint_##`, before pressing Rename or Enter.

**"Pattern must contain at least one '#' character."** The Hash pattern has no digit placeholder. Add at least one `#` where the number should go.

**"Find string is empty."** Find/Replace ran with nothing in the Find field. Fill in Find; Replace can stay empty if you want to delete the matched text.

**"Prefix is empty." / "Suffix is empty."** Add or Remove Prefix/Suffix ran with no text typed in. Enter the prefix or suffix text first.

**"<name>" does not start with "<prefix>" - skipped. / does not end with "<suffix>" - skipped.** (window's Remove mode) The selected object's short name doesn't contain that exact substring. Double-check spelling and case; the match is an exact substring match, not case-insensitive.

**"<name>" would be empty after stripping - skipped.** Removing that prefix/suffix would leave nothing behind. The tool keeps the original name rather than rename to blank; use Find/Replace if you actually want to replace the whole name.

**"Nothing to renumber - list is empty."** Renumber ran with no selection. Select the objects to resequence and run it again.

**Typing in the Bridge toolbar strip does nothing visible.** There's no Rename button on the strip; press Enter in the field to apply the change.

**Toolbar's Prefix/Suffix mode won't remove text.** That mode is add-only by design. Switch to Search/Replace mode and put the prefix or suffix text in Find with Replace left empty.

**Renumber isn't on the toolbar strip.** It isn't available there. Open the Renamer window from **FabricatorStudio > Scene & Selection > Renamer > Renumber** instead.
