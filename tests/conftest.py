"""pytest configuration — ensure the plugin package is importable."""

import sys
from pathlib import Path

# Add plugin/ to sys.path so that `import web_to_obsidian` resolves to
# plugin/web_to_obsidian.py (the tests were originally written against the
# root-level module before the restructure).
_plugin_dir = str(Path(__file__).resolve().parents[1] / "plugin")
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)
