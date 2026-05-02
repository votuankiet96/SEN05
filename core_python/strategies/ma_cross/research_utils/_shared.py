"""Display primitives shared by MA Cross research notebooks."""

from __future__ import annotations

import html
import json
from contextlib import contextmanager
from typing import Any

try:
    from IPython.display import HTML, Markdown, display as _ipython_display
except Exception:  # pragma: no cover - console fallback
    HTML = Markdown = None  # type: ignore[assignment]

    def _ipython_display(obj: Any) -> None:  # type: ignore[misc]
        print(obj)


_ACTIVE_WIDGET_OUTPUT: Any | None = None


@contextmanager
def _widget_output_target(output: Any):
    """Route display helpers directly into a widget Output during callbacks."""
    global _ACTIVE_WIDGET_OUTPUT
    previous = _ACTIVE_WIDGET_OUTPUT
    _ACTIVE_WIDGET_OUTPUT = output
    try:
        yield
    finally:
        _ACTIVE_WIDGET_OUTPUT = previous


def _display_obj(obj: Any) -> None:
    target = _ACTIVE_WIDGET_OUTPUT
    if target is not None and hasattr(target, "append_display_data"):
        html_text = _object_to_widget_html(obj)
        if html_text is not None and HTML is not None:
            target.append_display_data(HTML(html_text))
        else:
            target.append_display_data(obj)
    else:
        _ipython_display(obj)


def _display_html(html_text: str) -> None:
    if HTML is None:
        print(html_text)
        return
    _display_obj(HTML(html_text))


def _show_plot(fig: Any) -> None:
    target = _ACTIVE_WIDGET_OUTPUT
    if target is not None and hasattr(target, "append_display_data"):
        html_text = _figure_to_widget_html(fig)
        if html_text is not None and HTML is not None:
            target.append_display_data(HTML(html_text))
        else:
            target.append_display_data(fig)
        try:
            import matplotlib.pyplot as plt

            plt.close(fig)
        except Exception:
            pass
    else:
        try:
            import matplotlib.pyplot as plt

            plt.show()
        except Exception:
            _ipython_display(fig)


def display(obj: Any) -> None:
    """Display rich output once, with explicit widget routing when active."""
    _display_obj(obj)


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _short_repr(value: Any) -> str:
    if isinstance(value, dict):
        return "{}" if not value else json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=True, default=str)
    if isinstance(value, tuple):
        return json.dumps(list(value), ensure_ascii=True, default=str)
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def show_note(text: str, body: str | None = None) -> None:
    """Render a compact English note in notebooks, with console fallback."""
    if body is None:
        if Markdown is None:
            print(text)
        else:
            _display_obj(Markdown(text))
        return
    if HTML is None:
        print(f"{text}\n{body}")
        return
    _display_html(f"""
    <div style="
        border:1px solid #30363D;
        border-left:4px solid #2563EB;
        border-radius:6px;
        padding:10px 14px;
        margin:10px 0 12px 0;
        background:#F8FAFC;
        color:#111827;
    ">
      <div style="font-size:17px;font-weight:700;margin-bottom:4px;">{_escape(text)}</div>
      <div style="font-size:13px;line-height:1.45;">{_escape(body)}</div>
    </div>
    """)


def style_table(frame: Any, *, monospace_value_col: str | None = None) -> Any:
    """Apply a small consistent table style when pandas Styler is available."""
    if not hasattr(frame, "style"):
        return frame
    styler = (
        frame.style
        .hide(axis="index")
        .set_properties(**{"text-align": "left", "white-space": "pre-wrap"})
        .set_table_styles([{"selector": "th", "props": [("text-align", "left")]}])
    )
    if monospace_value_col and monospace_value_col in frame.columns:
        col_idx = list(frame.columns).index(monospace_value_col) + 1
        styler = styler.set_table_styles(
            [{"selector": f"td:nth-child({col_idx})", "props": [("font-family", "Consolas, monospace")]}],
            overwrite=False,
        )
    return styler


def display_frame(frame: Any) -> None:
    """Display helper kept for a stable local import surface."""
    display(frame)


def _object_to_widget_html(obj: Any) -> str | None:
    try:
        if obj.__class__.__name__ == "Styler" and hasattr(obj, "to_html"):
            return _wrap_table_html(obj.to_html())
        try:
            import pandas as pd

            if isinstance(obj, pd.Series):
                return _wrap_table_html(obj.to_frame().to_html(border=0, classes="ma-cross-table"))
            if isinstance(obj, pd.DataFrame):
                return _wrap_table_html(obj.to_html(border=0, classes="ma-cross-table"))
        except Exception:
            return None
    except Exception:
        return None
    return None


def _wrap_table_html(table_html: str) -> str:
    return f"""
    <div style="overflow-x:auto;margin:10px 0 14px 0;">
      <style>
        .ma-cross-table {{
          border-collapse: collapse;
          width: auto;
          max-width: 100%;
          font-size: 13px;
          color: #111827;
        }}
        .ma-cross-table th {{
          background: #E5E7EB;
          color: #111827;
          font-weight: 700;
          padding: 7px 10px;
          text-align: left;
          border-bottom: 1px solid #D1D5DB;
        }}
        .ma-cross-table td {{
          padding: 7px 10px;
          border-bottom: 1px solid #F1F5F9;
          vertical-align: top;
        }}
        .ma-cross-table tbody tr:nth-child(even) td {{
          background: #F8FAFC;
        }}
      </style>
      {table_html}
    </div>
    """


def _figure_to_widget_html(fig: Any) -> str | None:
    try:
        import base64
        from io import BytesIO

        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
        data = base64.b64encode(buf.getvalue()).decode("ascii")
        return (
            '<div style="overflow-x:auto;margin:12px 0 18px 0;">'
            f'<img src="data:image/png;base64,{data}" '
            'style="max-width:100%;height:auto;border:1px solid #E5E7EB;border-radius:6px;" />'
            "</div>"
        )
    except Exception:
        return None


__all__ = [
    "display",
    "display_frame",
    "show_note",
    "style_table",
    "_display_html",
    "_display_obj",
    "_show_plot",
    "_short_repr",
    "_widget_output_target",
]
