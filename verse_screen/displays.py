"""Find connected screens on any OS."""
import sys
from dataclasses import dataclass


def enable_windows_dpi_awareness():
    """Without this, Windows display scaling puts the projector window in the wrong place."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


@dataclass(frozen=True)
class Display:
    x: int
    y: int
    width: int
    height: int
    primary: bool

    @property
    def label(self):
        return f"{'Laptop screen' if self.primary else 'External screen'} {self.width}x{self.height} at {self.x},{self.y}"

    @property
    def geometry(self):
        return f"{self.width}x{self.height}{self.x:+d}{self.y:+d}"


def list_displays(root):
    try:
        from screeninfo import get_monitors

        monitors = get_monitors()
        if monitors:
            found = [Display(m.x, m.y, m.width, m.height, bool(m.is_primary)) for m in monitors]
            if not any(d.primary for d in found):
                first = found[0]
                found[0] = Display(first.x, first.y, first.width, first.height, True)
            return found
    except Exception:
        pass
    return [Display(0, 0, root.winfo_screenwidth(), root.winfo_screenheight(), True)]
