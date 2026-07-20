# Python/_vendor

Third-party Python packages vendored for Maya 2025 (Python 3.11).

Pure-Python only — no compiled extensions (.pyd) since Maya's bundled Python is fixed at 3.11 and binary wheels for other versions won't load.

## Setup

`userSetup.py` (in your Maya scripts dir, outside this repo) must add this folder to `sys.path` before any tool imports a vendored library. Add this near the top of `userSetup.py`:

```python
import maya_tools
_AM_VENDOR = os.path.join(os.path.dirname(next(iter(maya_tools.__path__))), '_vendor')
if _AM_VENDOR not in sys.path:
    sys.path.insert(0, _AM_VENDOR)
```

`maya_tools` is a namespace package (no `__init__.py`), so importing it doesn't execute any code — it only resolves the package directory so we can derive its `_vendor` sibling.

## Vendored

- **yaml/** — PyYAML 6.0.1 (MIT license). Pure-Python implementation. Bundled C extension (`_yaml.cp*.pyd`) was stripped at vendor time; PyYAML falls back to pure Python automatically when the C extension is missing.
