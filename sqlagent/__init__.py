"""SQL Calculation Agent: an agent on a local Ollama model (Agentic SWE, Part 1, Assignment 1)."""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(PROJECT_ROOT, "data", "shop.db")
DEFAULT_MODEL = "qwen2.5:3b"


def safe_console():
    """Model answers can contain any Unicode. On Windows, stdout redirected to a file uses
    the legacy code page and would crash on such characters, and it is block-buffered
    so progress lines only appear at the end. Make it UTF-8 and line-buffered."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass
