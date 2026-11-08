"""StartmenuOpen - tray tool that opens the Windows 11 Start menu on the monitor
where the mouse cursor is, instead of always on the primary monitor.

Windows key (alone) with the mouse on a secondary monitor
                             -> Start opens on that monitor's taskbar
Windows key on the primary monitor, Win+<key> combinations
                             -> unchanged, handled by Windows as usual
Left-click on the tray icon  -> switch the feature on/off
Right-click                  -> menu (on/off, autostart, language, exit)

How it works: a low-level keyboard hook watches the Windows key. If it is
released without any other key having been pressed and the cursor is on a
secondary monitor, the hook masks the key release (an unassigned key, vkE8, is
injected before it, so Windows sees a "combination" and does not open Start)
and then clicks the Start button of the taskbar on that monitor. The cursor is
moved back immediately.

Run with "--open" to open Start once on the monitor under the cursor and exit
(e.g. for a mouse button mapped to a program).

UI texts live in lang/<code>.json; the Windows display language is used by
default and can be overridden from the tray menu.
Settings (on/off, language) are stored in HKCU\\Software\\StartmenuOpen.
Standard library only (ctypes), no third-party runtime dependencies.
"""

import ctypes
import glob
import json
import os
import sys
import winreg
from ctypes import wintypes

__version__ = "1.0.0"

APP_NAME = "StartmenuOpen"
APP_ICON_FILE = "startmenu_open.ico"                 # program icon (exe)
TRAY_ICONS = {
    # (light taskbar, enabled): filename
    (False, True): "startmenu_open_light.ico",       # white glyph for a dark taskbar
    (False, False): "startmenu_open_light_paused.ico",
    (True, True): "startmenu_open_dark.ico",         # dark glyph for a light taskbar
    (True, False): "startmenu_open_dark_paused.ico",
}
LANG_DIR = "lang"
FALLBACK_LANGUAGE = "en"

# Processes that host the Start menu (it moved to SearchHost.exe in newer builds).
# If one of them is in the foreground, Start is open -> let Windows close it.
START_HOST_PROCESSES = {"searchhost.exe", "startmenuexperiencehost.exe"}
TASKBAR_CLASSES = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")
START_BUTTON_CLASS = "Start"   # hidden child window that tracks the Start button position

MASK_VK = 0xE8                 # unassigned virtual key, used to mask the Win key release
INJECTED_MARKER = 0x534D4F50   # dwExtraInfo of our own input ("SMOP"), ignored by the hook
CURSOR_RESTORE_MS = 60         # exact cursor restore after the click
HOOK_REFRESH_MS = 5 * 60 * 1000  # re-install the hook periodically (Windows may drop slow hooks)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SETTINGS_KEY = rf"Software\{APP_NAME}"
PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

# --- Win32 constants ----------------------------------------------------------
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_SETTINGCHANGE = 0x001A
WM_CONTEXTMENU = 0x007B
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_TIMER = 0x0113
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_OPEN_START = WM_APP + 2

WH_KEYBOARD_LL = 13
HC_ACTION = 0

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

MONITOR_DEFAULTTONULL = 0
MONITOR_DEFAULTTONEAREST = 2
MONITORINFOF_PRIMARY = 1

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04

MF_STRING = 0x0000
MF_CHECKED = 0x0008
MF_POPUP = 0x0010
MF_RADIOCHECK = 0x0200
MF_SEPARATOR = 0x0800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
SM_CXICON = 11
SM_CYICON = 12
SM_SWAPBUTTON = 23
SM_CXSMICON = 49
SM_CYSMICON = 50
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
IDI_APPLICATION = 32512
ERROR_ALREADY_EXISTS = 183
LOCALE_NAME_MAX_LENGTH = 85
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
WIN_VKS = (VK_LWIN, VK_RWIN)

TIMER_RESTORE_CURSOR = 1
TIMER_HOOK_REFRESH = 2
CMD_ENABLED = 1001
CMD_AUTOSTART = 1002
CMD_EXIT = 1003
CMD_LANG_AUTO = 2000
CMD_LANG_FIRST = 2001

# --- ctypes declarations ------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _proto(func, restype, *argtypes):
    func.restype = restype
    func.argtypes = argtypes


_proto(user32.DefWindowProcW, LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_proto(user32.RegisterClassExW, wintypes.ATOM, ctypes.POINTER(WNDCLASSEXW))
_proto(user32.CreateWindowExW, wintypes.HWND, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
       wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
       wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
_proto(user32.DestroyWindow, wintypes.BOOL, wintypes.HWND)
_proto(user32.GetMessageW, wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
_proto(user32.TranslateMessage, wintypes.BOOL, ctypes.POINTER(wintypes.MSG))
_proto(user32.DispatchMessageW, LRESULT, ctypes.POINTER(wintypes.MSG))
_proto(user32.PostQuitMessage, None, ctypes.c_int)
_proto(user32.PostMessageW, wintypes.BOOL, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_proto(user32.SetTimer, wintypes.WPARAM, wintypes.HWND, wintypes.WPARAM, wintypes.UINT, wintypes.LPVOID)
_proto(user32.KillTimer, wintypes.BOOL, wintypes.HWND, wintypes.WPARAM)
_proto(user32.RegisterWindowMessageW, wintypes.UINT, wintypes.LPCWSTR)
_proto(user32.SetWindowsHookExW, wintypes.HHOOK, ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
_proto(user32.UnhookWindowsHookEx, wintypes.BOOL, wintypes.HHOOK)
_proto(user32.CallNextHookEx, LRESULT, wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_proto(user32.GetAsyncKeyState, ctypes.c_short, ctypes.c_int)
_proto(user32.MapVirtualKeyW, wintypes.UINT, wintypes.UINT, wintypes.UINT)
_proto(user32.SendInput, wintypes.UINT, wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_proto(user32.GetCursorPos, wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
_proto(user32.SetCursorPos, wintypes.BOOL, ctypes.c_int, ctypes.c_int)
_proto(user32.MonitorFromPoint, wintypes.HMONITOR, wintypes.POINT, wintypes.DWORD)
_proto(user32.MonitorFromWindow, wintypes.HMONITOR, wintypes.HWND, wintypes.DWORD)
_proto(user32.GetMonitorInfoW, wintypes.BOOL, wintypes.HMONITOR, ctypes.POINTER(MONITORINFO))
_proto(user32.FindWindowExW, wintypes.HWND, wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
_proto(user32.GetWindowRect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_proto(user32.IsWindowVisible, wintypes.BOOL, wintypes.HWND)
_proto(user32.GetForegroundWindow, wintypes.HWND)
_proto(user32.GetWindowThreadProcessId, wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_proto(user32.CreatePopupMenu, wintypes.HMENU)
_proto(user32.AppendMenuW, wintypes.BOOL, wintypes.HMENU, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR)
_proto(user32.SetMenuDefaultItem, wintypes.BOOL, wintypes.HMENU, wintypes.UINT, wintypes.UINT)
_proto(user32.TrackPopupMenu, ctypes.c_int, wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
       ctypes.c_int, wintypes.HWND, wintypes.LPVOID)
_proto(user32.DestroyMenu, wintypes.BOOL, wintypes.HMENU)
_proto(user32.SetForegroundWindow, wintypes.BOOL, wintypes.HWND)
_proto(user32.LoadImageW, wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
       ctypes.c_int, ctypes.c_int, wintypes.UINT)
_proto(user32.LoadIconW, wintypes.HICON, wintypes.HINSTANCE, ctypes.c_void_p)
_proto(user32.DestroyIcon, wintypes.BOOL, wintypes.HICON)
_proto(user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
_proto(user32.MessageBoxW, ctypes.c_int, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
_proto(shell32.Shell_NotifyIconW, wintypes.BOOL, wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
_proto(kernel32.GetModuleHandleW, wintypes.HMODULE, wintypes.LPCWSTR)
_proto(kernel32.CreateMutexW, wintypes.HANDLE, wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
_proto(kernel32.OpenProcess, wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_proto(kernel32.QueryFullProcessImageNameW, wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD,
       wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
_proto(kernel32.CloseHandle, wintypes.BOOL, wintypes.HANDLE)
_proto(kernel32.GetUserDefaultUILanguage, wintypes.WORD)
_proto(kernel32.LCIDToLocaleName, ctypes.c_int, wintypes.DWORD, wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD)


# --- Paths --------------------------------------------------------------------
def resource_path(name):
    """Path to a bundled file (PyInstaller extracts to sys._MEIPASS)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def app_dir():
    """Folder of the exe (frozen) or of this script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# --- Settings (registry) ------------------------------------------------------
def load_setting(name, default=None):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SETTINGS_KEY) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return default


def save_setting(name, value):
    reg_type = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, SETTINGS_KEY) as key:
            winreg.SetValueEx(key, name, 0, reg_type, value)
    except OSError:
        pass


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    return f'"{pythonw}" "{os.path.abspath(__file__)}"'


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enable):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enable:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def taskbar_is_light():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY) as key:
            return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
    except OSError:
        return False  # Windows 11 default: dark taskbar


# --- Translations -------------------------------------------------------------
def system_ui_locale():
    """Windows display language as locale name, e.g. 'de-DE'."""
    buf = ctypes.create_unicode_buffer(LOCALE_NAME_MAX_LENGTH)
    if kernel32.LCIDToLocaleName(kernel32.GetUserDefaultUILanguage(), buf, LOCALE_NAME_MAX_LENGTH, 0):
        return buf.value
    return FALLBACK_LANGUAGE


class Translator:
    """Loads lang/<code>.json files. Files in a 'lang' folder next to the exe
    override/extend the bundled ones, so languages can be added without a rebuild."""

    def __init__(self):
        self.languages = {}
        for folder in dict.fromkeys([resource_path(LANG_DIR), os.path.join(app_dir(), LANG_DIR)]):
            for path in glob.glob(os.path.join(folder, "*.json")):
                code = os.path.splitext(os.path.basename(path))[0].lower()
                try:
                    with open(path, encoding="utf-8-sig") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    continue
                if isinstance(data, dict):
                    self.languages[code] = data
        self.system_code = self._match(system_ui_locale())
        self.setting = ""
        self.code = self.system_code
        self.set_language(str(load_setting("Language", "") or ""))

    def _match(self, locale):
        """'de-AT' -> 'de-at' if available, else 'de', else the fallback."""
        locale = locale.lower()
        for candidate in (locale, locale.split("-")[0], FALLBACK_LANGUAGE):
            if candidate in self.languages:
                return candidate
        return next(iter(self.languages), FALLBACK_LANGUAGE)

    def set_language(self, code):
        """code '' = follow the Windows display language."""
        code = code.lower()
        self.setting = code if code in self.languages else ""
        self.code = self.setting or self.system_code

    def name(self, code):
        return self.languages.get(code, {}).get("language_name", code)

    def sorted_codes(self):
        return sorted(self.languages, key=lambda c: self.name(c).lower())

    def __call__(self, key, **kwargs):
        text = (self.languages.get(self.code, {}).get(key)
                or self.languages.get(FALLBACK_LANGUAGE, {}).get(key)
                or key)
        return text.format(**kwargs) if kwargs else text


tr = None  # Translator instance, created in main()


# --- Monitors, taskbars, Start button -----------------------------------------
def cursor_pos():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt


def monitor_info(hmon):
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if hmon and user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        return info
    return None


def process_name(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def start_menu_is_open():
    fg = user32.GetForegroundWindow()
    return bool(fg) and process_name(fg) in START_HOST_PROCESSES


def start_button_point(hmon, rc_monitor):
    """Center of the Start button of the taskbar on monitor hmon, or None
    (no taskbar there, taskbar auto-hidden, ...)."""
    for cls in TASKBAR_CLASSES:
        taskbar = user32.FindWindowExW(None, None, cls, None)
        while taskbar:
            if (user32.IsWindowVisible(taskbar)
                    and user32.MonitorFromWindow(taskbar, MONITOR_DEFAULTTONULL) == hmon):
                button = user32.FindWindowExW(taskbar, None, START_BUTTON_CLASS, None)
                rect = wintypes.RECT()
                if button and user32.GetWindowRect(button, ctypes.byref(rect)) \
                        and rect.right > rect.left and rect.bottom > rect.top:
                    x = (rect.left + rect.right) // 2
                    y = (rect.top + rect.bottom) // 2
                    if rc_monitor.left <= x < rc_monitor.right and rc_monitor.top <= y < rc_monitor.bottom:
                        return x, y
            taskbar = user32.FindWindowExW(None, taskbar, cls, None)
    return None


def target_for_cursor(include_primary):
    """Start button point on the monitor under the cursor, or None if Windows
    should handle the key itself."""
    hmon = user32.MonitorFromPoint(cursor_pos(), MONITOR_DEFAULTTONEAREST)
    info = monitor_info(hmon)
    if info is None:
        return None
    if not include_primary and info.dwFlags & MONITORINFOF_PRIMARY:
        return None
    return start_button_point(hmon, info.rcMonitor)


# --- Input injection ----------------------------------------------------------
def _key_input(vk, up=False, extended=False):
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.u.ki.wVk = vk
    inp.u.ki.wScan = user32.MapVirtualKeyW(vk, 0) & 0xFF
    inp.u.ki.dwFlags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    inp.u.ki.dwExtraInfo = INJECTED_MARKER
    return inp


def _mouse_input(flags, x=0, y=0):
    inp = INPUT(type=INPUT_MOUSE)
    inp.u.mi.dx = x
    inp.u.mi.dy = y
    inp.u.mi.dwFlags = flags
    inp.u.mi.dwExtraInfo = INJECTED_MARKER
    return inp


def send_inputs(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    return user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def release_win_masked(win_vk):
    """Release the Windows key so that Windows does not open Start: an
    unassigned key pressed in between makes it a 'Win + key' combination."""
    send_inputs(_key_input(MASK_VK), _key_input(MASK_VK, up=True),
                _key_input(win_vk, up=True, extended=True))


def _absolute(x, y):
    """Screen pixel -> normalized 0..65535 coordinates on the virtual desktop."""
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = max(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) - 1, 1)
    vh = max(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) - 1, 1)
    return ((x - vx) * 65535 + vw // 2) // vw, ((y - vy) * 65535 + vh // 2) // vh


def click_at(x, y, back_to):
    """Left-click (primary button) at x, y and move the cursor back."""
    if user32.GetSystemMetrics(SM_SWAPBUTTON):
        down, up = MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
    else:
        down, up = MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
    move = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    send_inputs(_mouse_input(move | down, *_absolute(x, y)),
                _mouse_input(move | up, *_absolute(x, y)),
                _mouse_input(move, *_absolute(back_to.x, back_to.y)))


def load_icon(filename, large=False):
    cx = user32.GetSystemMetrics(SM_CXICON if large else SM_CXSMICON)
    cy = user32.GetSystemMetrics(SM_CYICON if large else SM_CYSMICON)
    path = resource_path(filename)
    if os.path.exists(path):
        hicon = user32.LoadImageW(None, path, IMAGE_ICON, cx, cy, LR_LOADFROMFILE)
        if hicon:
            return hicon
    return None


# --- Tray application ---------------------------------------------------------
class TrayApp:
    def __init__(self, tray=True):
        self.tray = tray
        self.hinst = kernel32.GetModuleHandleW(None)
        self.enabled = bool(load_setting("Enabled", 1)) if tray else False
        self.hook = None
        self.win_vk = None        # Windows key currently held (VK_LWIN/VK_RWIN)
        self.combo = False        # another key was pressed while it was held
        self.target = None        # Start button point to click
        self.restore_to = None    # cursor position to restore after the click
        self.menu_languages = []
        self.msg_taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")

        # Keep references to the callbacks, otherwise they get garbage-collected.
        self.wndproc = WNDPROC(self._wnd_proc)
        self.hookproc = HOOKPROC(self._hook_proc)

        self.hicon = None
        self.app_icon = self.app_icon_small = None
        if tray:
            self.app_icon = load_icon(APP_ICON_FILE, large=True)
            self.app_icon_small = load_icon(APP_ICON_FILE)
            self.hicon = self._load_tray_icon()
        main_class = self._register_class(f"{APP_NAME}Window", self.wndproc)

        # Hidden top-level window (not message-only, so that broadcasts like
        # TaskbarCreated and WM_SETTINGCHANGE arrive).
        self.hwnd = user32.CreateWindowExW(0, main_class, APP_NAME, 0, 0, 0, 0, 0,
                                           None, None, self.hinst, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        if tray:
            self._add_icon()
            if self.enabled and not self.install_hook():
                user32.MessageBoxW(self.hwnd, tr("hook_error", error=ctypes.WinError(ctypes.get_last_error())),
                                   APP_NAME, 0x10)
                self.set_enabled(False, save=False)
            user32.SetTimer(self.hwnd, TIMER_HOOK_REFRESH, HOOK_REFRESH_MS, None)

    def _register_class(self, name, wndproc):
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = wndproc
        wc.hInstance = self.hinst
        wc.hIcon = self.app_icon
        wc.hIconSm = self.app_icon_small
        wc.lpszClassName = name
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())
        return name

    # Keyboard hook -------------------------------------------------------------
    def install_hook(self):
        if not self.hook:
            self.hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self.hookproc, self.hinst, 0)
        return bool(self.hook)

    def uninstall_hook(self):
        if self.hook:
            user32.UnhookWindowsHookEx(self.hook)
            self.hook = None
        self.win_vk = None

    def refresh_hook(self):
        """Windows silently drops a low-level hook that once took too long;
        re-installing it now and then keeps the feature alive."""
        if self.enabled and self.win_vk is None:
            self.uninstall_hook()
            self.install_hook()

    def _hook_proc(self, code, wparam, lparam):
        try:
            if code == HC_ACTION:
                kb = KBDLLHOOKSTRUCT.from_address(lparam)
                if kb.dwExtraInfo != INJECTED_MARKER and self._on_key(kb.vkCode, wparam):
                    return 1  # swallow the key event
        except Exception:  # never let an exception break keyboard input
            pass
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _on_key(self, vk, wparam):
        """Returns True if the event must be swallowed. Runs inside the hook:
        keep it short, the real work is posted to the window."""
        down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
        if vk in WIN_VKS:
            if down:
                if self.win_vk is None:
                    self.win_vk = vk
                    # Ctrl/Alt/Shift already held -> a combination, not "Win alone".
                    self.combo = any(user32.GetAsyncKeyState(m) & 0x8000
                                     for m in (VK_CONTROL, VK_MENU, VK_SHIFT))
                elif vk != self.win_vk:
                    self.combo = True  # both Windows keys
                return False
            if vk != self.win_vk:
                return False
            alone, self.win_vk = not self.combo, None
            if not alone or start_menu_is_open():
                return False
            target = target_for_cursor(include_primary=False)
            if target is None:
                return False
            release_win_masked(vk)
            self.target = target
            user32.PostMessageW(self.hwnd, WM_OPEN_START, 0, 0)
            return True
        if down and self.win_vk is not None:
            self.combo = True
        return False

    # Opening Start -------------------------------------------------------------
    def open_start(self, target):
        """Click the Start button at target and put the cursor back."""
        user32.KillTimer(self.hwnd, TIMER_RESTORE_CURSOR)
        if self.restore_to is None:
            self.restore_to = cursor_pos()
        click_at(*target, self.restore_to)
        # The absolute move may be off by a pixel due to normalization.
        user32.SetTimer(self.hwnd, TIMER_RESTORE_CURSOR, CURSOR_RESTORE_MS, None)

    def restore_cursor(self):
        user32.KillTimer(self.hwnd, TIMER_RESTORE_CURSOR)
        if self.restore_to is not None:
            user32.SetCursorPos(self.restore_to.x, self.restore_to.y)
            self.restore_to = None
        if not self.tray:  # --open mode: exit afterwards
            user32.DestroyWindow(self.hwnd)

    # On/off --------------------------------------------------------------------
    def set_enabled(self, enabled, save=True):
        if enabled and not self.install_hook():
            user32.MessageBoxW(self.hwnd, tr("hook_error", error=ctypes.WinError(ctypes.get_last_error())),
                               APP_NAME, 0x10)
            enabled = False
        if not enabled:
            self.uninstall_hook()
        self.enabled = enabled
        if save:
            save_setting("Enabled", int(enabled))
        self.refresh_tray_icon()

    # Tray icon -----------------------------------------------------------------
    def _load_tray_icon(self):
        filename = TRAY_ICONS[(taskbar_is_light(), self.enabled)]
        return load_icon(filename) or user32.LoadIconW(None, IDI_APPLICATION)

    def refresh_tray_icon(self):
        """After switching on/off or between light and dark Windows mode."""
        old = self.hicon
        self.hicon = self._load_tray_icon()
        self.update_icon()
        if old:
            user32.DestroyIcon(old)

    def _nid(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.hicon
        nid.szTip = tr("tooltip_on" if self.enabled else "tooltip_off")[:127]
        return nid

    def _add_icon(self):
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid()))

    def update_icon(self):
        if self.tray:
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid()))

    def _remove_icon(self):
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid()))

    # Language ------------------------------------------------------------------
    def set_language(self, code):
        tr.set_language(code)
        save_setting("Language", tr.setting)
        self.update_icon()

    # Menu ----------------------------------------------------------------------
    def _build_language_menu(self):
        menu = user32.CreatePopupMenu()
        radio = MF_STRING | MF_RADIOCHECK
        auto_label = tr("menu_language_auto", language=tr.name(tr.system_code))
        user32.AppendMenuW(menu, radio | (0 if tr.setting else MF_CHECKED), CMD_LANG_AUTO, auto_label)
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        self.menu_languages = tr.sorted_codes()
        for i, code in enumerate(self.menu_languages):
            checked = MF_CHECKED if tr.setting == code else 0
            user32.AppendMenuW(menu, radio | checked, CMD_LANG_FIRST + i, tr.name(code))
        return menu

    def show_menu(self):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if self.enabled else 0), CMD_ENABLED,
                           tr("menu_enabled"))
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if autostart_enabled() else 0), CMD_AUTOSTART,
                           tr("menu_autostart"))
        user32.AppendMenuW(menu, MF_POPUP, self._build_language_menu(), tr("menu_language"))
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, CMD_EXIT, tr("menu_exit"))
        user32.SetMenuDefaultItem(menu, CMD_ENABLED, 0)

        pt = cursor_pos()
        # Required so the menu closes when the user clicks elsewhere.
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                    pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)  # also destroys the language submenu

        if cmd == CMD_ENABLED:
            self.set_enabled(not self.enabled)
        elif cmd == CMD_AUTOSTART:
            try:
                set_autostart(not autostart_enabled())
            except OSError as exc:
                user32.MessageBoxW(self.hwnd, tr("autostart_error", error=exc), APP_NAME, 0x10)
        elif cmd == CMD_LANG_AUTO:
            self.set_language("")
        elif CMD_LANG_FIRST <= cmd < CMD_LANG_FIRST + len(self.menu_languages):
            self.set_language(self.menu_languages[cmd - CMD_LANG_FIRST])
        elif cmd == CMD_EXIT:
            user32.DestroyWindow(self.hwnd)

    # Window procedure ----------------------------------------------------------
    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_OPEN_START:
            if self.target:
                self.open_start(self.target)
                self.target = None
            return 0
        if msg == WM_TRAY:
            event = lparam & 0xFFFF
            if event == WM_LBUTTONUP:
                self.set_enabled(not self.enabled)
            elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self.show_menu()
            return 0
        if msg == WM_TIMER:
            if wparam == TIMER_RESTORE_CURSOR:
                self.restore_cursor()
            elif wparam == TIMER_HOOK_REFRESH:
                self.refresh_hook()
            return 0
        if msg == WM_SETTINGCHANGE and self.tray and lparam:
            if ctypes.wstring_at(lparam) == "ImmersiveColorSet":
                self.refresh_tray_icon()
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        if msg == self.msg_taskbar_created and self.tray:
            self._add_icon()  # Explorer was restarted
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            self.uninstall_hook()
            user32.KillTimer(hwnd, TIMER_HOOK_REFRESH)
            if self.tray:
                self._remove_icon()
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def run(self):
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        for icon in (self.hicon, self.app_icon, self.app_icon_small):
            if icon:
                user32.DestroyIcon(icon)


def main():
    global tr
    # Physical pixel coordinates for cursor, monitors and taskbar windows.
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass

    tr = Translator()

    if "--open" in sys.argv[1:]:
        # Open Start once on the monitor under the cursor (primary included).
        target = target_for_cursor(include_primary=True)
        if target is None:
            return
        app = TrayApp(tray=False)
        app.open_start(target)
        app.run()
        return

    # Allow only a single instance.
    mutex = kernel32.CreateMutexW(None, False, f"Local\\{APP_NAME}_SingleInstance")
    if mutex and ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return

    TrayApp().run()


if __name__ == "__main__":
    main()
