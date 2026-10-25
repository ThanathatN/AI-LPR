"""Live video from Hikvision cameras through HCNetSDK (private protocol, port 8000) + PlayCtrl decoding.

Drop-in replacement for camera.VideoSource: same start / stop / latest / status, plus read() / latest_gray().

Two data paths:

1) Native (a window handle is given — desktop live view), exactly like ClientDemo:
     NET_DVR_RealPlay_V40(hPlayWnd = hwnd, callback = NULL)
   HCNetSDK receives, decodes and draws in its own C threads. No Python code runs per frame, so the
   GIL (uvicorn, LPR, Tk) can never delay the live picture.
   LPR pulls frames on demand: the session thread takes a snapshot of the SDK's own PlayCtrl port
   (NET_DVR_GetRealPlayerIndex -> PlayM4_GetBMP) only while a consumer is asking for frames,
   at most `grab_fps` times per second.

2) No window (server only / web): our own PlayCtrl port, fed from the real-data callback:
     HCNetSDK network thread ── _real_data (copy) ──▶ raw queue
     session thread          ── PlayM4_InputData ──▶ PlayCtrl decoder
                             └─ _decoded (YV12 copy, overwrites "latest") ──▶ consumers

ctypes structures below follow hiksdk/lib/HCNetSDK.h and plaympeg4.h (PlayM4) exactly; those headers
have no #pragma pack, so ctypes' default (natural) alignment matches the x64 DLLs.
"""
import atexit
import ctypes
import itertools
import logging
import os
import queue
import struct
import threading
import time
from ctypes import (POINTER, WINFUNCTYPE, Structure, byref, c_char, c_char_p, c_int, c_long, c_ubyte, c_uint,
                    c_ushort, c_void_p)
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("lpr.hiksdk")

# ---------------------------------------------------------------------------------- DLL location
# Resolved from this file (never the current directory): start.bat may run from anywhere.
SDK_ROOT = Path(os.environ.get("HIKSDK_DIR") or Path(__file__).resolve().parent.parent / "hiksdk")


def find_sdk_dir():
    """Folder that contains HCNetSDK.dll (hiksdk/lib, or hiksdk/include as shipped in this project)."""
    for d in (SDK_ROOT / "lib", SDK_ROOT / "include", SDK_ROOT):
        if (d / "HCNetSDK.dll").is_file() and (d / "PlayCtrl.dll").is_file():
            return d
    return None


# ---------------------------------------------------------------------------------- Windows types
BOOL, BYTE, WORD, DWORD, LONG = c_int, c_ubyte, c_ushort, c_uint, c_long   # Win64: long = 32-bit
HWND = c_void_p

# HCNetSDK.h constants
SERIALNO_LEN = 48
NET_DVR_DEV_ADDRESS_MAX_LEN = 129
NET_DVR_LOGIN_USERNAME_MAX_LEN = 64
NET_DVR_LOGIN_PASSWD_MAX_LEN = 64
STREAM_ID_LEN = 32
NET_SDK_MAX_FILE_PATH = 256
NET_SDK_INIT_CFG_SDK_PATH, NET_SDK_INIT_CFG_LIBEAY_PATH, NET_SDK_INIT_CFG_SSLEAY_PATH = 2, 3, 4
NET_DVR_SYSHEAD, NET_DVR_STREAMDATA = 1, 2
EXCEPTION_EXCHANGE, EXCEPTION_PREVIEW, EXCEPTION_RECONNECT = 0x8000, 0x8003, 0x8005
PREVIEW_RECONNECTSUCCESS = 0x8018
NET_DVR_PASSWORD_ERROR, NET_DVR_USER_LOCKED = 1, 153

# plaympeg4.h constants
STREAME_REALTIME = 0
T_YV12 = 3
SOURCE_BUF_MAX = 1024 * 100000
PLAY_BUF = 6 * 1024 * 1024          # PlayM4_OpenStream source buffer (bytes), within SOURCE_BUF_MIN..MAX


class NET_DVR_DEVICEINFO_V30(Structure):
    _fields_ = [
        ("sSerialNumber", BYTE * SERIALNO_LEN),
        ("byAlarmInPortNum", BYTE), ("byAlarmOutPortNum", BYTE), ("byDiskNum", BYTE), ("byDVRType", BYTE),
        ("byChanNum", BYTE), ("byStartChan", BYTE), ("byAudioChanNum", BYTE), ("byIPChanNum", BYTE),
        ("byZeroChanNum", BYTE), ("byMainProto", BYTE), ("bySubProto", BYTE), ("bySupport", BYTE),
        ("bySupport1", BYTE), ("bySupport2", BYTE), ("wDevType", WORD), ("bySupport3", BYTE),
        ("byMultiStreamProto", BYTE), ("byStartDChan", BYTE), ("byStartDTalkChan", BYTE),
        ("byHighDChanNum", BYTE), ("bySupport4", BYTE), ("byLanguageType", BYTE), ("byVoiceInChanNum", BYTE),
        ("byStartVoiceInChanNo", BYTE), ("bySupport5", BYTE), ("bySupport6", BYTE), ("byMirrorChanNum", BYTE),
        ("wStartMirrorChanNo", WORD), ("bySupport7", BYTE), ("byRes2", BYTE),
    ]


class NET_DVR_DEVICEINFO_V40(Structure):
    _fields_ = [
        ("struDeviceV30", NET_DVR_DEVICEINFO_V30),
        ("bySupportLock", BYTE), ("byRetryLoginTime", BYTE), ("byPasswordLevel", BYTE), ("byProxyType", BYTE),
        ("dwSurplusLockTime", DWORD), ("byCharEncodeType", BYTE), ("bySupportDev5", BYTE), ("bySupport", BYTE),
        ("byLoginMode", BYTE), ("dwOEMCode", DWORD), ("iResidualValidity", c_int), ("byResidualValidity", BYTE),
        ("bySingleStartDTalkChan", BYTE), ("bySingleDTalkChanNums", BYTE), ("byPassWordResetLevel", BYTE),
        ("bySupportStreamEncrypt", BYTE), ("byMarketType", BYTE), ("byTLSCap", BYTE), ("byChildManage", BYTE),
        ("byPlaybackNewPosCap", BYTE), ("bySecondaryAuth", BYTE), ("byHttpsResult", BYTE), ("byRes2", BYTE * 233),
    ]


fLoginResultCallBack = WINFUNCTYPE(None, LONG, DWORD, POINTER(NET_DVR_DEVICEINFO_V30), c_void_p)
fLoginResultCallBack_V40 = WINFUNCTYPE(None, LONG, DWORD, POINTER(NET_DVR_DEVICEINFO_V40), c_void_p)


class NET_DVR_USER_LOGIN_INFO(Structure):
    _fields_ = [   # OS_WINDOWS64 branch: no byRes2 padding before byRes3
        ("sDeviceAddress", c_char * NET_DVR_DEV_ADDRESS_MAX_LEN), ("byUseTransport", BYTE), ("wPort", WORD),
        ("sUserName", c_char * NET_DVR_LOGIN_USERNAME_MAX_LEN), ("sPassword", c_char * NET_DVR_LOGIN_PASSWD_MAX_LEN),
        ("cbLoginResult", fLoginResultCallBack), ("pUser", c_void_p), ("bUseAsynLogin", BOOL),
        ("byProxyType", BYTE), ("byUseUTCTime", BYTE), ("byLoginMode", BYTE), ("byHttps", BYTE),
        ("iProxyID", LONG), ("byVerifyMode", BYTE), ("byRes1", BYTE * 3),
        ("cbLoginResultV40", fLoginResultCallBack_V40), ("byRes3", BYTE * 109),
    ]


class NET_DVR_PREVIEWINFO(Structure):
    _fields_ = [
        ("lChannel", LONG), ("dwStreamType", DWORD), ("dwLinkMode", DWORD), ("hPlayWnd", HWND),
        ("bBlocked", DWORD), ("bPassbackRecord", DWORD), ("byPreviewMode", BYTE),
        ("byStreamID", BYTE * STREAM_ID_LEN), ("byProtoType", BYTE), ("byRes1", BYTE),
        ("byVideoCodingType", BYTE), ("dwDisplayBufNum", DWORD), ("byNPQMode", BYTE), ("byRecvMetaData", BYTE),
        ("byDataType", BYTE), ("byReconnect", BYTE), ("byRes", BYTE * 212),
    ]


class NET_DVR_LOCAL_SDK_PATH(Structure):
    _fields_ = [("sPath", c_char * NET_SDK_MAX_FILE_PATH), ("byRes", BYTE * 128)]


class FRAME_INFO(Structure):      # plaympeg4.h
    _fields_ = [("nWidth", c_long), ("nHeight", c_long), ("nStamp", c_long), ("nType", c_long),
                ("nFrameRate", c_long), ("dwFrameNum", DWORD)]


# typedef void (CALLBACK *REALDATACALLBACK)(LONG lPlayHandle, DWORD dwDataType, BYTE *pBuffer, DWORD dwBufSize, void* pUser)
REALDATACALLBACK = WINFUNCTYPE(None, LONG, DWORD, POINTER(BYTE), DWORD, c_void_p)
# void (CALLBACK* fExceptionCallBack)(DWORD dwType, LONG lUserID, LONG lHandle, void *pUser)
EXCEPTIONCALLBACK = WINFUNCTYPE(None, DWORD, LONG, LONG, c_void_p)
# void (CALLBACK* DecCBFun)(long nPort, char* pBuf, long nSize, FRAME_INFO* pFrameInfo, long nUser, long nReserved2)
DECCALLBACK = WINFUNCTYPE(None, c_long, POINTER(c_char), c_long, POINTER(FRAME_INFO), c_long, c_long)


# ---------------------------------------------------------------------------------- SDK (process-wide)
class _SDK:
    """Loads the DLLs and runs NET_DVR_Init / NET_DVR_Cleanup once for the whole process."""

    def __init__(self):
        self.lock = threading.Lock()
        self.net = self.play = None
        self.dir = None
        self.ready = False
        self.error = ""
        self._dll_handles = []

    def load(self):
        with self.lock:
            if self.ready:
                return True
            if struct.calcsize("P") != 8:
                self.error = "Python ต้องเป็น 64-bit เพื่อใช้ HCNetSDK x64"
                return False
            d = find_sdk_dir()
            if d is None:
                self.error = "ไม่พบ HCNetSDK ใน hiksdk/"
                log.error("HCNetSDK.dll / PlayCtrl.dll not found under %s", SDK_ROOT)
                return False
            try:
                for p in (d, d / "HCNetSDKCom"):
                    if p.is_dir():
                        self._dll_handles.append(os.add_dll_directory(str(p)))
                # PATH as well: HCNetSDK loads its HCNetSDKCom modules with LoadLibrary at runtime
                os.environ["PATH"] = f"{d};{d / 'HCNetSDKCom'};" + os.environ.get("PATH", "")
                self.net = ctypes.WinDLL(str(d / "HCNetSDK.dll"))
                self.play = ctypes.WinDLL(str(d / "PlayCtrl.dll"))
                self._prototypes()
                # tell the SDK where its component / OpenSSL DLLs are (must be before NET_DVR_Init)
                sp = NET_DVR_LOCAL_SDK_PATH()
                sp.sPath = (str(d) + "\\").encode("mbcs")
                self.net.NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SDK_PATH, byref(sp))
                for kind, name in ((NET_SDK_INIT_CFG_LIBEAY_PATH, "libcrypto-3-x64.dll"),
                                   (NET_SDK_INIT_CFG_SSLEAY_PATH, "libssl-3-x64.dll")):
                    if (d / name).is_file():
                        self.net.NET_DVR_SetSDKInitCfg(kind, c_char_p(str(d / name).encode("mbcs")))
                if not self.net.NET_DVR_Init():
                    raise RuntimeError(f"NET_DVR_Init failed ({self.net.NET_DVR_GetLastError()})")
                self.net.NET_DVR_SetConnectTime(3000, 1)
                self.net.NET_DVR_SetReconnect(5000, True)          # SDK-level preview reconnect
                log_dir = SDK_ROOT.parent / "data" / "logs" / "hiksdk"
                log_dir.mkdir(parents=True, exist_ok=True)
                self.net.NET_DVR_SetLogToFile(1, c_char_p((str(log_dir) + "\\").encode("mbcs")), True)
                self.net.NET_DVR_SetExceptionCallBack_V30(0, None, _EXC_CB, None)
                self.dir, self.ready, self.error = d, True, ""
                log.info("HCNetSDK ready (%s, SDK v%s)", d, self.version())
                return True
            except Exception as e:
                self.error = f"โหลด HCNetSDK ไม่สำเร็จ: {e}"
                log.exception("HCNetSDK load failed")
                return False

    def _prototypes(self):
        n, p = self.net, self.play
        sig = [
            (n.NET_DVR_Init, BOOL, []), (n.NET_DVR_Cleanup, BOOL, []), (n.NET_DVR_GetLastError, DWORD, []),
            (n.NET_DVR_GetSDKVersion, DWORD, []),
            (n.NET_DVR_SetSDKInitCfg, BOOL, [c_int, c_void_p]),
            (n.NET_DVR_SetConnectTime, BOOL, [DWORD, DWORD]), (n.NET_DVR_SetReconnect, BOOL, [DWORD, BOOL]),
            (n.NET_DVR_SetLogToFile, BOOL, [DWORD, c_char_p, BOOL]),
            (n.NET_DVR_SetExceptionCallBack_V30, BOOL, [c_uint, HWND, EXCEPTIONCALLBACK, c_void_p]),
            (n.NET_DVR_Login_V40, LONG, [POINTER(NET_DVR_USER_LOGIN_INFO), POINTER(NET_DVR_DEVICEINFO_V40)]),
            (n.NET_DVR_Logout, BOOL, [LONG]),
            (n.NET_DVR_RealPlay_V40, LONG, [LONG, POINTER(NET_DVR_PREVIEWINFO), c_void_p, c_void_p]),
            (n.NET_DVR_StopRealPlay, BOOL, [LONG]),
            (n.NET_DVR_GetRealPlayerIndex, LONG, [LONG]), (n.NET_DVR_RefreshPlay, BOOL, [LONG]),
            (n.NET_DVR_SetCapturePictureMode, BOOL, [DWORD]),
            (n.NET_DVR_CapturePictureBlock_New, BOOL, [LONG, c_void_p, DWORD, POINTER(DWORD)]),
            (p.PlayM4_GetPort, BOOL, [POINTER(LONG)]), (p.PlayM4_FreePort, BOOL, [LONG]),
            (p.PlayM4_SetStreamOpenMode, BOOL, [LONG, DWORD]),
            (p.PlayM4_OpenStream, BOOL, [LONG, c_void_p, DWORD, DWORD]),
            (p.PlayM4_CloseStream, BOOL, [LONG]),
            (p.PlayM4_InputData, BOOL, [LONG, c_void_p, DWORD]),
            (p.PlayM4_SetDecCallBackExMend, BOOL, [LONG, DECCALLBACK, c_void_p, c_long, c_long]),
            (p.PlayM4_SetDecCBStream, BOOL, [LONG, DWORD]),
            (p.PlayM4_Play, BOOL, [LONG, HWND]), (p.PlayM4_Stop, BOOL, [LONG]),
            (p.PlayM4_SetDisplayBuf, BOOL, [LONG, DWORD]), (p.PlayM4_RefreshPlay, BOOL, [LONG]),
            (p.PlayM4_GetLastError, DWORD, [LONG]),
            (p.PlayM4_GetPictureSize, BOOL, [LONG, POINTER(LONG), POINTER(LONG)]),
            (p.PlayM4_GetBMP, BOOL, [LONG, c_void_p, DWORD, POINTER(DWORD)]),
            (p.PlayM4_GetPlayedFrames, DWORD, [LONG]),
        ]
        for fn, res, args in sig:
            fn.restype, fn.argtypes = res, args

    def version(self):
        v = self.net.NET_DVR_GetSDKVersion()
        return f"{v >> 16}.{(v >> 8) & 0xFF}.{v & 0xFF}" if v else "?"

    def last_error(self):
        return self.net.NET_DVR_GetLastError() if self.net else -1

    def cleanup(self):
        with self.lock:
            if self.ready:
                for s in list(_SESSIONS.values()):
                    s.stop(join=True)
                self.net.NET_DVR_Cleanup()
                self.ready = False
                log.info("HCNetSDK cleaned up")


SDK = _SDK()
WINDOWS = {}                   # camera id -> HWND of a desktop live-view widget (set by app/desktop.py)
_SESSIONS = {}                 # session key -> HikSDKSource
_BY_PLAY = {}                  # NET_DVR_RealPlay handle -> session
_BY_PORT = {}                  # PlayM4 port -> session
_BY_USER = {}                  # login user id -> session
_keys = itertools.count(1)




# ---------------------------------------------------------------------------------- callbacks
# Module-level CFUNCTYPE objects: they live as long as the module, so the SDK never calls freed code.
# Callbacks only copy bytes / bump a counter / set a flag — no decoding, no cvtColor, no locks waited on.
def _real_data(lPlayHandle, dwDataType, pBuffer, dwBufSize, pUser):
    s = _SESSIONS.get(pUser or 0)
    if s is None or dwBufSize <= 0:
        return
    if dwDataType in (NET_DVR_SYSHEAD, NET_DVR_STREAMDATA):
        s._push_raw(dwDataType, ctypes.string_at(pBuffer, dwBufSize))


def _decoded(nPort, pBuf, nSize, pFrameInfo, nUser, nReserved2):
    s = _BY_PORT.get(nPort)
    if s is None:
        return
    fi = pFrameInfo.contents
    if fi.nType != T_YV12:
        return
    w, h = fi.nWidth, fi.nHeight
    n = w * h * 3 // 2
    if w <= 0 or h <= 0 or nSize < n:
        return
    s._dec_count += 1
    # newest frame overwrites the previous one (single reference assignment, atomic under the GIL)
    s._yv12 = (ctypes.string_at(pBuf, n), w, h, s._dec_count, time.time())


def _exception(dwType, lUserID, lHandle, pUser):
    s = _BY_PLAY.get(lHandle) or _BY_USER.get(lUserID)
    if s is not None:
        s._on_exception(dwType)


_REAL_CB = REALDATACALLBACK(_real_data)
_DEC_CB = DECCALLBACK(_decoded)
_EXC_CB = EXCEPTIONCALLBACK(_exception)


# ---------------------------------------------------------------------------------- one camera
class HikSDKSource:
    """Same interface as camera.VideoSource; ``start`` takes a dict instead of an RTSP URL:
    {"host", "port" (8000), "username", "password", "channel" (1), "stream_type" (0 main / 1 sub),
     "hwnd" (optional window handle: the SDK draws the live video into it, exactly like ClientDemo),
     "display_buf" (1-15 frames of display buffering; 6 = ClientDemo "Balance"),
     "grab_fps" (max frames/s handed to LPR / web view in window mode, default 25)}

    Live display (hwnd) is done entirely by PlayCtrl's own threads. Consumers (LPR, web view) get the
    latest decoded frame on demand; colour conversion happens in the consumer's thread, at its rate.
    """

    NO_FRAME_TIMEOUT = 10.0     # seconds without a decoded frame -> full reconnect

    def __init__(self, name="hik"):
        self.name = name
        self.key = next(_keys)
        self.cfg = {}
        self.connected = False
        self.error = ""
        self.fps = 0.0                  # decoded frames per second (PlayCtrl)
        self.width = self.height = 0
        self.device = {}
        self._yv12 = None               # (bytes, w, h, seq, ts) — written only by the decode callback
        self._dec_count = 0
        self._conv_lock = threading.Lock()
        self._bgr, self._bgr_seq = None, -1
        self._stop = threading.Event()
        self._restart = threading.Event()
        self._thread = None
        self._raw_q = queue.Queue(maxsize=2000)     # stream packets (~ several seconds)
        self._user = -1
        self._play = -1
        self._port = -1
        self._broken = threading.Event()            # set by the exception callback
        self._dropped = 0
        # native (window) mode
        self._native = False
        self._frame = None              # (BGR, seq, ts) — last snapshot pulled for consumers
        self._seq = 0
        self._want = 0.0                # last time a consumer asked for a frame
        self._last_grab = 0.0
        self._grab_buf = None
        self._played_last = None
        self._alive_ts = 0.0            # last time the SDK showed a new frame
        self._capture_mode_set = False

    # ------------------------------------------------------------ public interface (VideoSource)
    def start(self, cfg):
        self.cfg = dict(cfg or {})
        if self._thread and self._thread.is_alive():
            self._restart.set()
            return
        self._stop.clear()
        _SESSIONS[self.key] = self
        self._thread = threading.Thread(target=self._run, daemon=True, name=self.name)
        self._thread.start()

    def stop(self, join=False):
        self._stop.set()
        self._restart.set()
        if join and self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=5)

    def set_window(self, hwnd):
        """Attach (or detach with None) a native window for PlayCtrl to draw into; reconnects the stream."""
        if self.cfg.get("hwnd") != hwnd:
            self.cfg["hwnd"] = hwnd
            self._restart.set()

    def refresh_window(self):
        """Repaint after the window was resized / uncovered."""
        if self._native and self._play >= 0:
            SDK.net.NET_DVR_RefreshPlay(self._play)
        elif self._port >= 0 and self.cfg.get("hwnd"):
            SDK.play.PlayM4_RefreshPlay(self._port)

    @property
    def frame_id(self):
        if self._native:
            self._want = time.time()
            f = self._frame
            return f[1] if f else 0
        y = self._yv12
        return y[3] if y else 0

    @property
    def frame_ts(self):
        if self._native:
            f = self._frame
            return f[2] if f else 0.0
        y = self._yv12
        return y[4] if y else 0.0

    def latest(self):
        """(BGR frame, frame_id, ts). YV12 -> BGR runs here, in the caller's thread, once per new frame."""
        if self._native:
            self._want = time.time()
            f = self._frame
            return f if f else (None, 0, 0.0)
        y = self._yv12
        if y is None:
            return None, 0, 0.0
        buf, w, h, seq, ts = y
        with self._conv_lock:
            if self._bgr_seq != seq:
                self._bgr = cv2.cvtColor(np.frombuffer(buf, np.uint8).reshape(h * 3 // 2, w), cv2.COLOR_YUV2BGR_YV12)
                self._bgr_seq = seq
            return self._bgr, seq, ts

    def latest_gray(self):
        """(Y plane as a zero-copy uint8 (h, w) view, frame_id, ts) — grayscale without any conversion."""
        if self._native:
            bgr, seq, ts = self.latest()
            return (cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr is not None else None), seq, ts
        y = self._yv12
        if y is None:
            return None, 0, 0.0
        buf, w, h, seq, ts = y
        return np.frombuffer(buf, np.uint8, count=w * h).reshape(h, w), seq, ts

    def read(self, timeout=2.0):
        """Wait for the next new frame -> BGR ndarray (None on timeout)."""
        last, end = self.frame_id, time.time() + timeout
        while self.frame_id == last and time.time() < end and not self._stop.is_set():
            time.sleep(0.005)
        return self.latest()[0] if self.frame_id != last else None

    def status(self):
        alive = self._alive_ts if self._native else self.frame_ts
        return {"connected": self.connected and time.time() - alive < 5, "error": self.error,
                "fps": round(self.fps, 1), "width": self.width, "height": self.height, "backend": "sdk",
                "display": bool(self.cfg.get("hwnd"))}

    # ------------------------------------------------------------ callback side (cheap)
    def _push_raw(self, kind, data):
        try:
            self._raw_q.put_nowait((kind, data))
        except queue.Full:
            self._dropped += 1          # decoder far behind; PlayCtrl resyncs on the next key frame

    def _on_exception(self, kind):
        if kind in (EXCEPTION_PREVIEW, EXCEPTION_EXCHANGE):
            self._broken.set()
        elif kind == PREVIEW_RECONNECTSUCCESS:
            log.info("[%s] SDK preview reconnected", self.name)

    # ------------------------------------------------------------ session thread
    def _display_buf(self):
        return max(1, min(15, int(self.cfg.get("display_buf") or 6)))

    def _open_port(self, head):
        p = SDK.play
        port = LONG(-1)
        if not p.PlayM4_GetPort(byref(port)):
            raise RuntimeError("PlayM4_GetPort failed")
        self._port = port.value
        _BY_PORT[self._port] = self
        hwnd = self.cfg.get("hwnd") or None
        ok = (p.PlayM4_SetStreamOpenMode(self._port, STREAME_REALTIME)
              and p.PlayM4_OpenStream(self._port, head, len(head), PLAY_BUF)
              and p.PlayM4_SetDecCBStream(self._port, 1)                       # video only
              and p.PlayM4_SetDecCallBackExMend(self._port, _DEC_CB, None, 0, self.key))
        if ok and hwnd:
            p.PlayM4_SetDisplayBuf(self._port, self._display_buf())
        ok = ok and p.PlayM4_Play(self._port, hwnd)          # hwnd: PlayCtrl renders; None: decode only
        if not ok:
            raise RuntimeError(f"PlayCtrl open failed ({p.PlayM4_GetLastError(self._port)})")

    def _feed(self):
        """Raw stream packets -> PlayCtrl (between connect and disconnect)."""
        try:
            kind, data = self._raw_q.get(timeout=0.2)
        except queue.Empty:
            return
        if kind == NET_DVR_SYSHEAD:
            if self._port < 0:          # header repeats after an SDK-level reconnect: keep the decoder
                t = time.time()
                self._open_port(data)
                log.info("[%s] PlayCtrl port %s opened in %.0f ms%s", self.name, self._port, (time.time() - t) * 1000,
                         " (renders to window)" if self.cfg.get("hwnd") else "")
            return
        if self._port < 0:
            return
        p = SDK.play
        for _ in range(50):             # PlayCtrl source buffer full -> let the decoder catch up
            if p.PlayM4_InputData(self._port, data, len(data)):
                return
            if self._stop.is_set() or self._restart.is_set():
                return
            time.sleep(0.005)

    def _native_tick(self):
        """Window mode: watch the SDK's own player and pull a snapshot only when someone wants frames."""
        net, play = SDK.net, SDK.play
        if self._port < 0:
            idx = net.NET_DVR_GetRealPlayerIndex(self._play)
            if idx < 0:
                time.sleep(0.05)
                return
            self._port = idx
            log.info("[%s] SDK player port %s (native render, ClientDemo mode)", self.name, idx)
        now = time.time()
        played = play.PlayM4_GetPlayedFrames(self._port)
        if self._played_last is None or played < self._played_last:   # first call / player reset
            self._played_last = played
        elif played > self._played_last:
            self._dec_count += played - self._played_last
            self._played_last = played
            self._alive_ts = now
            if not self.connected:
                w, h = LONG(0), LONG(0)
                if play.PlayM4_GetPictureSize(self._port, byref(w), byref(h)):
                    self.width, self.height = w.value, h.value
                self.connected = True
        elif played == 0 and now - self._alive_ts >= 1.0:   # counter not reported: picture size = alive
            w, h = LONG(0), LONG(0)
            if play.PlayM4_GetPictureSize(self._port, byref(w), byref(h)) and w.value > 0:
                self.width, self.height, self.connected, self._alive_ts = w.value, h.value, True, now
        grab_fps = max(1.0, float(self.cfg.get("grab_fps") or 25))
        if now - self._want < 2.0 and now - self._last_grab >= 1.0 / grab_fps and self._alive_ts:
            self._last_grab = now
            self._grab()
        time.sleep(0.01)

    def _grab(self):
        """Snapshot of the frame on screen -> BGR (PlayCtrl converts in C; ctypes releases the GIL)."""
        net, play = SDK.net, SDK.play
        w, h = LONG(0), LONG(0)
        if not play.PlayM4_GetPictureSize(self._port, byref(w), byref(h)) or w.value <= 0 or h.value <= 0:
            return
        self.width, self.height = w.value, h.value
        need = w.value * h.value * 4 + 4096
        if self._grab_buf is None or len(self._grab_buf) < need:
            self._grab_buf = (c_ubyte * need)()
        size = DWORD(0)
        ok = play.PlayM4_GetBMP(self._port, self._grab_buf, need, byref(size))
        if not ok:                              # fallback through the SDK's capture API
            if not self._capture_mode_set:
                net.NET_DVR_SetCapturePictureMode(0)       # BMP_MODE
                self._capture_mode_set = True
            ok = net.NET_DVR_CapturePictureBlock_New(self._play, self._grab_buf, need, byref(size))
        if not ok or size.value <= 0:
            return
        img = cv2.imdecode(np.frombuffer(self._grab_buf, np.uint8, count=size.value), cv2.IMREAD_COLOR)
        if img is not None:
            self._seq += 1
            self._frame = (img, self._seq, time.time())

    def _connect(self):
        c = self.cfg
        li = NET_DVR_USER_LOGIN_INFO()
        li.sDeviceAddress = str(c["host"]).encode()
        li.wPort = int(c.get("port") or 8000)
        li.sUserName = str(c.get("username", "admin")).encode()
        li.sPassword = str(c.get("password", "")).encode()
        li.bUseAsynLogin = 0
        dev = NET_DVR_DEVICEINFO_V40()
        uid = SDK.net.NET_DVR_Login_V40(byref(li), byref(dev))
        if uid < 0:
            err = SDK.last_error()
            msg = {NET_DVR_PASSWORD_ERROR: "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง",
                   NET_DVR_USER_LOCKED: f"บัญชีถูกล็อก (เหลือ {dev.dwSurplusLockTime} วินาที)",
                   7: f"เชื่อมต่อ {c['host']}:{li.wPort} ไม่ได้"}.get(err, f"NET_DVR_Login_V40 error {err}")
            raise ConnectionError(msg)
        self._user = uid
        _BY_USER[uid] = self
        v30 = dev.struDeviceV30
        self.device = {"serial": bytes(v30.sSerialNumber).split(b"\0")[0].decode(errors="ignore"),
                       "channels": v30.byChanNum, "start_chan": v30.byStartChan,
                       "alarm_in": v30.byAlarmInPortNum, "alarm_out": v30.byAlarmOutPortNum}
        pi = NET_DVR_PREVIEWINFO()
        pi.lChannel = int(c.get("channel") or v30.byStartChan or 1)
        pi.dwStreamType = int(c.get("stream_type", 0))      # 0 = main stream
        pi.dwLinkMode = 0                                   # 0 = TCP
        hwnd = c.get("hwnd") or None
        self._native = bool(hwnd)
        self._frame, self._played_last, self._alive_ts = None, None, 0.0
        # window: the SDK decodes + draws by itself (ClientDemo); no window: our callback feeds our PlayCtrl
        pi.hPlayWnd = hwnd
        pi.bBlocked = 1
        pi.dwDisplayBufNum = self._display_buf()
        self._broken.clear()
        cb = None if self._native else ctypes.cast(_REAL_CB, c_void_p)
        handle = SDK.net.NET_DVR_RealPlay_V40(uid, byref(pi), cb, c_void_p(self.key))
        if handle < 0:
            raise ConnectionError(f"NET_DVR_RealPlay_V40 error {SDK.last_error()} (channel {pi.lChannel})")
        self._play = handle
        _BY_PLAY[handle] = self
        log.info("[%s] HCNetSDK live: %s:%s ch%s %s, display buffer %s%s", self.name, c["host"], li.wPort,
                 pi.lChannel, "main" if pi.dwStreamType == 0 else "sub", pi.dwDisplayBufNum,
                 " (native window, like ClientDemo)" if self._native else "")

    def _disconnect(self):
        """Teardown in dependency order: no more network data -> no more decoding -> logout."""
        net, play = SDK.net, SDK.play
        if self._play >= 0:
            net.NET_DVR_StopRealPlay(self._play)
            _BY_PLAY.pop(self._play, None)
            self._play = -1
        if self._port >= 0 and self._native:      # the SDK owns this port; StopRealPlay released it
            self._port = -1
        if self._port >= 0:
            play.PlayM4_Stop(self._port)
            play.PlayM4_CloseStream(self._port)
            play.PlayM4_FreePort(self._port)
            _BY_PORT.pop(self._port, None)
            self._port = -1
        if self._user >= 0:
            net.NET_DVR_Logout(self._user)
            _BY_USER.pop(self._user, None)
            self._user = -1
        while not self._raw_q.empty():
            try:
                self._raw_q.get_nowait()
            except queue.Empty:
                break
        self.connected = False

    def _run(self):
        backoff = 1.0
        try:
            while not self._stop.is_set():
                self._restart.clear()
                if not self.cfg.get("host"):
                    self.connected, self.error, self._yv12 = False, "ปิดใช้งาน หรือยังไม่ได้ตั้งค่ากล้อง", None
                    self._restart.wait(1.0)
                    continue
                if not SDK.load():
                    self.error = SDK.error
                    self._restart.wait(10)
                    continue
                try:
                    self._connect()
                    self.error, started = "", time.time()
                    n0, t0 = self._dec_count, time.time()
                    while not self._stop.is_set() and not self._restart.is_set():
                        if self._native:
                            self._native_tick()
                        else:
                            self._feed()
                        now = time.time()
                        if now - t0 >= 1.0:                       # decode FPS + frame size
                            self.fps, n0, t0 = (self._dec_count - n0) / (now - t0), self._dec_count, now
                            y = None if self._native else self._yv12
                            if y:
                                self.width, self.height, self.connected = y[1], y[2], True
                        last = (self._alive_ts if self._native else self.frame_ts) or started
                        if self._broken.is_set() and now - last > 3:
                            raise ConnectionError("สัญญาณภาพจากกล้องขาดหาย (SDK exception)")
                        if now - last > self.NO_FRAME_TIMEOUT:
                            raise ConnectionError("ไม่ได้รับภาพจากกล้องเกิน 10 วินาที")
                        if (self._alive_ts if self._native else self.frame_ts) > started:
                            backoff = 1.0
                except Exception as e:
                    self.error = str(e)
                    log.warning("[%s] %s — reconnect in %.0fs", self.name, e, backoff)
                finally:
                    self._disconnect()
                if not self._stop.is_set() and not self._restart.is_set():
                    self._restart.wait(backoff)
                    backoff = min(backoff * 2, 30)
        finally:
            self._disconnect()
            _SESSIONS.pop(self.key, None)


def shutdown():
    """Stop every SDK session and call NET_DVR_Cleanup (safe to call more than once)."""
    SDK.cleanup()


atexit.register(shutdown)
