"""Hermes registration for the standalone web-to-Obsidian plugin."""

from pathlib import Path

from .web_to_obsidian import build_handler, build_resume_tool


_RESUME_PENDING_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["yes", "no"],
            "description": "yes to download remote article images into the vault; no to keep remote image URLs.",
        }
    },
    "required": ["decision"],
    "additionalProperties": False,
}


def register(ctx) -> None:
    plugin_root = Path(__file__).resolve().parent
    handler = build_handler(plugin_root)
    ctx.register_command(
        "clip",
        handler=handler,
        description="Clip a public web article into an Obsidian vault with guarded Git sync.",
        args_hint="<url> [--refresh] [--no-browser] [--no-git] [--save-images yes|no|ask]",
    )
    ctx.register_tool(
        name="web_to_obsidian_resume_pending",
        toolset="web_to_obsidian",
        schema=_RESUME_PENDING_SCHEMA,
        handler=build_resume_tool(plugin_root),
        emoji="🖼️",
    )
