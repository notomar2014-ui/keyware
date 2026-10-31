"""Create Desktop and Start Menu shortcuts for Keyware.exe (Windows only).

The shortcuts carry Keyware's icon and the same AppUserModelID that main()
sets, so a pinned taskbar button and the running window share one button.

    python tools/make_shortcuts.py [path\\to\\Keyware.exe]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ID = "Keyware.Keyware"


def make(link: Path, target: Path, icon: Path) -> None:
    import pythoncom
    from win32com.propsys import propsys, pscon
    from win32com.shell import shell

    item = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
    item.SetPath(str(target))
    item.SetWorkingDirectory(str(target.parent))
    item.SetIconLocation(str(icon), 0)
    item.SetDescription("Keyware key remapper")
    store = item.QueryInterface(propsys.IID_IPropertyStore)
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID))
    store.Commit()
    link.parent.mkdir(parents=True, exist_ok=True)
    item.QueryInterface(pythoncom.IID_IPersistFile).Save(str(link), 0)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / "dist" / "Keyware.exe"
    icon = root / "ui" / "assets" / "keyware.ico"
    from win32com.shell import shell, shellcon

    desktop = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0))
    start = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    for link in (desktop / "Keyware.lnk", start / "Keyware.lnk"):
        make(link, target, icon)
        print(link)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
