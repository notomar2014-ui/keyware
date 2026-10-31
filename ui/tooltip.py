"""A small hover tooltip for Tk widgets, and the status dot's text."""

from __future__ import annotations


def dot_tooltip(on: bool, paused: bool) -> str:
    if on and paused:
        return "Keyware is PAUSED - click to turn off"
    if on:
        return "Keyware is ON - click to turn off"
    return "Keyware is OFF - click to turn on"


class Tooltip:
    """Shows ``text`` in a borderless box after a short hover. Never takes focus."""

    def __init__(self, widget, text: str = "", delay_ms: int = 400):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text
        if self._tip is not None:
            try:
                self._label.configure(text=text)
            except Exception:
                pass

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after = self.widget.after(self.delay_ms, self.show)

    def _cancel(self) -> None:
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    def show(self) -> None:
        import tkinter as tk

        self._after = None
        if self._tip is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            tip.attributes("-topmost", True)
            self._label = tk.Label(
                tip, text=self.text, bg="#18181b", fg="#f4f4f5", padx=8, pady=4,
                font=("Segoe UI", 9), relief="solid", borderwidth=1,
            )
            self._label.pack()
            tip.wm_geometry(f"+{x}+{y}")
            self._tip = tip
        except Exception:
            self._tip = None

    def hide(self, _event=None) -> None:
        self._cancel()
        tip, self._tip = self._tip, None
        if tip is not None:
            try:
                tip.destroy()
            except Exception:
                pass
