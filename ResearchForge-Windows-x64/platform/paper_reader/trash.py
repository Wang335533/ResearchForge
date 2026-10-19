"""Send files the user deletes to the system recycle bin, never erase them outright.

Windows: the shell's SHFileOperationW with FOF_ALLOWUNDO (the same as deleting in Explorer). Elsewhere, or
if the shell refuses, files move into the data folder's "trash" directory so they stay recoverable.
"""
import ctypes
import os
import shutil
import time
from ctypes import wintypes
from pathlib import Path

if __package__:
    from .configuration import DATA
else:
    from configuration import DATA


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [('hwnd', wintypes.HWND), ('wFunc', wintypes.UINT), ('pFrom', wintypes.LPCWSTR), ('pTo', wintypes.LPCWSTR),
                ('fFlags', ctypes.c_uint16), ('fAnyOperationsAborted', wintypes.BOOL), ('hNameMappings', ctypes.c_void_p),
                ('lpszProgressTitle', wintypes.LPCWSTR)]


def _recycle_windows(path):
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = _SHFILEOPSTRUCTW(None, FO_DELETE, str(path) + '\0', None, FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted


def recycle(paths):
    """Recycle each existing file or folder; returns the ones that could not be moved anywhere."""
    failed = []
    for raw in paths:
        path = Path(raw).resolve()
        if not path.exists() or path.is_symlink():
            continue
        try:
            if os.name == 'nt' and _recycle_windows(path) and not path.exists():
                continue
            target = DATA / 'trash' / (time.strftime('%Y%m%d-%H%M%S-') + path.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
        except OSError:
            failed.append(str(path))
    return failed
