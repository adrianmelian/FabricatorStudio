"""Toolset auto-updater — core logic, no Qt. See updater_ui for the face.

One channel, one manifest: the publish script writes toolset-latest.json
(version, url, sha256, size, date) beside the release artifacts at cut time,
Cache-Control no-cache. The url points at the IMMUTABLE versioned archive,
never the overwritten stable key, so the manifest hash and the served bytes
can never disagree. A future beta ring is a second manifest plus a Settings
toggle; the URL lives in ONE constant on purpose.

Hard rules (the spec's refusal guards):
  * Never offer, let alone install, on anything but a wired COPY install
    with a parseable VERSION.txt. A source checkout (Adrian's dev machine
    runs the depot via a 'linked' userSetup block) and a network-share
    'linked' install are both structurally out of scope.
  * The downloaded zip's sha256 must match the manifest before ANY file is
    touched. We ship code that runs inside Maya; no hash, no install.
  * The current maya_tools tree is backed up first and restored on any
    install failure. The Ribbon Pack overlay and project configs survive by
    the installer's own preserve/restore (install_payload), which this
    module reuses from the DOWNLOADED zip — one installer truth, and it is
    the NEW version's installer that runs.
"""
__author__ = "Adrian Melian"

import hashlib
import importlib.util
import json
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile

MANIFEST_URL = "https://downloads.fabricator.studio/dl/7766c9bad233637b/toolset-latest.json"
CHANGELOG_URL = "https://fabricator.studio/changelog"

_TIMEOUT_S = 3.0
_DOWNLOAD_TIMEOUT_S = 300.0
# The R2 host 403s urllib's default UA (measured on the 1.2.0 verification).
_USER_AGENT = "FabricatorUpdater/1"

_PREF_KEY = "update_check_on_startup"

# Same sentinel grammar Fabricator_Install.py writes; the installer module
# itself is not shipped inside maya_tools, so the two regexes are restated
# here (kept deliberately identical).
_SENTINEL_START = "# FABRICATOR_START"
_REPO_ROOT_RE = re.compile(r"_FAB_REPO_ROOT\s*=\s*r?['\"](.+?)['\"]")
_MODE_RE = re.compile(r"#\s*FABRICATOR_MODE:\s*(\w+)")
_VERSION_LINE_RE = re.compile(r"^\s*Fabricator\s+([0-9][^\s]*)\s*$")


class UpdateError(RuntimeError):
    """A user-facing updater failure. The install is left as it was."""


# ─────────────────────────────────────────────────────────────────────────────
# Preferences
# ─────────────────────────────────────────────────────────────────────────────

def check_on_startup_enabled() -> bool:
    from maya_tools.framework.toolbar import toolbar_prefs
    return bool(toolbar_prefs.load_prefs().get(_PREF_KEY, True))


def set_check_on_startup(enabled: bool) -> None:
    from maya_tools.framework.toolbar import toolbar_prefs
    prefs = toolbar_prefs.load_prefs()
    prefs[_PREF_KEY] = bool(enabled)
    toolbar_prefs.save_prefs(prefs)


# ─────────────────────────────────────────────────────────────────────────────
# Install detection (the refusal guards)
# ─────────────────────────────────────────────────────────────────────────────

def _user_setup_path() -> str:
    # Mirror of the installer's get_maya_root()/scripts/userSetup.py without
    # importing Maya: MAYA_APP_DIR wins, else the Documents/maya default.
    root = os.environ.get("MAYA_APP_DIR", "")
    if not root:
        root = os.path.join(os.path.expanduser("~"), "maya")
    return os.path.join(root, "scripts", "userSetup.py")


def _installed_version(payload_root: str) -> str:
    try:
        with open(os.path.join(payload_root, "VERSION.txt"),
                  "r", encoding="utf-8") as fh:
            m = _VERSION_LINE_RE.match(fh.readline())
        return m.group(1) if m else ""
    except OSError:
        return ""


def detect_install(user_setup_path: str = "") -> dict:
    """{'ok': bool, 'reason': str, 'root': str, 'mode': str, 'version': str}

    ok=True means this machine is a wired COPY install this updater may
    replace. Every False carries a plain-words reason for Settings."""
    path = user_setup_path or _user_setup_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {"ok": False, "root": "", "mode": "", "version": "",
                "reason": "No Fabricator userSetup wiring found."}

    if _SENTINEL_START not in text:
        return {"ok": False, "root": "", "mode": "", "version": "",
                "reason": "No Fabricator userSetup wiring found."}
    m = _REPO_ROOT_RE.search(text)
    if not m:
        return {"ok": False, "root": "", "mode": "", "version": "",
                "reason": "The userSetup wiring is present but unreadable."}
    root = os.path.normpath(m.group(1))
    mode_m = _MODE_RE.search(text)
    mode = mode_m.group(1) if mode_m else "copy"

    if mode != "copy":
        return {"ok": False, "root": root, "mode": mode, "version": "",
                "reason": "This install is linked to a shared or development "
                          "location; the updater only manages copied "
                          "installs."}
    for probe in (root, os.path.dirname(root)):
        if os.path.isdir(os.path.join(probe, ".git")):
            return {"ok": False, "root": root, "mode": mode, "version": "",
                    "reason": "This install is a source checkout; the "
                              "updater will not touch it."}
    version = _installed_version(root)
    if not version:
        return {"ok": False, "root": root, "mode": mode, "version": "",
                "reason": "No VERSION.txt at the install root (development "
                          "build); the updater stays out of the way."}
    if not os.path.isdir(os.path.join(root, "maya_tools")):
        return {"ok": False, "root": root, "mode": mode, "version": version,
                "reason": "The wired install root has no maya_tools tree."}
    return {"ok": True, "root": root, "mode": mode, "version": version,
            "reason": ""}


# ─────────────────────────────────────────────────────────────────────────────
# Manifest + version compare
# ─────────────────────────────────────────────────────────────────────────────

def fetch_manifest(url: str = "", timeout: float = _TIMEOUT_S):
    """The parsed manifest dict, or None on ANY failure — offline is a
    normal state, never a warning."""
    req = urllib.request.Request(url or MANIFEST_URL,
                                 headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("version") \
            or not data.get("url") or not data.get("sha256"):
        return None
    return data


def parse_version(text: str) -> tuple:
    parts = []
    for chunk in str(text).split("."):
        digits = re.match(r"\d+", chunk)
        parts.append(int(digits.group(0)) if digits else 0)
    return tuple(parts) if parts else (0,)


def is_newer(candidate: str, installed: str) -> bool:
    return parse_version(candidate) > parse_version(installed)


def check_for_update(manifest_url: str = "", user_setup_path: str = ""):
    """(offer, status): offer is a dict when a newer version is available on
    an updatable install, else None. status is a plain sentence either way."""
    install = detect_install(user_setup_path)
    if not install["ok"]:
        return None, install["reason"]
    manifest = fetch_manifest(manifest_url)
    if manifest is None:
        return None, "Could not reach the update server (offline is fine)."
    if not is_newer(manifest["version"], install["version"]):
        return None, ("Up to date: Fabricator %s." % install["version"])
    offer = dict(manifest)
    offer["installed"] = install["version"]
    offer["root"] = install["root"]
    return offer, ("Fabricator %s is available (installed: %s)."
                   % (manifest["version"], install["version"]))


# ─────────────────────────────────────────────────────────────────────────────
# Download + verify + install (+ rollback)
# ─────────────────────────────────────────────────────────────────────────────

def _download(url: str, dest_path: str, expected_sha256: str,
              progress_cb=None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    digest = hashlib.sha256()
    read = 0
    try:
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_S) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            with open(dest_path, "wb") as out:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    read += len(chunk)
                    if progress_cb and total:
                        progress_cb(read, total)
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError("Download failed: %s" % exc)

    if digest.hexdigest().lower() != str(expected_sha256).lower():
        try:
            os.remove(dest_path)
        except OSError:
            pass
        raise UpdateError(
            "The downloaded file does not match the published hash. "
            "Nothing was installed. Try again later; if it keeps happening, "
            "download from fabricator.studio instead.")


def _load_downloaded_installer(dist_dir: str):
    installer_path = os.path.join(dist_dir, "Fabricator_Install.py")
    if not os.path.isfile(installer_path):
        raise UpdateError("The update package has no installer inside it.")
    spec = importlib.util.spec_from_file_location(
        "_fabricator_downloaded_installer", installer_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def download_and_install(offer: dict, log=print, progress_cb=None) -> str:
    """Run the full one-click flow against `offer` from check_for_update().
    Returns the new version string. Raises UpdateError with the install
    left exactly as found."""
    root = offer["root"]
    dest_maya_tools = os.path.join(root, "maya_tools")
    backup = os.path.join(root, "maya_tools.backup")

    staging = tempfile.mkdtemp(prefix="fabricator_update_")
    zip_path = os.path.join(staging, "Fabricator.zip")
    try:
        log("Downloading Fabricator %s..." % offer["version"])
        _download(offer["url"], zip_path, offer["sha256"], progress_cb)
        log("Hash verified.")

        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(os.path.join(staging, "x"))
        x_root = os.path.join(staging, "x")
        dists = [d for d in os.listdir(x_root)
                 if os.path.isdir(os.path.join(x_root, d, "Fabricator_Data"))]
        if len(dists) != 1:
            raise UpdateError("The update package layout is not recognized.")
        dist_dir = os.path.join(x_root, dists[0])
        payload_dir = os.path.join(dist_dir, "Fabricator_Data")

        installer = _load_downloaded_installer(dist_dir)

        if os.path.isdir(backup):
            shutil.rmtree(backup)
        log("Backing up the current install...")
        shutil.copytree(dest_maya_tools, backup)

        try:
            log("Installing...")
            installer.install_payload(
                scripts_dir=root,
                icons_dir=os.path.join(root, "icons"),
                payload_dir=payload_dir,
            )
        except Exception as exc:
            log("Install failed; restoring the previous version...")
            if os.path.isdir(dest_maya_tools):
                shutil.rmtree(dest_maya_tools)
            shutil.copytree(backup, dest_maya_tools)
            raise UpdateError(
                "Install failed and the previous version was restored: %s"
                % exc)

        new_version = _installed_version(root) or offer["version"]
        log("Updated to Fabricator %s." % new_version)
        return new_version
    finally:
        shutil.rmtree(staging, ignore_errors=True)
