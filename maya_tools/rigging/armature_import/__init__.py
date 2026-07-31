"""Armature import — build a Fabricator rig in Maya from an Armature USD export.

Two-file split per house convention: armature_import_app (functions, headless) and
armature_import_ui (the window). blueprint_source and classify are pure-data helpers
with no Maya dependency, so they test without a Maya session.
"""
__author__ = "Adrian Melian"
