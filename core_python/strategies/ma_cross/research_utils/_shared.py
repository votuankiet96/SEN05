"""Display primitives dùng chung cho research notebooks MA Cross."""

from __future__ import annotations

from typing import Any

try:
    from IPython.display import Markdown, display
except Exception:  # pragma: no cover - console fallback
    Markdown = None  # type: ignore[assignment]

    def display(obj: Any) -> None:  # type: ignore[misc]
        print(obj)


def show_note(text: str) -> None:
    """Render markdown text trong Jupyter; fallback sang print ngoài notebook."""
    if Markdown is None:
        print(text)
    else:
        display(Markdown(text))


__all__ = ["show_note"]
