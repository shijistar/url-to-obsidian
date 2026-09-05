"""pytest configuration — ensure the plugin package is importable."""

import sys
from pathlib import Path

# Add plugin/ (this directory's parent) to sys.path so that `import
# web_to_obsidian` resolves to plugin/web_to_obsidian.py. Tests live next to
# the plugin package, so parents[1] IS the plugin directory.
_plugin_dir = str(Path(__file__).resolve().parents[1])
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)
