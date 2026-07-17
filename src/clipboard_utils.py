"""
Cross-platform clipboard helpers (macOS / Linux path).

Windows keeps its own rich win32clipboard code at the call sites, because it
preserves every clipboard format (images, RTF, ...). This module provides the
non-Windows path: text-only clipboard access plus an OS-correct paste
keystroke. Text-only restore is an acceptable fallback on macOS/Linux.

NOTE (2026-07-17): written on Windows, UNTESTED on macOS. Must be verified on
the Mac. The paste keystroke and typing require macOS "Accessibility"
permission (Systemeinstellungen > Datenschutz & Sicherheit > Bedienungshilfen)
and microphone permission - without them nothing is typed and no error is
raised by macOS.
"""
import sys
import time

from pynput.keyboard import Controller, Key

IS_WINDOWS = sys.platform == 'win32'
IS_MAC = sys.platform == 'darwin'

# macOS pastes with Cmd+V; every other platform uses Ctrl+V.
PASTE_MODIFIER = Key.cmd if IS_MAC else Key.ctrl


def _pyperclip():
    """
    Import pyperclip lazily. This keeps the module importable on Windows even
    when pyperclip is not installed there - Windows never uses this path (it has
    its own win32clipboard code), so the dependency is only required on
    macOS/Linux, where these functions are actually called.
    """
    import pyperclip
    return pyperclip


def get_text():
    """Return the current clipboard text, or '' if empty/unavailable."""
    try:
        return _pyperclip().paste() or ''
    except Exception:
        return ''


def set_text(text):
    """Put text on the clipboard."""
    try:
        _pyperclip().copy(text)
    except Exception:
        pass


def send_paste(keyboard=None):
    """Send the OS paste shortcut (Cmd+V on macOS, Ctrl+V elsewhere)."""
    kb = keyboard or Controller()
    with kb.pressed(PASTE_MODIFIER):
        kb.press('v')
        kb.release('v')


def paste_preserving(text, keyboard=None, delay=0.1):
    """
    Put `text` on the clipboard, paste it, then restore the previous clipboard
    text. Text-only - no image/RTF preservation (fallback for macOS/Linux).
    """
    saved = get_text()
    set_text(text)
    time.sleep(delay)
    send_paste(keyboard)
    time.sleep(delay)
    set_text(saved)
