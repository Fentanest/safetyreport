import os
import sys

is_frozen = getattr(sys, 'frozen', False)

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    if is_frozen:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        # For directory mode, the EXE sits in the distribution root, and _MEIPASS points to distribution root
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    else:
        # In dev mode, find the project root relative to this file (core/utils/path_utils.py)
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    return os.path.abspath(os.path.join(base_path, relative_path))

def enforce_utf8():
    """ Enforce UTF-8 encoding for stdout and stderr on Windows """
    if sys.platform == "win32":
        import io
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        else:
            # Fallback for very old Python versions if needed, though 3.7+ is assumed
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
