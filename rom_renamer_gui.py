from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import queue
import re
import shutil
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

try:
    from google import genai
except Exception:  # google-genai はDATのみ利用時には必須ではない
    genai = None


APP_TITLE = "ROM Japanese Renamer"
APP_VERSION = "1.8.0"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
MODEL_CHOICES = ("gemini-3.5-flash-lite", "gemini-3.8-flash")
DEFAULT_MIN_CONFIDENCE = 0.85
DEFAULT_LARGE_HASH_LIMIT_MB = 512
CACHE_FILE = ".rom_jp_title_cache.json"
HISTORY_DIR = "rom_rename_history"
SETTINGS_DIR = Path.home() / ".rom_japanese_renamer"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"
DB_ROOT = SETTINGS_DIR / "databases" / "libretro"
DB_MANIFEST_FILE = DB_ROOT / "manifest.json"
LIBRETRO_REPO = "libretro/libretro-database"
LIBRETRO_BRANCH = "master"
LIBRETRO_API_BASE = f"https://api.github.com/repos/{LIBRETRO_REPO}/contents"
LIBRETRO_SOURCES = {
    "no-intro": "No-Intro",
    "redump": "Redump",
    "mame": "MAME",
    "tosec": "TOSEC",
}
CREDENTIAL_TARGET = "ROM-Japanese-Renamer/GeminiAPIKey"
CREDENTIAL_USERNAME = "Gemini API Key"
DEFAULT_COMPLETED_FOLDER_NAME = "完了"

# 代表的なエミュレータ/ROM形式。ディスクのリンク型セットは別途保護する。
SYSTEM_BY_EXTENSION = {
    ".nes": "Nintendo Famicom / NES",
    ".fds": "Nintendo Famicom Disk System",
    ".qd": "Nintendo Famicom Disk System",
    ".unf": "Nintendo Famicom / NES",
    ".unif": "Nintendo Famicom / NES",
    ".unh": "Nintendo Famicom / NES",
    ".fcn": "Nintendo Famicom Network System",
    ".sfc": "Nintendo Super Famicom / SNES",
    ".smc": "Nintendo Super Famicom / SNES",
    ".gb": "Nintendo Game Boy",
    ".gbc": "Nintendo Game Boy Color",
    ".gba": "Nintendo Game Boy Advance",
    ".vb": "Nintendo Virtual Boy",
    ".n64": "Nintendo 64",
    ".z64": "Nintendo 64",
    ".v64": "Nintendo 64",
    ".nds": "Nintendo DS",
    ".3ds": "Nintendo 3DS",
    ".cci": "Nintendo 3DS",
    ".cia": "Nintendo 3DS",
    ".xci": "Nintendo Switch",
    ".nsp": "Nintendo Switch",
    ".sg": "SEGA SG-1000",
    ".sms": "SEGA Master System",
    ".gg": "SEGA Game Gear",
    ".md": "SEGA Mega Drive / Genesis",
    ".gen": "SEGA Mega Drive / Genesis",
    ".smd": "SEGA Mega Drive / Genesis",
    ".32x": "SEGA 32X",
    ".pce": "NEC PC Engine / TurboGrafx-16",
    ".sgx": "NEC SuperGrafx",
    ".ngp": "SNK Neo Geo Pocket",
    ".ngc": "SNK Neo Geo Pocket Color",
    ".ws": "Bandai WonderSwan",
    ".wsc": "Bandai WonderSwan Color",
    ".a26": "Atari 2600",
    ".a52": "Atari 5200",
    ".a78": "Atari 7800",
    ".lnx": "Atari Lynx",
    ".j64": "Atari Jaguar",
    ".jag": "Atari Jaguar",
    ".col": "ColecoVision",
    ".int": "Intellivision",
    ".vec": "Vectrex",
    ".rom": "MSX / Generic ROM",
    ".mx1": "MSX",
    ".mx2": "MSX2",
    ".d64": "Commodore 64",
    ".t64": "Commodore 64",
    ".prg": "Commodore 64",
    ".crt": "Commodore 64",
    ".adf": "Commodore Amiga",
    ".ipf": "Commodore Amiga",
    ".d88": "PC-88 / PC-98",
    ".d98": "PC-98",
    ".fdi": "PC-98 / Floppy image",
    ".hdi": "PC-98",
    ".nhd": "PC-98",
    ".t98": "PC-98",
    ".xdf": "Sharp X68000 / Floppy image",
    ".dim": "Sharp X68000 / Floppy image",
    ".2hd": "Sharp X68000 / Floppy image",
    ".chd": "Disc / Arcade image",
    ".iso": "Disc image",
    ".cso": "PSP / Disc image",
    ".pbp": "PlayStation / PSP",
    ".gcm": "Nintendo GameCube",
    ".rvz": "Nintendo GameCube / Wii",
    ".gcz": "Nintendo GameCube / Wii",
    ".wbfs": "Nintendo Wii",
    ".wia": "Nintendo Wii",
    ".cdi": "SEGA Dreamcast / Disc image",
    ".bin": "Disc / Cartridge binary",
    ".img": "Disc image",
    ".cue": "Disc set descriptor",
    ".gdi": "SEGA Dreamcast disc descriptor",
    ".ccd": "CloneCD descriptor",
    ".m3u": "Multi-disc playlist",
    ".zip": "ZIP archive",
    ".7z": "7z archive",
}
SUPPORTED_EXTENSIONS = set(SYSTEM_BY_EXTENSION)

# 参照先を伴うため単体リネームすると壊れやすい形式。
DESCRIPTOR_EXTENSIONS = {".cue", ".gdi", ".ccd", ".m3u"}
POTENTIAL_COMPANION_EXTENSIONS = {".bin", ".img", ".sub", ".wav", ".iso"}
ARCHIVE_EXTENSIONS = {".zip", ".7z"}
COMPOUND_EXTENSIONS = (".nkit.iso", ".nkit.gcz")

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "japanese_title": {"type": "string"},
                    "confidence": {"type": "number"},
                    "status": {"type": "string", "enum": ["ok", "unknown"]},
                    "reason": {"type": "string"},
                },
                "required": ["id", "japanese_title", "confidence", "status", "reason"],
            },
        }
    },
    "required": ["results"],
}


@dataclass(frozen=True)
class DatEntry:
    game_name: str
    rom_name: str
    size: int | None
    crc: str
    md5: str
    sha1: str
    system: str
    source: str


@dataclass
class RomRecord:
    path: str
    relative_path: str
    old_name: str
    extension: str
    system: str = "Unknown"
    dat_title: str = ""
    dat_rom_name: str = ""
    dat_source: str = ""
    match_method: str = ""
    japanese_title: str = ""
    new_name: str = ""
    confidence: float = 0.0
    source: str = ""
    status: str = "未処理"
    note: str = ""
    protected: bool = False
    enabled: bool = False
    variant_type: str = "標準候補"
    main_candidate: bool = True
    variant_excluded: bool = False
    variant_reason: str = ""
    region: str = "不明"
    japanese_region: bool = False
    region_excluded: bool = False
    region_reason: str = ""

    def key(self) -> str:
        return self.path.casefold()


class DatIndex:
    def __init__(self) -> None:
        self.by_sha1: dict[str, list[DatEntry]] = {}
        self.by_md5: dict[str, list[DatEntry]] = {}
        self.by_crc_size: dict[tuple[str, int], list[DatEntry]] = {}
        self.entry_count = 0
        self.sources: list[str] = []

    @staticmethod
    def _append(index: dict, key, entry: DatEntry) -> None:
        if not key:
            return
        index.setdefault(key, []).append(entry)

    def add(self, entry: DatEntry) -> None:
        self.entry_count += 1
        self._append(self.by_sha1, entry.sha1.lower(), entry)
        self._append(self.by_md5, entry.md5.lower(), entry)
        if entry.crc and entry.size is not None:
            self._append(self.by_crc_size, (entry.crc.lower(), entry.size), entry)

    @staticmethod
    def _collapse(entries: list[DatEntry] | None) -> DatEntry | None:
        if not entries:
            return None
        if len(entries) == 1:
            return entries[0]
        # 同一ゲームに属する重複エントリなら安全に1件へまとめる。
        names = {(e.game_name.casefold(), e.system.casefold()) for e in entries}
        return entries[0] if len(names) == 1 else None

    def match(self, *, size: int, crc: str = "", md5: str = "", sha1: str = "") -> tuple[DatEntry | None, str]:
        if sha1:
            hit = self._collapse(self.by_sha1.get(sha1.lower()))
            if hit:
                return hit, "SHA1"
        if md5:
            hit = self._collapse(self.by_md5.get(md5.lower()))
            if hit:
                return hit, "MD5"
        if crc:
            hit = self._collapse(self.by_crc_size.get((crc.lower(), size)))
            if hit:
                return hit, "CRC32+SIZE"
        return None, ""


def local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


class CredentialManagerError(RuntimeError):
    """Windows資格情報マネージャー操作に失敗したときの例外。"""


def _require_windows_credential_api():
    """Windows Credential API を遅延ロードする。非Windowsでは例外。"""
    if os.name != "nt":
        raise CredentialManagerError("Windows資格情報マネージャーはWindowsでのみ利用できます。")

    import ctypes
    from ctypes import wintypes

    class CREDENTIALW(ctypes.Structure):
        pass

    PCREDENTIALW = ctypes.POINTER(CREDENTIALW)
    CREDENTIALW._fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]

    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)

    advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL

    advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(PCREDENTIALW),
    ]
    advapi32.CredReadW.restype = wintypes.BOOL

    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL

    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    advapi32.CredFree.restype = None

    return ctypes, wintypes, advapi32, CREDENTIALW, PCREDENTIALW


def save_api_key_to_windows_credential(api_key: str) -> None:
    """APIキーを現在のWindowsユーザーの汎用資格情報として保存する。"""
    api_key = api_key.strip()
    if not api_key:
        raise CredentialManagerError("保存するAPIキーが空です。")

    ctypes, _wintypes, advapi32, CREDENTIALW, _PCREDENTIALW = _require_windows_credential_api()

    # Generic Credential のCredentialBlobは任意バイト列。UTF-16LEで保存する。
    blob_bytes = api_key.encode("utf-16-le")
    blob = ctypes.create_string_buffer(blob_bytes, len(blob_bytes))

    credential = CREDENTIALW()
    credential.Flags = 0
    credential.Type = 1  # CRED_TYPE_GENERIC
    credential.TargetName = CREDENTIAL_TARGET
    credential.Comment = "ROM Japanese Renamer - Gemini API key"
    credential.CredentialBlobSize = len(blob_bytes)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE (同じWindowsユーザーで再ログイン後も保持)
    credential.AttributeCount = 0
    credential.Attributes = None
    credential.TargetAlias = None
    credential.UserName = CREDENTIAL_USERNAME

    if not advapi32.CredWriteW(ctypes.byref(credential), 0):
        error = ctypes.get_last_error()
        raise CredentialManagerError(f"資格情報の保存に失敗しました (WinError {error})")


def load_api_key_from_windows_credential() -> str:
    """Windows資格情報マネージャーから保存済みAPIキーを読み込む。未保存なら空文字。"""
    ctypes, _wintypes, advapi32, _CREDENTIALW, PCREDENTIALW = _require_windows_credential_api()
    cred_ptr = PCREDENTIALW()

    if not advapi32.CredReadW(CREDENTIAL_TARGET, 1, 0, ctypes.byref(cred_ptr)):
        error = ctypes.get_last_error()
        if error == 1168:  # ERROR_NOT_FOUND
            return ""
        raise CredentialManagerError(f"資格情報の読込に失敗しました (WinError {error})")

    try:
        cred = cred_ptr.contents
        if not cred.CredentialBlob or cred.CredentialBlobSize == 0:
            return ""
        raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        try:
            return raw.decode("utf-16-le").rstrip("\x00")
        except UnicodeDecodeError:
            # 旧版や手動登録などUTF-8で保存されていた場合にも可能な範囲で対応。
            return raw.decode("utf-8").rstrip("\x00")
    finally:
        advapi32.CredFree(ctypes.cast(cred_ptr, ctypes.c_void_p))


def delete_api_key_from_windows_credential() -> bool:
    """保存済みAPIキーを削除する。削除済み/未保存ならFalse。"""
    ctypes, _wintypes, advapi32, _CREDENTIALW, _PCREDENTIALW = _require_windows_credential_api()
    if advapi32.CredDeleteW(CREDENTIAL_TARGET, 1, 0):
        return True

    error = ctypes.get_last_error()
    if error == 1168:  # ERROR_NOT_FOUND
        return False
    raise CredentialManagerError(f"資格情報の削除に失敗しました (WinError {error})")


def normalize_hash(value: str | None) -> str:
    return (value or "").strip().lower()


def iter_dat_streams(path: Path):
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path, "r") as zf:
            members = [n for n in zf.namelist() if Path(n).suffix.lower() in {".dat", ".xml"}]
            for member in members:
                with zf.open(member, "r") as stream:
                    yield stream, f"{path.name}:{member}"
    else:
        with path.open("rb") as stream:
            yield stream, path.name


def _decode_dat_bytes(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp932", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _tokenize_clrmamepro(text: str):
    """Quote-aware tokenizer for clrmamepro/Logiqx style text DATs."""
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c in "()":
            yield c
            i += 1
            continue
        if c == '"':
            i += 1
            buf = []
            while i < n:
                c = text[i]
                if c == '"':
                    i += 1
                    break
                if c == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    if nxt in {'"', "\\"}:
                        buf.append(nxt)
                        i += 2
                        continue
                buf.append(c)
                i += 1
            yield "".join(buf)
            continue

        start = i
        while i < n and (not text[i].isspace()) and text[i] not in "()":
            i += 1
        yield text[start:i]


class _TokenStream:
    def __init__(self, iterable):
        self._iter = iter(iterable)
        self._buffer = None

    def peek(self):
        if self._buffer is None:
            try:
                self._buffer = next(self._iter)
            except StopIteration:
                return None
        return self._buffer

    def pop(self):
        value = self.peek()
        self._buffer = None
        return value


def _parse_cmp_block(ts: _TokenStream):
    """Parse contents after an opening '(' into repeated key/value pairs."""
    items = []
    while True:
        token = ts.pop()
        if token is None or token == ")":
            break
        if token == "(":
            # malformed anonymous block; still consume it safely
            items.append(("", _parse_cmp_block(ts)))
            continue

        key = token
        nxt = ts.peek()
        if nxt == "(":
            ts.pop()
            value = _parse_cmp_block(ts)
        elif nxt is None or nxt == ")":
            value = ""
        else:
            value = ts.pop()
        items.append((key, value))
    return items


def _first_field(items, key: str, default: str = "") -> str:
    key_cf = key.casefold()
    for k, value in items:
        if k.casefold() == key_cf and isinstance(value, str):
            return value
    return default


def _parse_text_dat(raw: bytes, source_name: str, index: DatIndex) -> int:
    text = _decode_dat_bytes(raw)
    ts = _TokenStream(_tokenize_clrmamepro(text))
    system_name = ""

    while True:
        token = ts.pop()
        if token is None:
            break
        if token in {"(", ")"}:
            continue
        if ts.peek() != "(":
            continue
        ts.pop()
        block = _parse_cmp_block(ts)
        kind = token.casefold()

        if kind in {"clrmamepro", "datafile"}:
            system_name = _first_field(block, "description") or _first_field(block, "name") or system_name
            continue
        if kind not in {"game", "machine", "software"}:
            continue

        game_name = _first_field(block, "description") or _first_field(block, "name")
        for key, value in block:
            if key.casefold() != "rom" or not isinstance(value, list):
                continue
            rom_name = _first_field(value, "name")
            size_raw = _first_field(value, "size")
            try:
                size = int(size_raw) if size_raw else None
            except ValueError:
                size = None
            entry = DatEntry(
                game_name=game_name,
                rom_name=rom_name,
                size=size,
                crc=normalize_hash(_first_field(value, "crc")),
                md5=normalize_hash(_first_field(value, "md5")),
                sha1=normalize_hash(_first_field(value, "sha1")),
                system=system_name,
                source=source_name,
            )
            if entry.crc or entry.md5 or entry.sha1:
                index.add(entry)
    return index.entry_count


def parse_dat_file(path: Path, index: DatIndex) -> int:
    before = index.entry_count
    for stream, source_name in iter_dat_streams(path):
        raw = stream.read()
        stripped = raw.lstrip()
        if not stripped:
            continue
        probe = stripped[3:] if stripped.startswith(b"\xef\xbb\xbf") else stripped

        # Logiqx XML DAT
        if probe.startswith(b"<") or probe.startswith(b"<?xml"):
            system_name = ""
            try:
                context = ET.iterparse(io.BytesIO(raw), events=("end",))
                for _event, elem in context:
                    tag = local_tag(elem.tag)
                    if tag == "header":
                        for child in list(elem):
                            ctag = local_tag(child.tag)
                            if ctag in {"name", "description"} and (child.text or "").strip():
                                system_name = (child.text or "").strip()
                                break
                        elem.clear()
                        continue

                    if tag not in {"game", "machine", "software"}:
                        continue

                    game_name = (elem.attrib.get("name") or "").strip()
                    description = ""
                    for child in list(elem):
                        if local_tag(child.tag) == "description" and (child.text or "").strip():
                            description = (child.text or "").strip()
                            break
                    display_name = description or game_name

                    for child in list(elem):
                        if local_tag(child.tag) != "rom":
                            continue
                        attrs = child.attrib
                        rom_name = (attrs.get("name") or "").strip()
                        try:
                            size = int(attrs["size"]) if attrs.get("size") else None
                        except ValueError:
                            size = None
                        entry = DatEntry(
                            game_name=display_name,
                            rom_name=rom_name,
                            size=size,
                            crc=normalize_hash(attrs.get("crc")),
                            md5=normalize_hash(attrs.get("md5")),
                            sha1=normalize_hash(attrs.get("sha1")),
                            system=system_name,
                            source=source_name,
                        )
                        if entry.crc or entry.md5 or entry.sha1:
                            index.add(entry)
                    elem.clear()
            except ET.ParseError as exc:
                raise ValueError(f"DAT/XML解析エラー: {source_name}: {exc}") from exc
        else:
            _parse_text_dat(raw, source_name, index)

    if path.name not in index.sources:
        index.sources.append(path.name)
    return index.entry_count - before


def _http_json(url: str, timeout: int = 30):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"ROM-Japanese-Renamer/{APP_VERSION}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")), dict(response.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 403 and "rate limit" in body.lower():
            raise RuntimeError("GitHub APIの未認証レート制限に達しました。しばらく待ってから再試行してください。") from exc
        raise RuntimeError(f"GitHub APIエラー HTTP {exc.code}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHubへ接続できません: {exc.reason}") from exc


def _http_download(url: str, destination: Path, timeout: int = 60) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": f"ROM-Japanese-Renamer/{APP_VERSION}"})
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".part")
    sha256 = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response, temp.open("wb") as f:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                sha256.update(chunk)
                total += len(chunk)
        temp.replace(destination)
        return total, sha256.hexdigest()
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def load_db_manifest() -> dict:
    try:
        if DB_MANIFEST_FILE.exists():
            data = json.loads(DB_MANIFEST_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def save_db_manifest(data: dict) -> None:
    DB_ROOT.mkdir(parents=True, exist_ok=True)
    DB_MANIFEST_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def discover_managed_dat_files() -> list[Path]:
    if not DB_ROOT.exists():
        return []
    result = []
    for p in DB_ROOT.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".dat", ".xml", ".zip"} and p != DB_MANIFEST_FILE:
            result.append(p)
    return sorted(result, key=lambda x: str(x).casefold())


def _github_list_dir(path: str) -> list[dict]:
    quoted = "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
    url = f"{LIBRETRO_API_BASE}/{quoted}?ref={urllib.parse.quote(LIBRETRO_BRANCH)}"
    data, _headers = _http_json(url)
    if not isinstance(data, list):
        raise RuntimeError(f"GitHubのDAT一覧応答が想定形式ではありません: {path}")
    return data


def fetch_libretro_catalog(sources: Iterable[str], progress: Callable[[str], None] | None = None) -> list[dict]:
    progress = progress or (lambda _m: None)
    catalog: list[dict] = []

    def walk(source: str, repo_path: str) -> None:
        items = _github_list_dir(repo_path)
        for item in items:
            item_type = item.get("type")
            item_path = str(item.get("path", ""))
            if item_type == "dir":
                walk(source, item_path)
                continue
            if item_type != "file":
                continue
            name = str(item.get("name", ""))
            if Path(name).suffix.lower() not in {".dat", ".xml"}:
                continue
            catalog.append({
                "source": source,
                "source_label": LIBRETRO_SOURCES.get(source, source),
                "name": name,
                "path": item_path,
                "sha": str(item.get("sha", "")),
                "size": int(item.get("size", 0) or 0),
                "download_url": str(item.get("download_url", "")),
            })

    for source in sources:
        if source not in LIBRETRO_SOURCES:
            continue
        progress(f"GitHubから {LIBRETRO_SOURCES[source]} のDAT一覧を取得中...")
        walk(source, f"metadat/{source}")
    return sorted(catalog, key=lambda x: (x["source"], x["name"].casefold()))


def managed_dat_destination(item: dict) -> Path:
    source = str(item["source"])
    # path basename can collide in nested dirs. Preserve subpath below metadat/source.
    prefix = f"metadat/{source}/"
    rel = str(item["path"])
    if rel.startswith(prefix):
        rel = rel[len(prefix):]
    return DB_ROOT / source / Path(rel)


def install_libretro_items(items: list[dict], progress: Callable[[str], None] | None = None) -> tuple[int, int]:
    progress = progress or (lambda _m: None)
    manifest = load_db_manifest()
    files = manifest.setdefault("files", {})
    updated = 0
    skipped = 0

    for pos, item in enumerate(items, 1):
        dest = managed_dat_destination(item)
        key = str(dest.relative_to(DB_ROOT)).replace("\\", "/")
        known = files.get(key, {}) if isinstance(files.get(key), dict) else {}
        remote_sha = str(item.get("sha", ""))
        if dest.exists() and remote_sha and known.get("remote_sha") == remote_sha:
            skipped += 1
            progress(f"最新: {item['name']} ({pos}/{len(items)})")
            continue
        url = str(item.get("download_url", ""))
        if not url:
            raise RuntimeError(f"ダウンロードURLがありません: {item['name']}")
        progress(f"取得中: {item['source_label']} / {item['name']} ({pos}/{len(items)})")
        size, sha256 = _http_download(url, dest)
        files[key] = {
            "source": item["source"],
            "repo_path": item["path"],
            "remote_sha": remote_sha,
            "sha256": sha256,
            "size": size,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        updated += 1
        save_db_manifest(manifest)

    manifest["last_checked_at"] = datetime.now().isoformat(timespec="seconds")
    manifest["repository"] = LIBRETRO_REPO
    manifest["branch"] = LIBRETRO_BRANCH
    save_db_manifest(manifest)
    return updated, skipped


def remove_managed_dat(path: Path) -> None:
    try:
        rel = str(path.relative_to(DB_ROOT)).replace("\\", "/")
    except ValueError:
        return
    path.unlink(missing_ok=True)
    manifest = load_db_manifest()
    files = manifest.get("files")
    if isinstance(files, dict):
        files.pop(rel, None)
        save_db_manifest(manifest)

def sanitize_windows_filename(title: str) -> str:
    title = (title or "").strip()
    title = re.sub(r'[<>:"/\\|?*]', "", title)
    title = re.sub(r"[\x00-\x1f]", "", title)
    title = re.sub(r"\s+", " ", title).rstrip(" .")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if title.upper() in reserved:
        title = "_" + title
    return title[:220].rstrip(" .")


def strip_rom_tags(name: str) -> str:
    stem = Path(name).stem
    # No-Intro/GoodTools系の代表的な末尾タグを削る。ゲーム名中の括弧は完全には消さない。
    previous = None
    while previous != stem:
        previous = stem
        stem = re.sub(r"\s*(\([^()]*(Japan|USA|Europe|World|Rev|Beta|Proto|En|Ja|Unl|Demo|Sample|Alt)[^()]*\)|\[[^\]]+\])\s*$", "", stem, flags=re.I)
    return stem.strip(" -_")


# GoodTools/No-Intro系の派生・非標準ROM判定。
# [!] は検証済みGood Dump。Revは公式改訂版なので既定ではメイン候補として残す。
_GOODTOOLS_VARIANT_RULES = (
    ("Bad Dump", re.compile(r"\[b(?:ad(?:\s*dump)?)?\d*[^\]]*\]", re.I), "Bad Dumpタグ [b]"),
    ("Alternate Dump", re.compile(r"\[a\d*[^\]]*\]", re.I), "Alternate Dumpタグ [a]"),
    ("Hack", re.compile(r"\[h\d*[^\]]*\]", re.I), "Hackタグ [h]"),
    ("Fixed", re.compile(r"\[f\d*[^\]]*\]", re.I), "Fixed Dumpタグ [f]"),
    ("Overdump", re.compile(r"\[o\d*[^\]]*\]", re.I), "Overdumpタグ [o]"),
    ("Trainer", re.compile(r"\[t\d*[^\]]*\]", re.I), "Trainer付きタグ [t]"),
    ("Translation", re.compile(r"\[T[+-][^\]]*\]", re.I), "翻訳パッチ版タグ [T+/-]"),
    ("Cracked/Modified", re.compile(r"\[(?:cr|p)\d*[^\]]*\]", re.I), "改変/海賊版系タグ"),
)
_NON_RETAIL_RULES = (
    ("Beta", re.compile(r"(?:^|[\s(\[_-])beta(?:\s*\d+)?(?:$|[\s)\]_-])", re.I), "Beta版"),
    ("Prototype", re.compile(r"(?:^|[\s(\[_-])(?:proto|prototype)(?:\s*\d+)?(?:$|[\s)\]_-])", re.I), "Prototype版"),
    ("Demo", re.compile(r"(?:^|[\s(\[_-])demo(?:$|[\s)\]_-])", re.I), "Demo版"),
    ("Sample", re.compile(r"(?:^|[\s(\[_-])sample(?:$|[\s)\]_-])", re.I), "Sample版"),
    ("Kiosk/Test", re.compile(r"(?:^|[\s(\[_-])(?:kiosk|test\s*(?:program|version)?)(?:$|[\s)\]_-])", re.I), "Kiosk/Test版"),
)
_VERIFIED_GOOD_RE = re.compile(r"\[!\]", re.I)
_REVISION_RE = re.compile(r"\((?:rev(?:ision)?\s*[A-Z0-9.]+|v(?:er(?:sion)?)?\s*\d+(?:\.\d+)*)\)", re.I)


def classify_rom_variant(record: RomRecord) -> tuple[str, bool, str]:
    """ROMをメイン候補/派生ROMに分類する。DAT一致をファイル名タグより優先する。"""
    canonical = " ".join(x for x in (record.dat_title, record.dat_rom_name) if x).strip()
    filename = record.old_name

    # DAT側にBeta/Proto等が明記されている場合は、ハッシュ一致していても非製品版として除外する。
    for label, pattern, reason in _NON_RETAIL_RULES:
        if canonical and pattern.search(canonical):
            return label, False, f"DAT情報で{reason}と判定"

    # DAT側そのものにGoodTools型の派生タグがある場合も除外。
    for label, pattern, reason in _GOODTOOLS_VARIANT_RULES:
        if canonical and pattern.search(canonical):
            return label, False, f"DAT情報で{reason}と判定"

    # No-Intro/Redump等のハッシュ一致が得られ、DAT側が非製品版でなければ内容を優先。
    # 元ファイル名に誤った [b1] 等が付いていても、ハッシュ一致した正規内容はメイン候補にする。
    if record.dat_title and record.match_method:
        if _REVISION_RE.search(canonical):
            return "公式Revision", True, f"DAT {record.match_method} 一致（公式改訂版）"
        return "DAT検証済み", True, f"DAT {record.match_method} 一致"

    # DATで確認できない場合はGoodToolsタグを補助判定に利用する。
    for label, pattern, reason in _GOODTOOLS_VARIANT_RULES:
        if pattern.search(filename):
            return label, False, reason

    for label, pattern, reason in _NON_RETAIL_RULES:
        if pattern.search(filename):
            return label, False, reason

    if _VERIFIED_GOOD_RE.search(filename):
        return "Good Dump [!]", True, "GoodTools検証済みタグ [!]"

    if _REVISION_RE.search(filename):
        return "公式Revision", True, "公式改訂版はBad Dumpではないため対象"

    return "標準候補", True, "派生ROMを示すタグなし"


def apply_main_rom_policy(record: RomRecord, main_rom_only: bool) -> None:
    variant_type, main_candidate, reason = classify_rom_variant(record)
    record.variant_type = variant_type
    record.main_candidate = main_candidate
    record.variant_reason = reason
    record.variant_excluded = bool(main_rom_only and not main_candidate)

    if record.variant_excluded:
        record.enabled = False
        if not record.protected:
            record.status = "派生ROM除外"
        marker = f"メインROMのみ: {reason}"
        if marker not in record.note:
            record.note = (record.note + " / " if record.note else "") + marker



_REGION_GROUP_RE = re.compile(r"[\(\[]([^\)\]]+)[\)\]]")
_JAPAN_WORD_RE = re.compile(r"(?i)(?:^|[\s,;/+\-])(?:japan|japanese|jpn|jp)(?:$|[\s,;/+\-])")
_FOREIGN_REGION_RE = re.compile(
    r"(?i)(?:^|[\s,;/+\-])(?:"
    r"usa|united states|europe|world|asia|australia|brazil|canada|"
    r"france|germany|italy|spain|portugal|netherlands|sweden|finland|"
    r"denmark|norway|russia|korea|south korea|china|taiwan|hong kong|"
    r"united kingdom|uk|mexico|argentina"
    r")(?:$|[\s,;/+\-])"
)


def _region_from_text(text: str) -> tuple[str, bool, str] | None:
    """ファイル名/DAT名に明示された地域タグだけを判定する。

    GoodTools の (J)/(U)/(E)/(JU)/(JUE) と、
    No-Intro/Redump の (Japan)/(USA)/(Europe) 等を対象とする。
    地域が明示されていない場合は None。
    """
    if not text:
        return None

    groups = [g.strip() for g in _REGION_GROUP_RE.findall(text)]
    if not groups:
        return None

    # 日本を含むタグを最優先。
    for group in groups:
        if _JAPAN_WORD_RE.search(group):
            if _FOREIGN_REGION_RE.search(group):
                return "日本含む", True, f"地域タグ: ({group})"
            return "日本", True, f"地域タグ: ({group})"

        compact = re.sub(r"[^A-Za-z]", "", group).upper()
        # GoodTools: J / JU / JE / JUE 等。言語タグ Ja はここに入れない。
        if compact and len(compact) <= 4 and set(compact) <= set("JUEA") and "J" in compact:
            label = "日本" if compact == "J" else "日本含む"
            return label, True, f"GoodTools地域タグ: ({group})"

    # 明示された海外地域。
    for group in groups:
        if _FOREIGN_REGION_RE.search(group):
            return "海外", False, f"海外地域タグ: ({group})"

        compact = re.sub(r"[^A-Za-z]", "", group).upper()
        # GoodTools の代表的な地域コード。PD/Rev/En 等を誤判定しないよう単独コード中心。
        if compact in {"U", "E", "UE", "EU", "A", "F", "G", "I", "S", "K", "C"}:
            return "海外", False, f"GoodTools海外地域タグ: ({group})"

    return None


def classify_rom_region(record: RomRecord) -> tuple[str, bool, str]:
    """DATを最優先し、日本版/海外版/地域不明を判定する。"""
    # DAT一致時はDAT側の地域情報を最優先する。
    if record.dat_title or record.dat_rom_name:
        for source_name, value in (
            ("DATタイトル", record.dat_title),
            ("DAT ROM名", record.dat_rom_name),
        ):
            hit = _region_from_text(value)
            if hit is not None:
                region, is_japan, reason = hit
                return region, is_japan, f"{source_name} / {reason}"

        # DATに地域明記がない場合のみ元ファイル名を補助利用。
        hit = _region_from_text(record.old_name)
        if hit is not None:
            region, is_japan, reason = hit
            return region, is_japan, f"ファイル名補助 / {reason}"

        return "不明", False, "DAT/ファイル名に地域タグなし"

    # DAT未一致ではファイル名だけで判定。
    hit = _region_from_text(record.old_name)
    if hit is not None:
        return hit

    return "不明", False, "ファイル名に地域タグなし"


def apply_region_policy(record: RomRecord, japan_only: bool) -> None:
    region, is_japan, reason = classify_rom_region(record)
    record.region = region
    record.japanese_region = is_japan
    record.region_reason = reason
    record.region_excluded = bool(japan_only and not is_japan)

    if record.region_excluded:
        record.enabled = False
        # 旧CSV/旧候補に海外ROMの日本語化結果が残っていても、厳格モードでは表示・適用しない。
        record.japanese_title = ""
        record.new_name = ""
        record.confidence = 0.0
        if not record.protected and not record.variant_excluded:
            record.status = "海外ROM除外" if region == "海外" else "地域不明除外"
        marker = f"日本版のみ: {reason}"
        if marker not in record.note:
            record.note = (record.note + " / " if record.note else "") + marker


def has_japanese(text: str) -> bool:
    return bool(re.search(r"[ぁ-んァ-ヶ一-龯々ー]", text or ""))


def compound_extension(path: Path) -> str:
    lower = path.name.lower()
    for ext in COMPOUND_EXTENSIONS:
        if lower.endswith(ext):
            return path.name[-len(ext):]
    return path.suffix


def infer_system_from_extension(ext: str) -> str:
    return SYSTEM_BY_EXTENSION.get(ext.lower(), "Unknown")


def supported_member_extension(name: str) -> str:
    lower = name.lower()
    for ext in COMPOUND_EXTENSIONS:
        if lower.endswith(ext):
            return ext
    return Path(name).suffix.lower()


def _path_is_within(path: Path, folder: Path | None) -> bool:
    if folder is None:
        return False
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except (ValueError, OSError):
        return False


def _path_is_in_named_folder(path: Path, root: Path, folder_name: str | None) -> bool:
    """root配下で、完了フォルダ等の配下にあるパスか判定する。

    v1.7.0 ではサブフォルダの完了先を「サブフォルダ名＋完了」として
    親フォルダに作るため、folder_name が「完了」の場合は
    「完了」そのものに加えて「SFC完了」「Japan完了」等も除外する。
    v1.6.0以前の「SFC\完了」も引き続き除外できる。
    """
    if not folder_name:
        return False
    try:
        rel = path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    target = folder_name.casefold()

    def matches(part: str) -> bool:
        folded = part.casefold()
        if folded == target:
            return True
        if target == DEFAULT_COMPLETED_FOLDER_NAME.casefold():
            return folded.endswith(target) and len(folded) > len(target)
        return False

    # ファイル名そのものではなく親ディレクトリだけを見る。
    return any(matches(part) for part in rel.parts[:-1])


def collect_companion_references(
    root: Path,
    recursive: bool,
    exclude_dir: Path | None = None,
    exclude_folder_name: str | None = None,
) -> set[str]:
    refs: set[str] = set()
    iterator = root.rglob("*") if recursive else root.iterdir()
    descriptors = [
        p for p in iterator
        if p.is_file()
        and p.suffix.lower() in DESCRIPTOR_EXTENSIONS
        and not _path_is_within(p, exclude_dir)
        and not _path_is_in_named_folder(p, root, exclude_folder_name)
    ]

    for path in descriptors:
        ext = path.suffix.lower()
        try:
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
        except Exception:
            continue

        if ext == ".cue":
            for m in re.finditer(r'^\s*FILE\s+(?:"([^"]+)"|(\S+))', text, re.I | re.M):
                ref = m.group(1) or m.group(2)
                refs.add(str((path.parent / ref).resolve()).casefold())
        elif ext == ".gdi":
            for line in text.splitlines()[1:]:
                parts = re.findall(r'"[^"]+"|\S+', line)
                if len(parts) >= 5:
                    ref = parts[4].strip('"')
                    refs.add(str((path.parent / ref).resolve()).casefold())
        elif ext == ".m3u":
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    refs.add(str((path.parent / line).resolve()).casefold())
        elif ext == ".ccd":
            for sibling_ext in (".img", ".sub"):
                refs.add(str(path.with_suffix(sibling_ext).resolve()).casefold())
    return refs


def file_hashes(path: Path) -> tuple[int, str, str, str]:
    crc = 0
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    size = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            size += len(chunk)
            crc = zlib.crc32(chunk, crc)
            md5.update(chunk)
            sha1.update(chunk)
    return size, f"{crc & 0xffffffff:08x}", md5.hexdigest(), sha1.hexdigest()


def zip_single_rom_info(path: Path) -> tuple[zipfile.ZipInfo | None, str, int]:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            candidates = []
            for info in zf.infolist():
                if info.is_dir():
                    continue
                ext = supported_member_extension(info.filename)
                if ext in SUPPORTED_EXTENSIONS - ARCHIVE_EXTENSIONS - DESCRIPTOR_EXTENSIONS:
                    candidates.append(info)
            if len(candidates) == 1:
                info = candidates[0]
                return info, supported_member_extension(info.filename), 1
            return None, "", len(candidates)
    except (zipfile.BadZipFile, OSError):
        return None, "", -1


def zip_member_hashes(path: Path, member: zipfile.ZipInfo) -> tuple[int, str, str, str]:
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    with zipfile.ZipFile(path, "r") as zf:
        with zf.open(member, "r") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                md5.update(chunk)
                sha1.update(chunk)
    return member.file_size, f"{member.CRC & 0xffffffff:08x}", md5.hexdigest(), sha1.hexdigest()


def load_cache(root: Path) -> dict:
    path = root / CACHE_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_cache(root: Path, cache: dict) -> None:
    path = root / CACHE_FILE
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def cache_key(record: RomRecord) -> str:
    # DAT同定結果が変わった場合にはAI結果も分離する。
    return f"{record.relative_path}|{record.dat_title}|{record.system}"


def lookup_cached_result(cache: dict, record: RomRecord) -> tuple[dict | None, str]:
    """パス変更後でも、安全に既存Geminiキャッシュを再利用する。"""
    exact = cache.get(cache_key(record))
    if isinstance(exact, dict):
        return exact, "exact"

    def valid_items(predicate) -> list[dict]:
        found: list[dict] = []
        for key, value in cache.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            parts = key.rsplit("|", 2)
            if len(parts) != 3:
                continue
            _old_rel, dat_title, system = parts
            if not predicate(dat_title, system, value):
                continue
            if str(value.get("status", "")) != "ok":
                continue
            title = sanitize_windows_filename(str(value.get("japanese_title", "")))
            if not title:
                continue
            found.append(value)
        return found

    # DATで同じゲーム・同じシステムと再同定できた場合は、旧パスのキャッシュを再利用できる。
    if record.dat_title:
        matches = valid_items(
            lambda dat_title, system, _value:
                dat_title == record.dat_title and system == record.system
        )
        titles = {sanitize_windows_filename(str(v.get("japanese_title", ""))).casefold() for v in matches}
        if matches and len(titles) == 1:
            return max(matches, key=lambda v: float(v.get("confidence", 0.0) or 0.0)), "dat-relocated"

    # DATが無い場合でも、現在のファイル名が過去に確定した日本語タイトルそのものなら再利用する。
    current_base = record.old_name
    if record.extension and current_base.lower().endswith(record.extension.lower()):
        current_base = current_base[:-len(record.extension)]
    current_base = sanitize_windows_filename(current_base).casefold()
    if current_base:
        matches = valid_items(
            lambda _dat_title, system, value:
                system == record.system
                and sanitize_windows_filename(str(value.get("japanese_title", ""))).casefold() == current_base
        )
        titles = {sanitize_windows_filename(str(v.get("japanese_title", ""))).casefold() for v in matches}
        if matches and len(titles) == 1:
            return max(matches, key=lambda v: float(v.get("confidence", 0.0) or 0.0)), "title-relocated"

    return None, ""


def build_ai_prompt(items: list[dict]) -> str:
    payload = json.dumps(items, ensure_ascii=False, indent=2)
    return f"""
あなたは日本で発売されたゲームの公式タイトル照合担当です。
次のゲーム情報を、日本で正式発売された当時の「公式な日本語タイトル」に変換してください。

重要:
- dat_title がある場合、そのDAT照合でゲーム同定は済んでいるので最優先の根拠にしてください。
- dat_title が空の場合だけ file_name から慎重に推定してください。
- 単純な直訳ではなく、日本版で実際に使われた正式名称を返してください。
- サブタイトル、ナンバリング、ローマ数字、中黒、長音などの公式表記をできるだけ維持してください。
- 地域タグ、Rev、Beta、Proto、ROMセット記号、拡張子、メーカー名、発売年は追加しないでください。
- 入力はアプリ側で日本版と判定済みですが、海外版だと判明した場合は status="unknown" にしてください。\n- 日本未発売、同定不能、複数候補で断定できない場合は status="unknown" にしてください。
- japanese_title はタイトルだけにしてください。
- confidence は0.0～1.0です。

入力データ:
{payload}
""".strip()


def query_gemini(api_key: str, model: str, records: list[RomRecord], progress: Callable[[str], None]) -> dict[str, dict]:
    if genai is None:
        raise RuntimeError("google-genai がインストールされていません。requirements.txt をインストールしてください。")
    client = genai.Client(api_key=api_key)
    result_map: dict[str, dict] = {}
    batch_size = 20

    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        items = []
        for i, record in enumerate(batch):
            items.append({
                "id": str(start + i),
                "file_name": record.old_name,
                "system": record.system,
                "region": record.region,
                "dat_title": record.dat_title,
                "dat_rom_name": record.dat_rom_name,
            })
        prompt = build_ai_prompt(items)
        last_error = None
        for attempt in range(1, 4):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config={
                        "temperature": 0.1,
                        "response_mime_type": "application/json",
                        "response_schema": RESULT_SCHEMA,
                    },
                )
                if not response.text:
                    raise RuntimeError("Geminiから空の応答が返されました。")
                parsed = json.loads(response.text)
                for item in parsed.get("results", []):
                    idx = item.get("id", "")
                    try:
                        absolute_idx = int(idx)
                    except (TypeError, ValueError):
                        continue
                    if start <= absolute_idx < start + len(batch):
                        rec = records[absolute_idx]
                        result_map[rec.key()] = item
                break
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    wait = 2 ** attempt
                    progress(f"Gemini APIエラー。{wait}秒後に再試行: {exc}")
                    time.sleep(wait)
        else:
            progress(f"Geminiバッチ失敗: {last_error}")

        progress(f"Gemini: {min(start + len(batch), len(records))}/{len(records)} 件")
        if start + batch_size < len(records):
            time.sleep(0.5)
    return result_map


def create_new_name(record: RomRecord, title: str) -> str:
    safe = sanitize_windows_filename(title)
    if not safe:
        return ""
    return safe + record.extension


def validate_plan(records: list[RomRecord]) -> None:
    # 同じフォルダ内の変換先重複を検出し、自動選択から外す。
    seen: dict[tuple[str, str], RomRecord] = {}
    for record in records:
        if not record.new_name or record.protected or record.variant_excluded or record.region_excluded:
            continue
        parent = str(Path(record.path).parent).casefold()
        key = (parent, record.new_name.casefold())
        if key in seen and seen[key].path.casefold() != record.path.casefold():
            other = seen[key]
            record.enabled = False
            other.enabled = False
            record.status = "要確認"
            other.status = "要確認"
            record.note = f"変換先重複: {record.new_name}"
            other.note = f"変換先重複: {other.new_name}"
        else:
            seen[key] = record


def scan_records(
    root: Path,
    dat_index: DatIndex | None,
    recursive: bool,
    large_hash: bool,
    protect_multi_zip: bool,
    main_rom_only: bool,
    japan_only: bool,
    progress: Callable[[str], None],
    exclude_dir: Path | None = None,
    exclude_folder_name: str | None = None,
) -> list[RomRecord]:
    refs = collect_companion_references(
        root, recursive, exclude_dir=exclude_dir, exclude_folder_name=exclude_folder_name
    )
    iterator = root.rglob("*") if recursive else root.iterdir()
    files = [
        p for p in iterator
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_EXTENSIONS
        and not _path_is_within(p, exclude_dir)
        and not _path_is_in_named_folder(p, root, exclude_folder_name)
    ]
    files.sort(key=lambda p: str(p.relative_to(root)).casefold())
    records: list[RomRecord] = []
    limit = DEFAULT_LARGE_HASH_LIMIT_MB * 1024 * 1024

    for n, path in enumerate(files, start=1):
        ext = compound_extension(path)
        ext_lower = ext.lower()
        rel = str(path.relative_to(root))
        record = RomRecord(
            path=str(path.resolve()),
            relative_path=rel,
            old_name=path.name,
            extension=ext,
            system=infer_system_from_extension(path.suffix.lower()),
        )

        if path.suffix.lower() in DESCRIPTOR_EXTENSIONS:
            record.protected = True
            record.status = "保護"
            record.note = "リンク型ディスクセットの参照ファイル。単体リネームすると参照が壊れるため保護。"
        elif str(path.resolve()).casefold() in refs:
            record.protected = True
            record.status = "保護"
            record.note = "CUE/GDI/CCD/M3U から参照される付随ファイルのため保護。"

        entry = None
        method = ""

        if path.suffix.lower() == ".zip":
            member, inner_ext, count = zip_single_rom_info(path)
            if member and inner_ext:
                record.system = infer_system_from_extension(inner_ext)
                if dat_index:
                    try:
                        size, crc, md5, sha1 = zip_member_hashes(path, member)
                        entry, method = dat_index.match(size=size, crc=crc, md5=md5, sha1=sha1)
                    except Exception as exc:
                        record.note = f"ZIP内部ハッシュ失敗: {exc}"
            elif count > 1 and protect_multi_zip:
                record.protected = True
                record.status = "保護"
                record.note = "複数ROM入りZIP。MAME/Arcade等の可能性があるため既定で保護。"
            elif count == -1:
                record.status = "要確認"
                record.note = "ZIPとして読み取れません。"
        elif path.suffix.lower() == ".7z":
            record.status = "要確認"
            record.note = "7zはファイル名ベースのAI候補のみ。内部DATハッシュ照合は未実装。"
        elif dat_index and not record.protected:
            try:
                file_size = path.stat().st_size
                if file_size <= limit or large_hash:
                    size, crc, md5, sha1 = file_hashes(path)
                    entry, method = dat_index.match(size=size, crc=crc, md5=md5, sha1=sha1)
                else:
                    record.note = f"{DEFAULT_LARGE_HASH_LIMIT_MB}MB超のためハッシュ照合を省略。"
            except Exception as exc:
                record.note = f"ハッシュ計算失敗: {exc}"

        if entry:
            record.dat_title = entry.game_name
            record.dat_rom_name = entry.rom_name
            record.dat_source = entry.source
            record.match_method = method
            if entry.system:
                record.system = entry.system
            record.source = f"DAT({method})"
            record.status = "DAT一致" if not record.protected else record.status

        if not record.source and not record.protected:
            record.source = "ファイル名"
            if record.status == "未処理":
                record.status = "AI待ち"

        apply_main_rom_policy(record, main_rom_only)
        apply_region_policy(record, japan_only)
        records.append(record)
        if n == 1 or n % 10 == 0 or n == len(files):
            progress(f"スキャン/DAT照合: {n}/{len(files)} 件")

    return records


def apply_ai_results(
    root: Path,
    records: list[RomRecord],
    api_key: str,
    model: str,
    min_confidence: float,
    progress: Callable[[str], None],
) -> None:
    cache = load_cache(root)
    targets = [r for r in records if not r.protected and not r.variant_excluded and not r.region_excluded]
    uncached: list[RomRecord] = []

    for record in targets:
        cached, cache_mode = lookup_cached_result(cache, record)
        if isinstance(cached, dict):
            title = sanitize_windows_filename(str(cached.get("japanese_title", "")))
            try:
                confidence = float(cached.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            status = str(cached.get("status", "unknown"))
            if title and status == "ok":
                record.japanese_title = title
                record.confidence = confidence
                record.new_name = create_new_name(record, title)
                if cache_mode == "exact":
                    record.source = ("DAT+Gemini(cache)" if record.dat_title else "Gemini(cache)")
                else:
                    record.source = ("DAT+Gemini(cache再利用)" if record.dat_title else "Gemini(cache再利用)")
                if record.new_name.casefold() == record.old_name.casefold():
                    record.status = "リネーム済み"
                    record.enabled = False
                else:
                    record.status = "候補" if confidence >= min_confidence else "要確認"
                    record.enabled = confidence >= min_confidence
            else:
                uncached.append(record)
        else:
            uncached.append(record)

    if not uncached:
        validate_plan(records)
        return

    if not api_key:
        for record in uncached:
            if record.dat_title:
                fallback = sanitize_windows_filename(strip_rom_tags(record.dat_title))
                record.japanese_title = fallback
                record.new_name = create_new_name(record, fallback)
                record.confidence = 0.60
                record.source = "DAT名(未日本語化)"
                record.status = "要確認"
                record.enabled = False
                record.note = (record.note + " / " if record.note else "") + "Gemini APIキー未設定。DAT名を仮候補として表示。"
            else:
                record.status = "要確認"
                record.enabled = False
                record.note = (record.note + " / " if record.note else "") + "Gemini APIキー未設定。"
        validate_plan(records)
        return

    ai_map = query_gemini(api_key, model, uncached, progress)
    for record in uncached:
        item = ai_map.get(record.key())
        if not item:
            record.status = "要確認"
            record.enabled = False
            record.note = (record.note + " / " if record.note else "") + "Gemini結果なし。"
            continue
        status = str(item.get("status", "unknown"))
        title = sanitize_windows_filename(str(item.get("japanese_title", "")))
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        reason = str(item.get("reason", ""))
        cache[cache_key(record)] = {
            "japanese_title": title,
            "confidence": confidence,
            "status": status,
            "reason": reason,
            "model": model,
        }
        if status == "ok" and title:
            record.japanese_title = title
            record.new_name = create_new_name(record, title)
            record.confidence = confidence
            record.source = "DAT+Gemini" if record.dat_title else "Gemini"
            if record.new_name.casefold() == record.old_name.casefold():
                record.status = "リネーム済み"
                record.enabled = False
            else:
                record.status = "候補" if confidence >= min_confidence else "要確認"
                record.enabled = confidence >= min_confidence
            if confidence < min_confidence:
                record.note = (record.note + " / " if record.note else "") + f"確信度不足: {confidence:.2f}"
        else:
            record.confidence = confidence
            record.status = "要確認"
            record.enabled = False
            record.note = (record.note + " / " if record.note else "") + (reason or "日本語タイトルを確定できませんでした。")

    save_cache(root, cache)
    validate_plan(records)


def export_csv(path: Path, root: Path, records: list[RomRecord]) -> None:
    fields = [
        "enabled", "relative_path", "old_name", "new_name", "japanese_title", "dat_title",
        "system", "source", "confidence", "status", "protected", "note", "dat_source", "match_method",
        "variant_type", "main_candidate", "variant_excluded", "variant_reason", "dat_rom_name",
        "region", "japanese_region", "region_excluded", "region_reason",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in records:
            row = {k: getattr(r, k) for k in fields}
            row["enabled"] = "1" if r.enabled else "0"
            row["protected"] = "1" if r.protected else "0"
            row["main_candidate"] = "1" if r.main_candidate else "0"
            row["variant_excluded"] = "1" if r.variant_excluded else "0"
            row["japanese_region"] = "1" if r.japanese_region else "0"
            row["region_excluded"] = "1" if r.region_excluded else "0"
            writer.writerow(row)


def import_csv(path: Path, root: Path, main_rom_only: bool = False, japan_only: bool = True) -> list[RomRecord]:
    records: list[RomRecord] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rel = row.get("relative_path", "")
            full = root / rel
            old_name = row.get("old_name", "") or full.name
            ext = compound_extension(Path(old_name))
            try:
                confidence = float(row.get("confidence", "0") or 0)
            except ValueError:
                confidence = 0.0
            protected = row.get("protected", "0") in {"1", "true", "True", "yes"}
            enabled = row.get("enabled", "0") in {"1", "true", "True", "yes"} and not protected
            record = RomRecord(
                path=str(full.resolve()),
                relative_path=rel,
                old_name=old_name,
                extension=ext,
                system=row.get("system", "Unknown"),
                dat_title=row.get("dat_title", ""),
                dat_source=row.get("dat_source", ""),
                match_method=row.get("match_method", ""),
                dat_rom_name=row.get("dat_rom_name", ""),
                japanese_title=row.get("japanese_title", ""),
                new_name=row.get("new_name", ""),
                confidence=confidence,
                source=row.get("source", "CSV"),
                status=row.get("status", "読込"),
                note=row.get("note", ""),
                protected=protected,
                enabled=enabled,
            )
            apply_main_rom_policy(record, main_rom_only)
            apply_region_policy(record, japan_only)
            if record.variant_excluded or record.region_excluded:
                record.enabled = False
            if not full.exists():
                record.enabled = False
                record.status = "要確認"
                record.note = (record.note + " / " if record.note else "") + "元ファイルが見つかりません。"
            records.append(record)
    validate_plan(records)
    return records


def _casefold_path(path: Path) -> str:
    return str(path.resolve()).casefold()


def _named_sibling_completed_dir(root: Path, source: Path) -> Path:
    """sourceの完了先ディレクトリを返す。

    ルート直下のROMは root/完了。
    サブフォルダ内のROMは、そのサブフォルダの親に「サブフォルダ名＋完了」を作る。

    例:
      ROM/SFC/a.sfc -> ROM/SFC完了/
      ROM/PS1/Japan/a.bin -> ROM/PS1/Japan完了/
      ROM/a.sfc -> ROM/完了/
    """
    root_resolved = root.resolve()
    source_parent = source.parent.resolve()
    try:
        source_parent.relative_to(root_resolved)
    except (ValueError, OSError):
        return source.parent / DEFAULT_COMPLETED_FOLDER_NAME

    if source_parent == root_resolved:
        return root_resolved / DEFAULT_COMPLETED_FOLDER_NAME

    return source_parent.parent / f"{source_parent.name}{DEFAULT_COMPLETED_FOLDER_NAME}"


def completed_target_path(
    root: Path,
    record: RomRecord,
    completed_dir: Path | None,
    per_directory_completed: bool = False,
) -> Path:
    """リネーム後の最終パスを返す。

    per_directory_completed=True の場合、サブフォルダ内のROMは
    親フォルダ側の「サブフォルダ名＋完了」へ移動する。
    例: ROM/SFC/a.sfc -> ROM/SFC完了/日本語.sfc
    """
    old = Path(record.path)
    if completed_dir is None:
        return old.with_name(record.new_name)

    if per_directory_completed:
        return _named_sibling_completed_dir(root, old) / record.new_name

    try:
        rel_parent = old.parent.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        rel_parent = Path()

    return completed_dir / rel_parent / record.new_name


def apply_rename_plan(
    root: Path,
    records: list[RomRecord],
    completed_dir: Path | None = None,
    per_directory_completed: bool = False,
) -> tuple[Path | None, list[str]]:
    chosen = [
        r for r in records
        if r.enabled
        and not r.protected
        and not r.variant_excluded
        and not r.region_excluded
        and r.new_name
        and r.new_name.casefold() != r.old_name.casefold()
    ]
    errors: list[str] = []
    if not chosen:
        return None, ["適用対象がありません。"]

    if completed_dir is not None:
        completed_dir = completed_dir.expanduser().resolve()
        if not per_directory_completed and completed_dir == root.resolve():
            return None, ["完了フォルダにはROMフォルダそのもの以外を指定してください。"]

    source_keys = {_casefold_path(Path(r.path)) for r in chosen}
    target_keys: set[str] = set()
    operations: list[tuple[RomRecord, Path, Path]] = []

    for r in chosen:
        old = Path(r.path)
        if not old.exists():
            errors.append(f"元ファイルなし: {old}")
            continue

        if r.extension and r.new_name.lower().endswith(r.extension.lower()):
            base_name = r.new_name[:-len(r.extension)]
        else:
            base_name = Path(r.new_name).stem

        safe_name = sanitize_windows_filename(base_name) + r.extension
        if safe_name != r.new_name:
            errors.append(f"無効な変換先名または拡張子変更: {r.new_name}")
            continue

        new = completed_target_path(root, r, completed_dir, per_directory_completed)
        nk = _casefold_path(new)
        if nk in target_keys:
            errors.append(f"変換先重複: {new}")
            continue
        target_keys.add(nk)

        if new.exists() and (completed_dir is not None or nk not in source_keys):
            errors.append(f"既存ファイルと衝突: {new}")
            continue

        operations.append((r, old, new))

    if errors:
        return None, errors

    staged: list[tuple[RomRecord, Path, Path, Path]] = []
    try:
        for idx, (record, old, new) in enumerate(operations):
            temp = old.with_name(f".__romrename_{uuid.uuid4().hex}_{idx}{old.suffix}")
            old.rename(temp)
            staged.append((record, old, temp, new))
    except Exception as exc:
        for _record, old, temp, _new in reversed(staged):
            try:
                if temp.exists() and not old.exists():
                    old.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(temp), str(old))
            except Exception:
                pass
        return None, [f"一時リネーム中に失敗。ロールバックしました: {exc}"]

    completed: list[tuple[RomRecord, Path, Path, Path]] = []
    try:
        for record, old, temp, new in staged:
            new.parent.mkdir(parents=True, exist_ok=True)
            if completed_dir is None:
                temp.rename(new)
            else:
                shutil.move(str(temp), str(new))
            completed.append((record, old, temp, new))
    except Exception as exc:
        for record, old, temp, new in reversed(staged):
            try:
                current = new if new.exists() else temp
                if current.exists() and not old.exists():
                    old.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(current), str(old))
            except Exception:
                pass
        return None, [f"最終リネーム/移動中に失敗。可能な範囲で元に戻しました: {exc}"]

    history_dir = root / HISTORY_DIR
    history_dir.mkdir(exist_ok=True)
    history_path = history_dir / f"rename_{datetime.now():%Y%m%d_%H%M%S_%f}.csv"
    with history_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["old_path", "new_path", "timestamp", "moved_to_completed", "completed_dir", "action"],
        )
        writer.writeheader()
        for _record, old, _temp, new in completed:
            writer.writerow({
                "old_path": str(old),
                "new_path": str(new),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "moved_to_completed": "1" if completed_dir is not None else "0",
                "completed_dir": str(new.parent) if completed_dir is not None else "",
                "action": (
                    "rename_move_named_sibling_completed"
                    if completed_dir is not None and per_directory_completed
                    else "rename_move" if completed_dir is not None
                    else "rename_only"
                ),
            })

    return history_path, []


def history_files(root: Path) -> list[Path]:
    folder = root / HISTORY_DIR
    if not folder.exists():
        return []
    return sorted(
        [p for p in folder.glob("rename_*.csv") if not p.name.endswith(".undone.csv")],
        reverse=True,
    )


def latest_history(root: Path) -> Path | None:
    files = history_files(root)
    return files[0] if files else None


def configured_completed_target(
    root: Path,
    source: Path,
    completed_dir: Path,
    per_directory_completed: bool = False,
) -> Path:
    if per_directory_completed:
        return _named_sibling_completed_dir(root, source) / source.name
    try:
        rel_parent = source.parent.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        rel_parent = Path()
    return completed_dir / rel_parent / source.name


def collect_already_renamed_candidates(
    root: Path,
    records: list[RomRecord],
    completed_dir: Path,
    per_directory_completed: bool = False,
    japan_only: bool = True,
) -> list[tuple[Path, Path, str]]:
    """過去の成功履歴と現在の確認済み一覧から、移動だけ行えるROMを集める。"""
    root = root.resolve()
    completed_dir = completed_dir.expanduser().resolve()
    candidates: dict[str, tuple[Path, Path, str]] = {}
    current_record_map = {_casefold_path(Path(r.path)): r for r in records}

    # 1) 以前このアプリで正常にリネーム済みだが、完了フォルダへは移していないファイル。
    for history in history_files(root):
        try:
            with history.open("r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            continue
        for row in rows:
            if str(row.get("moved_to_completed", "0")).strip().lower() in {"1", "true", "yes"}:
                continue
            current_text = str(row.get("new_path", "") or "").strip()
            if not current_text:
                continue
            current = Path(current_text)
            if not current.exists() or not current.is_file():
                continue
            try:
                current_resolved = current.resolve()
                current_resolved.relative_to(root)
            except (ValueError, OSError):
                continue
            if (
                _path_is_in_named_folder(current_resolved, root, DEFAULT_COMPLETED_FOLDER_NAME)
                if per_directory_completed
                else _path_is_within(current_resolved, completed_dir)
            ):
                continue

            if japan_only:
                known_record = current_record_map.get(_casefold_path(current_resolved))
                if known_record is not None:
                    if known_record.region_excluded or not known_record.japanese_region:
                        continue
                else:
                    # 旧履歴だけから移動する場合は、リネーム前の元ファイル名に
                    # 日本地域タグが明示されているものだけを安全側で採用する。
                    original_name = Path(str(row.get("old_path", "") or "")).name
                    history_probe = RomRecord(
                        path=str(current_resolved),
                        relative_path=current_resolved.name,
                        old_name=original_name or current_resolved.name,
                        extension=compound_extension(current_resolved),
                    )
                    region, is_japan, _reason = classify_rom_region(history_probe)
                    if not is_japan:
                        continue

            target = configured_completed_target(
                root, current_resolved, completed_dir, per_directory_completed
            )
            candidates[_casefold_path(current_resolved)] = (current_resolved, target, "リネーム履歴")

    # 2) 現在の候補作成で「すでに正しい日本語名」と確認できたファイル。
    for record in records:
        if record.protected or record.variant_excluded or record.region_excluded:
            continue
        if not record.japanese_title or not record.new_name:
            continue
        if record.status in {"要確認", "保護", "派生ROM除外"}:
            continue
        if record.new_name.casefold() != record.old_name.casefold():
            continue
        current = Path(record.path)
        if not current.exists() or not current.is_file():
            continue
        try:
            current_resolved = current.resolve()
            current_resolved.relative_to(root)
        except (ValueError, OSError):
            continue
        if (
            _path_is_in_named_folder(current_resolved, root, DEFAULT_COMPLETED_FOLDER_NAME)
            if per_directory_completed
            else _path_is_within(current_resolved, completed_dir)
        ):
            continue
        target = configured_completed_target(
            root, current_resolved, completed_dir, per_directory_completed
        )
        candidates.setdefault(
            _casefold_path(current_resolved),
            (current_resolved, target, "確認済みリネーム済み"),
        )

    return sorted(candidates.values(), key=lambda item: str(item[0]).casefold())


def move_already_renamed_to_completed(
    root: Path,
    candidates: list[tuple[Path, Path, str]],
    completed_dir: Path,
    per_directory_completed: bool = False,
) -> tuple[Path | None, list[str]]:
    errors: list[str] = []
    if not candidates:
        return None, ["移動対象がありません。"]

    root = root.resolve()
    completed_dir = completed_dir.expanduser().resolve()
    if not per_directory_completed and completed_dir == root:
        return None, ["完了フォルダにはROMフォルダそのもの以外を指定してください。"]

    target_keys: set[str] = set()
    checked: list[tuple[Path, Path, str]] = []
    for source, target, reason in candidates:
        if not source.exists():
            errors.append(f"元ファイルなし: {source}")
            continue
        if (
            _path_is_in_named_folder(source, root, DEFAULT_COMPLETED_FOLDER_NAME)
            if per_directory_completed
            else _path_is_within(source, completed_dir)
        ):
            continue
        key = _casefold_path(target)
        if key in target_keys:
            errors.append(f"移動先重複: {target}")
            continue
        target_keys.add(key)
        if target.exists():
            errors.append(f"移動先に同名ファイルがあります: {target}")
            continue
        checked.append((source, target, reason))

    if errors:
        return None, errors
    if not checked:
        return None, ["移動対象がありません。"]

    staged: list[tuple[Path, Path, Path, str]] = []
    try:
        for idx, (source, target, reason) in enumerate(checked):
            temp = source.with_name(f".__rommove_{uuid.uuid4().hex}_{idx}{source.suffix}")
            shutil.move(str(source), str(temp))
            staged.append((source, temp, target, reason))
    except Exception as exc:
        for source, temp, _target, _reason in reversed(staged):
            try:
                if temp.exists() and not source.exists():
                    shutil.move(str(temp), str(source))
            except Exception:
                pass
        return None, [f"移動準備中に失敗。ロールバックしました: {exc}"]

    moved: list[tuple[Path, Path, Path, str]] = []
    try:
        for source, temp, target, reason in staged:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(temp), str(target))
            moved.append((source, temp, target, reason))
    except Exception as exc:
        for source, temp, target, _reason in reversed(staged):
            try:
                current = target if target.exists() else temp
                if current.exists() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(current), str(source))
            except Exception:
                pass
        return None, [f"完了フォルダへの移動中に失敗。可能な範囲で元に戻しました: {exc}"]

    history_dir = root / HISTORY_DIR
    history_dir.mkdir(exist_ok=True)
    history_path = history_dir / f"rename_{datetime.now():%Y%m%d_%H%M%S_%f}.csv"
    with history_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["old_path", "new_path", "timestamp", "moved_to_completed", "completed_dir", "action"],
        )
        writer.writeheader()
        for source, _temp, target, reason in moved:
            writer.writerow({
                "old_path": str(source),
                "new_path": str(target),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "moved_to_completed": "1",
                "completed_dir": str(target.parent),
                "action": (
                    f"move_only_named_sibling_completed:{reason}"
                    if per_directory_completed
                    else f"move_only:{reason}"
                ),
            })
    return history_path, []

def undo_history(history_path: Path) -> list[str]:
    errors: list[str] = []
    with history_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    operations: list[tuple[Path, Path]] = []  # current(new) -> original(old)
    current_keys = {_casefold_path(Path(row["new_path"])) for row in rows}
    target_keys: set[str] = set()

    for row in rows:
        old = Path(row["old_path"])
        new = Path(row["new_path"])
        if not new.exists():
            errors.append(f"現在のファイルなし: {new}")
            continue
        ok = _casefold_path(old)
        if ok in target_keys:
            errors.append(f"復元先重複: {old}")
            continue
        target_keys.add(ok)
        # 復元先が存在していても、それが同じUndo対象の現在名なら二段階で入替可能。
        if old.exists() and ok not in current_keys:
            errors.append(f"元の名前が既に存在: {old}")
            continue
        operations.append((new, old))

    if errors:
        return errors

    staged: list[tuple[Path, Path, Path]] = []
    try:
        for idx, (new, old) in enumerate(operations):
            temp = new.with_name(f".__romundo_{uuid.uuid4().hex}_{idx}{new.suffix}")
            shutil.move(str(new), str(temp))
            staged.append((new, temp, old))
    except Exception as exc:
        for new, temp, _old in reversed(staged):
            try:
                if temp.exists() and not new.exists():
                    temp.rename(new)
            except Exception:
                pass
        return [f"Undo一時退避中に失敗。ロールバックしました: {exc}"]

    try:
        for _new, temp, old in staged:
            old.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(temp), str(old))
    except Exception as exc:
        # 最善努力でUndo開始前の状態へ戻す。
        for new, temp, old in reversed(staged):
            try:
                current = old if old.exists() else temp
                if current.exists() and not new.exists():
                    new.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(current), str(new))
            except Exception:
                pass
        return [f"Undo復元中に失敗。可能な範囲で元の状態へ戻しました: {exc}"]

    try:
        history_path.rename(history_path.with_suffix(".undone.csv"))
    except Exception:
        pass
    return []


def load_settings() -> dict:
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def save_settings(data: dict) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")



class DatManagerDialog(tk.Toplevel):
    def __init__(self, parent, settings: dict, on_change: Callable[[], None]) -> None:
        super().__init__(parent)
        self.title("DATマネージャー - Libretro Database")
        self.geometry("1120x700")
        self.minsize(820, 520)
        self.transient(parent)
        self.settings = settings
        self.on_change = on_change
        self.catalog: list[dict] = []
        self.worker_queue: queue.Queue = queue.Queue()
        self.busy = False

        selected_sources = set(settings.get("db_sources", ["no-intro", "redump"]))
        self.source_vars = {
            key: tk.BooleanVar(value=key in selected_sources)
            for key in LIBRETRO_SOURCES
        }
        self.filter_var = tk.StringVar()
        self.status_var = tk.StringVar(value="GitHubからDAT一覧を取得してください。")
        self._build_ui()
        self.filter_var.trace_add("write", lambda *_: self._refresh_tree())
        self.after(150, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(200, self._load_catalog)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        source_frame = ttk.LabelFrame(outer, text="取得元", padding=8)
        source_frame.pack(fill="x")
        for key, label in LIBRETRO_SOURCES.items():
            ttk.Checkbutton(source_frame, text=label, variable=self.source_vars[key]).pack(side="left", padx=(0, 14))
        ttk.Button(source_frame, text="一覧を再取得", command=self._load_catalog).pack(side="left", padx=6)
        ttk.Label(source_frame, text="検索").pack(side="left", padx=(18, 4))
        ttk.Entry(source_frame, textvariable=self.filter_var, width=32).pack(side="left", fill="x", expand=True)

        info = ttk.Label(
            outer,
            text=(
                "Libretro Database の公開GitHubからDATを取得します。"
                " No-Introは主にカートリッジ系、Redumpは主にディスク系です。"
                " 必要な機種だけ選ぶと解析が速くなります。"
            ),
            wraplength=1060,
            justify="left",
        )
        info.pack(fill="x", pady=(8, 6))

        tree_frame = ttk.Frame(outer)
        tree_frame.pack(fill="both", expand=True)
        columns = ("source", "name", "size", "local", "state")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="extended")
        headings = {
            "source": "ソース", "name": "DAT / 機種", "size": "サイズ", "local": "ローカル", "state": "状態"
        }
        widths = {"source": 90, "name": 600, "size": 100, "local": 100, "state": 150}
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], stretch=col == "name")
        ybar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="表示中を全選択", command=self._select_visible).pack(side="left")
        ttk.Button(buttons, text="選択解除", command=lambda: self.tree.selection_remove(self.tree.selection())).pack(side="left", padx=4)
        ttk.Button(buttons, text="選択DATを取得 / 更新", command=self._install_selected).pack(side="left", padx=(14, 4))
        ttk.Button(buttons, text="インストール済みを更新", command=self._update_installed).pack(side="left", padx=4)
        ttk.Button(buttons, text="選択したローカルDATを削除", command=self._delete_selected).pack(side="left", padx=4)
        ttk.Button(buttons, text="閉じる", command=self._close).pack(side="right")

        status = ttk.Frame(outer)
        status.pack(fill="x", pady=(8, 0))
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=180)
        self.progress.pack(side="left", padx=(0, 8))
        ttk.Label(status, textvariable=self.status_var).pack(side="left", fill="x", expand=True)

    def _source_selection(self) -> list[str]:
        return [key for key, var in self.source_vars.items() if var.get()]

    def _set_busy(self, busy: bool, message: str | None = None) -> None:
        self.busy = busy
        if busy:
            self.progress.start(10)
        else:
            self.progress.stop()
        if message:
            self.status_var.set(message)

    def _load_catalog(self) -> None:
        if self.busy:
            return
        sources = self._source_selection()
        if not sources:
            messagebox.showwarning(APP_TITLE, "取得元を1つ以上選択してください。", parent=self)
            return
        self.settings["db_sources"] = sources
        self._set_busy(True, "GitHubからDAT一覧を取得中...")

        def worker() -> None:
            try:
                catalog = fetch_libretro_catalog(sources, lambda m: self.worker_queue.put(("status", m)))
                self.worker_queue.put(("catalog", catalog))
            except Exception as exc:
                self.worker_queue.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _format_size(size: int) -> str:
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} B"

    def _visible_catalog(self) -> list[dict]:
        needle = self.filter_var.get().strip().casefold()
        if not needle:
            return self.catalog
        return [
            item for item in self.catalog
            if needle in item["name"].casefold() or needle in item["source_label"].casefold()
        ]

    def _refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        manifest = load_db_manifest()
        manifest_files = manifest.get("files", {}) if isinstance(manifest.get("files"), dict) else {}
        for item in self._visible_catalog():
            dest = managed_dat_destination(item)
            rel = str(dest.relative_to(DB_ROOT)).replace("\\", "/")
            local = dest.exists()
            known = manifest_files.get(rel, {}) if isinstance(manifest_files.get(rel), dict) else {}
            if not local:
                state = "未取得"
            elif item.get("sha") and known.get("remote_sha") == item.get("sha"):
                state = "最新"
            else:
                state = "更新候補"
            iid = str(len(self.tree.get_children()))
            self.tree.insert(
                "", "end", iid=iid,
                values=(item["source_label"], item["name"], self._format_size(item["size"]), "あり" if local else "なし", state),
            )
            self.tree.set(iid, "name", item["name"])
            # item itself is kept by visible index mapping through iid
        installed = len(discover_managed_dat_files())
        self.status_var.set(f"一覧 {len(self._visible_catalog()):,}件 / ローカルDAT {installed:,}件")

    def _selected_items(self) -> list[dict]:
        visible = self._visible_catalog()
        result = []
        for iid in self.tree.selection():
            try:
                idx = int(iid)
            except ValueError:
                continue
            if 0 <= idx < len(visible):
                result.append(visible[idx])
        return result

    def _select_visible(self) -> None:
        self.tree.selection_set(self.tree.get_children())

    def _install_items(self, items: list[dict]) -> None:
        if self.busy:
            return
        if not items:
            messagebox.showinfo(APP_TITLE, "対象DATを選択してください。", parent=self)
            return
        self._set_busy(True, f"{len(items)}件のDATを確認・取得中...")

        def worker() -> None:
            try:
                result = install_libretro_items(items, lambda m: self.worker_queue.put(("status", m)))
                self.worker_queue.put(("installed", result))
            except Exception as exc:
                self.worker_queue.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _install_selected(self) -> None:
        self._install_items(self._selected_items())

    def _update_installed(self) -> None:
        items = [item for item in self.catalog if managed_dat_destination(item).exists()]
        self._install_items(items)

    def _delete_selected(self) -> None:
        items = [item for item in self._selected_items() if managed_dat_destination(item).exists()]
        if not items:
            messagebox.showinfo(APP_TITLE, "削除するローカルDATを選択してください。", parent=self)
            return
        if not messagebox.askyesno(APP_TITLE, f"選択した {len(items)} 件のローカルDATを削除しますか？\n\nGitHub上のデータは削除されません。", parent=self):
            return
        for item in items:
            remove_managed_dat(managed_dat_destination(item))
        self.on_change()
        self._refresh_tree()

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.worker_queue.get_nowait()
                if kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "catalog":
                    self.catalog = payload
                    self._set_busy(False)
                    self._refresh_tree()
                elif kind == "installed":
                    updated, skipped = payload
                    self._set_busy(False)
                    self.on_change()
                    self._refresh_tree()
                    messagebox.showinfo(
                        APP_TITLE,
                        f"DAT更新完了\n\n取得・更新: {updated}件\nすでに最新: {skipped}件",
                        parent=self,
                    )
                elif kind == "error":
                    self._set_busy(False)
                    self.status_var.set(f"エラー: {payload}")
                    messagebox.showerror(APP_TITLE, str(payload), parent=self)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self._drain_queue)

    def _close(self) -> None:
        self.settings["db_sources"] = self._source_selection()
        self.destroy()

class RomRenamerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_TITLE} {APP_VERSION}")
        self.geometry("1600x850")
        self.minsize(1080, 650)

        self.settings = load_settings()
        self.records: list[RomRecord] = []
        self.dat_paths: list[Path] = [Path(p) for p in self.settings.get("dat_paths", []) if Path(p).exists()]
        self.dat_index: DatIndex | None = None
        self.worker_queue: queue.Queue = queue.Queue()
        self.busy = False

        self.rom_dir_var = tk.StringVar(value=self.settings.get("rom_dir", ""))
        initial_api_key, credential_status = self._load_initial_api_key()
        self.api_key_var = tk.StringVar(value=initial_api_key)
        self.credential_status_var = tk.StringVar(value=credential_status)
        self.model_var = tk.StringVar(value=self.settings.get("model", DEFAULT_MODEL))
        self.recursive_var = tk.BooleanVar(value=bool(self.settings.get("recursive", False)))
        self.large_hash_var = tk.BooleanVar(value=bool(self.settings.get("large_hash", False)))
        self.protect_multi_zip_var = tk.BooleanVar(value=bool(self.settings.get("protect_multi_zip", True)))
        self.main_rom_only_var = tk.BooleanVar(value=bool(self.settings.get("main_rom_only", True)))
        self.japan_only_var = tk.BooleanVar(value=bool(self.settings.get("japan_only", True)))
        self.move_completed_var = tk.BooleanVar(value=bool(self.settings.get("move_completed", False)))
        self.per_directory_completed_var = tk.BooleanVar(
            value=bool(self.settings.get("per_directory_completed", True))
        )
        self.completed_dir_var = tk.StringVar(value=str(self.settings.get("completed_dir", "")))
        self.min_conf_var = tk.DoubleVar(value=float(self.settings.get("min_confidence", DEFAULT_MIN_CONFIDENCE)))
        self.status_var = tk.StringVar(value="準備完了")
        self.dat_label_var = tk.StringVar()

        self._build_ui()
        self._update_dat_label()
        self.after(150, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_initial_api_key(self) -> tuple[str, str]:
        env_key = os.getenv("GEMINI_API_KEY", "").strip()
        if env_key:
            return env_key, "環境変数 GEMINI_API_KEY から読込"

        if os.name != "nt":
            return "", "Windows以外では資格情報マネージャーを使用できません"

        try:
            saved = load_api_key_from_windows_credential().strip()
            if saved:
                return saved, "Windows資格情報マネージャーから自動読込済み"
            return "", "保存済みAPIキーなし"
        except Exception as exc:
            return "", f"資格情報の読込失敗: {exc}"

    def _save_api_key_credential(self) -> None:
        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning(APP_TITLE, "Gemini APIキーを入力してから保存してください。")
            return

        try:
            save_api_key_to_windows_credential(api_key)
        except Exception as exc:
            self.credential_status_var.set(f"保存失敗: {exc}")
            messagebox.showerror(APP_TITLE, f"Windows資格情報マネージャーへの保存に失敗しました。\n\n{exc}")
            return

        self.credential_status_var.set("Windows資格情報マネージャーへ保存済み")
        self._log(f"Gemini APIキーをWindows資格情報マネージャーへ保存しました: {CREDENTIAL_TARGET}")
        messagebox.showinfo(
            APP_TITLE,
            "Gemini APIキーをWindows資格情報マネージャーへ保存しました。\n"
            "次回起動時に自動で読み込みます。\n\n"
            "キー本体はsettings.jsonへ保存しません。",
        )

    def _delete_api_key_credential(self) -> None:
        if not messagebox.askyesno(
            APP_TITLE,
            "Windows資格情報マネージャーに保存したGemini APIキーを削除しますか？\n\n"
            "現在画面に入力されているキーは、このセッション中はそのまま残します。",
        ):
            return

        try:
            deleted = delete_api_key_from_windows_credential()
        except Exception as exc:
            self.credential_status_var.set(f"削除失敗: {exc}")
            messagebox.showerror(APP_TITLE, f"保存済みAPIキーの削除に失敗しました。\n\n{exc}")
            return

        if deleted:
            self.credential_status_var.set("保存済みAPIキーを削除しました")
            self._log(f"Windows資格情報マネージャーからGemini APIキーを削除しました: {CREDENTIAL_TARGET}")
            messagebox.showinfo(APP_TITLE, "Windows資格情報マネージャーから保存済みAPIキーを削除しました。")
        else:
            self.credential_status_var.set("保存済みAPIキーなし")
            messagebox.showinfo(APP_TITLE, "Windows資格情報マネージャーに保存済みAPIキーはありませんでした。")

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=8)
        outer.pack(fill="both", expand=True)

        settings = ttk.LabelFrame(outer, text="設定", padding=8)
        settings.pack(fill="x")
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="ROMフォルダ").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(settings, textvariable=self.rom_dir_var).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Button(settings, text="参照...", command=self._browse_rom_dir).grid(row=0, column=2, padx=6, pady=3)

        ttk.Label(settings, text="DATデータベース").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Label(settings, textvariable=self.dat_label_var, anchor="w").grid(row=1, column=1, sticky="ew", pady=3)
        dat_buttons = ttk.Frame(settings)
        dat_buttons.grid(row=1, column=2, columnspan=2, sticky="w", padx=(6, 0), pady=3)
        ttk.Button(dat_buttons, text="DATマネージャー...", command=self._open_dat_manager).pack(side="left")
        ttk.Button(dat_buttons, text="手動DAT追加...", command=self._browse_dat).pack(side="left", padx=(5, 0))
        ttk.Button(dat_buttons, text="手動クリア", command=self._clear_dat).pack(side="left", padx=(5, 0))

        ttk.Label(settings, text="Gemini APIキー").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(settings, textvariable=self.api_key_var, show="●").grid(row=2, column=1, sticky="ew", pady=3)
        key_buttons = ttk.Frame(settings)
        key_buttons.grid(row=2, column=2, columnspan=2, sticky="w", padx=(6, 0), pady=3)
        ttk.Button(key_buttons, text="資格情報に保存", command=self._save_api_key_credential).pack(side="left")
        ttk.Button(key_buttons, text="保存キー削除", command=self._delete_api_key_credential).pack(side="left", padx=(5, 0))

        ttk.Label(settings, text="APIキー状態").grid(row=3, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Label(settings, textvariable=self.credential_status_var, anchor="w").grid(row=3, column=1, sticky="w", pady=3)
        ttk.Label(settings, text="モデル").grid(row=3, column=2, sticky="e", padx=(6, 4))
        ttk.Combobox(settings, textvariable=self.model_var, values=MODEL_CHOICES, width=24).grid(row=3, column=3, sticky="w")

        option_bar = ttk.Frame(settings)
        option_bar.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Checkbutton(option_bar, text="サブフォルダも対象", variable=self.recursive_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(option_bar, text=f"{DEFAULT_LARGE_HASH_LIMIT_MB}MB超もハッシュ照合", variable=self.large_hash_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(option_bar, text="複数ROM入りZIPを保護(MAME対策)", variable=self.protect_multi_zip_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(
            option_bar,
            text="メインROMのみ（派生/不良ダンプを除外）",
            variable=self.main_rom_only_var,
        ).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(
            option_bar,
            text="日本版ROMのみ日本語化（海外/地域不明を除外）",
            variable=self.japan_only_var,
        ).pack(side="left", padx=(0, 12))
        ttk.Label(option_bar, text="自動選択する最低確信度").pack(side="left")
        ttk.Spinbox(option_bar, from_=0.50, to=1.00, increment=0.01, textvariable=self.min_conf_var, width=6).pack(side="left", padx=4)

        completed_bar = ttk.Frame(settings)
        completed_bar.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Checkbutton(
            completed_bar,
            text="リネーム成功後、完了フォルダへ移動",
            variable=self.move_completed_var,
        ).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(
            completed_bar,
            text="サブフォルダ名＋『完了』を親フォルダに作成",
            variable=self.per_directory_completed_var,
        ).pack(side="left", padx=(0, 10))
        ttk.Label(completed_bar, text="一括完了フォルダ").pack(side="left")
        ttk.Entry(completed_bar, textvariable=self.completed_dir_var, width=38).pack(
            side="left", fill="x", expand=True, padx=5
        )
        ttk.Button(completed_bar, text="参照...", command=self._browse_completed_dir).pack(side="left")
        ttk.Label(
            completed_bar,
            text="（サブフォルダ別作成OFF時のみ使用）",
        ).pack(side="left", padx=(6, 0))

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=8)
        self.create_btn = ttk.Button(buttons, text="候補を自動作成 (DAT → Gemini)", command=self._start_create_candidates)
        self.create_btn.pack(side="left")
        ttk.Button(buttons, text="CSVリスト保存", command=self._save_csv).pack(side="left", padx=5)
        ttk.Button(buttons, text="CSVリスト読込", command=self._load_csv).pack(side="left", padx=5)
        ttk.Separator(buttons, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(buttons, text="全候補をON", command=lambda: self._set_all(True)).pack(side="left", padx=3)
        ttk.Button(buttons, text="全てOFF", command=lambda: self._set_all(False)).pack(side="left", padx=3)
        ttk.Button(buttons, text="選択行を編集", command=self._edit_selected).pack(side="left", padx=3)
        ttk.Separator(buttons, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(buttons, text="チェックした名前を適用", command=self._apply_plan).pack(side="left", padx=3)
        self.move_existing_btn = ttk.Button(
            buttons,
            text="リネーム済みを完了へ移動",
            command=self._move_already_renamed,
        )
        self.move_existing_btn.pack(side="left", padx=3)
        ttk.Button(buttons, text="直前の変更を戻す", command=self._undo_last).pack(side="left", padx=3)

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        list_frame = ttk.Frame(notebook)
        log_frame = ttk.Frame(notebook)
        notebook.add(list_frame, text="リネーム一覧")
        notebook.add(log_frame, text="ログ")

        columns = ("use", "system", "region", "variant", "old", "dat", "jp", "new", "confidence", "source", "status", "note")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="extended")
        headings = {
            "use": "適用", "system": "機種/システム", "region": "地域", "variant": "ROM種別", "old": "現在のファイル名", "dat": "DAT一致タイトル",
            "jp": "日本語公式タイトル", "new": "変更後ファイル名", "confidence": "確信度",
            "source": "根拠", "status": "状態", "note": "メモ",
        }
        widths = {"use": 48, "system": 180, "region": 80, "variant": 125, "old": 250, "dat": 250, "jp": 220, "new": 250, "confidence": 70, "source": 130, "status": 110, "note": 300}
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], minwidth=40, stretch=col not in {"use", "confidence", "status"})

        ybar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(list_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        self.tree.bind("<Button-1>", self._tree_click)
        self.tree.bind("<Double-1>", lambda _e: self._edit_selected())

        self.log_text = tk.Text(log_frame, wrap="word", height=10)
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        status_bar = ttk.Frame(outer)
        status_bar.pack(fill="x", pady=(6, 0))
        self.progress = ttk.Progressbar(status_bar, mode="indeterminate", length=180)
        self.progress.pack(side="left", padx=(0, 8))
        ttk.Label(status_bar, textvariable=self.status_var).pack(side="left")

    def _browse_rom_dir(self) -> None:
        initial = self.rom_dir_var.get() or str(Path.home())
        selected = filedialog.askdirectory(title="ROMフォルダを選択", initialdir=initial)
        if selected:
            self.rom_dir_var.set(selected)

    def _browse_completed_dir(self) -> None:
        root_text = self.rom_dir_var.get().strip()
        initial = self.completed_dir_var.get().strip()
        if not initial:
            initial = str(Path(root_text).expanduser() / "完了") if root_text else str(Path.home())
        selected = filedialog.askdirectory(title="完了フォルダを選択", initialdir=initial)
        if selected:
            self.completed_dir_var.set(selected)

    def _configured_completed_dir(self, root: Path) -> Path:
        text = self.completed_dir_var.get().strip()
        folder = Path(text).expanduser() if text else root / "完了"
        try:
            folder = folder.resolve()
        except OSError:
            folder = folder.absolute()
        return folder

    def _effective_completed_dir(self, root: Path) -> Path | None:
        if not self.move_completed_var.get():
            return None
        return self._configured_completed_dir(root)

    def _browse_dat(self) -> None:
        selected = filedialog.askopenfilenames(
            title="No-Intro / Redump DATを選択",
            filetypes=[("DAT/XML", "*.dat *.xml *.zip"), ("DAT", "*.dat"), ("XML", "*.xml"), ("ZIP", "*.zip"), ("すべて", "*.*")],
        )
        for item in selected:
            p = Path(item)
            if p not in self.dat_paths:
                self.dat_paths.append(p)
        self.dat_index = None
        self._update_dat_label()

    def _clear_dat(self) -> None:
        self.dat_paths.clear()
        self.dat_index = None
        self._update_dat_label()

    def _open_dat_manager(self) -> None:
        DatManagerDialog(self, self.settings, self._managed_dat_changed)

    def _managed_dat_changed(self) -> None:
        self.dat_index = None
        self._update_dat_label()

    def _effective_dat_paths(self) -> list[Path]:
        result: list[Path] = []
        seen: set[str] = set()
        for p in [*discover_managed_dat_files(), *self.dat_paths]:
            try:
                resolved = str(p.resolve()).casefold()
            except Exception:
                resolved = str(p).casefold()
            if resolved in seen or not p.exists():
                continue
            seen.add(resolved)
            result.append(p)
        return result

    def _update_dat_label(self) -> None:
        managed = discover_managed_dat_files()
        manual = [p for p in self.dat_paths if p.exists()]
        manifest = load_db_manifest()
        checked = str(manifest.get("last_checked_at", "")).replace("T", " ")
        suffix = f" / 最終確認 {checked}" if checked else ""
        if not managed and not manual:
            self.dat_label_var.set("未導入 — 『DATマネージャー...』からNo-Intro / Redump等を取得できます")
        else:
            self.dat_label_var.set(f"自動DB {len(managed)}件 / 手動DAT {len(manual)}件{suffix}")

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{stamp}] {message}\n")
        self.log_text.see("end")
        self.status_var.set(message)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.create_btn.configure(state="disabled" if busy else "normal")
        if hasattr(self, "move_existing_btn"):
            self.move_existing_btn.configure(state="disabled" if busy else "normal")
        if busy:
            self.progress.start(10)
        else:
            self.progress.stop()

    def _queue_log(self, msg: str) -> None:
        self.worker_queue.put(("log", msg))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.worker_queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "done":
                    self.records = payload
                    self._refresh_tree()
                    self._set_busy(False)
                    enabled = sum(1 for r in self.records if r.enabled)
                    protected = sum(1 for r in self.records if r.protected)
                    variant_excluded = sum(1 for r in self.records if r.variant_excluded)
                    region_excluded = sum(1 for r in self.records if r.region_excluded)
                    self._log(
                        f"候補作成完了: {len(self.records)}件 / 自動選択 {enabled}件 / "
                        f"派生ROM除外 {variant_excluded}件 / 海外・地域不明除外 {region_excluded}件 / 保護 {protected}件"
                    )
                elif kind == "error":
                    self._set_busy(False)
                    self._log(f"エラー: {payload}")
                    messagebox.showerror(APP_TITLE, str(payload))
        except queue.Empty:
            pass
        self.after(150, self._drain_queue)

    def _root_path(self) -> Path | None:
        text = self.rom_dir_var.get().strip()
        if not text:
            messagebox.showwarning(APP_TITLE, "ROMフォルダを選択してください。")
            return None
        root = Path(text).expanduser()
        if not root.is_dir():
            messagebox.showerror(APP_TITLE, f"ROMフォルダが見つかりません:\n{root}")
            return None
        return root.resolve()

    def _start_create_candidates(self) -> None:
        if self.busy:
            return
        root = self._root_path()
        if not root:
            return
        self._set_busy(True)
        self._log("候補作成を開始します。実ファイル名はまだ変更しません。")
        api_key = self.api_key_var.get().strip()
        model = self.model_var.get().strip() or DEFAULT_MODEL
        recursive = self.recursive_var.get()
        large_hash = self.large_hash_var.get()
        protect_multi = self.protect_multi_zip_var.get()
        main_rom_only = self.main_rom_only_var.get()
        japan_only = self.japan_only_var.get()
        per_directory_completed = self.per_directory_completed_var.get()
        completed_exclude = self._effective_completed_dir(root)
        completed_folder_exclude = (
            DEFAULT_COMPLETED_FOLDER_NAME
            if recursive and per_directory_completed
            else None
        )
        try:
            min_conf = float(self.min_conf_var.get())
        except Exception:
            min_conf = DEFAULT_MIN_CONFIDENCE

        def worker() -> None:
            try:
                dat_index = None
                effective_dat_paths = self._effective_dat_paths()
                if effective_dat_paths:
                    dat_index = DatIndex()
                    for p in effective_dat_paths:
                        try:
                            rel = p.relative_to(DB_ROOT)
                            label = f"自動DB/{rel}"
                        except ValueError:
                            label = p.name
                        self._queue_log(f"DAT読込: {label}")
                        count = parse_dat_file(p, dat_index)
                        self._queue_log(f"  {count:,} ROMエントリ追加")
                    self.dat_index = dat_index
                    self._queue_log(f"DATインデックス完成: {dat_index.entry_count:,}エントリ / {len(effective_dat_paths)}ファイル")
                else:
                    self._queue_log("DAT未導入: ファイル名からGemini推定を行います。DATマネージャーから導入すると精度が上がります。")

                records = scan_records(
                    root=root,
                    dat_index=dat_index,
                    recursive=recursive,
                    large_hash=large_hash,
                    protect_multi_zip=protect_multi,
                    main_rom_only=main_rom_only,
                    japan_only=japan_only,
                    progress=self._queue_log,
                    exclude_dir=completed_exclude,
                    exclude_folder_name=completed_folder_exclude,
                )
                if not records:
                    raise RuntimeError("対応するROMファイルが見つかりませんでした。")
                apply_ai_results(root, records, api_key, model, min_conf, self._queue_log)
                self.worker_queue.put(("done", records))
            except Exception as exc:
                self.worker_queue.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for idx, r in enumerate(self.records):
            use = "✓" if r.enabled else ""
            conf = f"{r.confidence:.2f}" if r.confidence else ""
            self.tree.insert("", "end", iid=str(idx), values=(
                use, r.system, r.region, r.variant_type, r.relative_path, r.dat_title, r.japanese_title, r.new_name,
                conf, r.source, r.status, r.note,
            ))

    def _tree_click(self, event) -> None:
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        column = self.tree.identify_column(event.x)
        row = self.tree.identify_row(event.y)
        if column == "#1" and row:
            idx = int(row)
            record = self.records[idx]
            if record.protected:
                messagebox.showinfo(APP_TITLE, f"このファイルは保護対象です。\n\n{record.note}")
                return "break"
            if record.variant_excluded:
                messagebox.showinfo(
                    APP_TITLE,
                    f"『メインROMのみ』設定で除外されています。\n\n種別: {record.variant_type}\n理由: {record.variant_reason}\n\n"
                    "このROMも処理する場合は『メインROMのみ』をOFFにして候補を再作成してください。",
                )
                return "break"
            if record.region_excluded:
                messagebox.showinfo(
                    APP_TITLE,
                    f"『日本版ROMのみ』設定で除外されています。\n\n地域: {record.region}\n理由: {record.region_reason}\n\n"
                    "海外版も処理する場合は『日本版ROMのみ日本語化』をOFFにして候補を再作成してください。",
                )
                return "break"
            record.enabled = not record.enabled
            self.tree.set(row, "use", "✓" if record.enabled else "")
            return "break"
        return None

    def _set_all(self, enabled: bool) -> None:
        for r in self.records:
            if not r.protected and not r.variant_excluded and not r.region_excluded and r.new_name and r.status not in {"要確認", "保護", "派生ROM除外", "海外ROM除外", "地域不明除外"}:
                r.enabled = enabled
        self._refresh_tree()

    def _edit_selected(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo(APP_TITLE, "編集する行を選択してください。")
            return
        idx = int(selected[0])
        record = self.records[idx]
        if record.protected:
            messagebox.showwarning(APP_TITLE, f"このファイルは参照関係保護のため編集適用できません。\n\n{record.note}")
            return
        if record.variant_excluded:
            messagebox.showwarning(
                APP_TITLE,
                f"『メインROMのみ』設定で除外されています。\n\n種別: {record.variant_type}\n理由: {record.variant_reason}\n\n"
                "編集する場合は『メインROMのみ』をOFFにして候補を再作成してください。",
            )
            return
        if record.region_excluded:
            messagebox.showwarning(
                APP_TITLE,
                f"『日本版ROMのみ』設定で除外されています。\n\n地域: {record.region}\n理由: {record.region_reason}\n\n"
                "編集する場合は『日本版ROMのみ日本語化』をOFFにして候補を再作成してください。",
            )
            return
        initial = record.new_name or (record.japanese_title + record.extension if record.japanese_title else record.old_name)
        new_name = simpledialog.askstring("変更後ファイル名を編集", "変更後のファイル名:", initialvalue=initial, parent=self)
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name:
            return
        if Path(new_name).name != new_name or re.search(r'[<>:"/\\|?*]', new_name):
            messagebox.showerror(APP_TITLE, "フォルダ区切りやWindows禁止文字は使用できません。")
            return
        record.new_name = new_name
        record.japanese_title = new_name[:-len(record.extension)] if record.extension and new_name.lower().endswith(record.extension.lower()) else Path(new_name).stem
        record.source = "手動編集"
        record.status = "候補"
        record.confidence = 1.0
        record.enabled = record.new_name.casefold() != record.old_name.casefold()
        validate_plan(self.records)
        self._refresh_tree()
        self.tree.selection_set(str(idx))

    def _save_csv(self) -> None:
        root = self._root_path()
        if not root or not self.records:
            if not self.records:
                messagebox.showinfo(APP_TITLE, "保存するリネーム一覧がありません。")
            return
        path = filedialog.asksaveasfilename(
            title="リネームリストを保存",
            initialdir=str(root),
            initialfile=f"rom_rename_list_{datetime.now():%Y%m%d_%H%M%S}.csv",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if path:
            export_csv(Path(path), root, self.records)
            self._log(f"CSVリスト保存: {path}")

    def _load_csv(self) -> None:
        root = self._root_path()
        if not root:
            return
        path = filedialog.askopenfilename(title="リネームリストを読込", filetypes=[("CSV", "*.csv"), ("すべて", "*.*")])
        if not path:
            return
        try:
            self.records = import_csv(
                Path(path), root, self.main_rom_only_var.get(), self.japan_only_var.get()
            )
            self._refresh_tree()
            self._log(f"CSVリスト読込: {path} ({len(self.records)}件)")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"CSV読込に失敗しました:\n{exc}")

    def _apply_plan(self) -> None:
        root = self._root_path()
        if not root:
            return
        count = sum(1 for r in self.records if r.enabled and not r.protected and not r.variant_excluded and not r.region_excluded and r.new_name and r.new_name.casefold() != r.old_name.casefold())
        if count == 0:
            messagebox.showinfo(APP_TITLE, "適用対象がありません。左端の『適用』欄を確認してください。")
            return
        completed_dir = self._effective_completed_dir(root)
        per_directory_completed = self.per_directory_completed_var.get()
        extra = ""
        if completed_dir is not None:
            if per_directory_completed:
                extra = (
                    "\n\nリネーム成功したファイルだけ、元サブフォルダの親に"
                    f"『サブフォルダ名＋{DEFAULT_COMPLETED_FOLDER_NAME}』を作って移動します。"
                    "\n例: ROM\\SFC\\game.sfc → ROM\\SFC完了\\日本語名.sfc"
                    "\n\n派生ROM・保護対象・未選択・失敗ファイルは移動しません。"
                )
            else:
                extra = (
                    f"\n\nリネーム成功したファイルだけ、次の一括完了フォルダへ移動します。"
                    f"\n{completed_dir}\n\n派生ROM・保護対象・未選択・失敗ファイルは移動しません。"
                )
        if not messagebox.askyesno(
            APP_TITLE,
            f"{count}件のファイル名を実際に変更します。\n\n上書きは行いません。{extra}\n\n実行しますか？",
        ):
            return

        history, errors = apply_rename_plan(
            root, self.records, completed_dir=completed_dir,
            per_directory_completed=per_directory_completed,
        )
        if errors:
            messagebox.showerror(APP_TITLE, "リネームを実行できませんでした:\n\n" + "\n".join(errors[:20]))
            self._log("リネーム中止: " + errors[0])
            return

        if completed_dir is None:
            self._log(f"リネーム完了: {count}件 / 履歴: {history}")
            messagebox.showinfo(APP_TITLE, f"{count}件のリネームが完了しました。\n\n履歴:\n{history}")
        else:
            if per_directory_completed:
                self._log(
                    f"リネーム＋サブフォルダ名付き完了フォルダ移動: {count}件 / 履歴: {history}"
                )
                messagebox.showinfo(
                    APP_TITLE,
                    f"{count}件のリネームと『サブフォルダ名＋{DEFAULT_COMPLETED_FOLDER_NAME}』フォルダへの移動が完了しました。"
                    f"\n\n履歴:\n{history}",
                )
            else:
                self._log(f"リネーム＋完了フォルダ移動: {count}件 / {completed_dir} / 履歴: {history}")
                messagebox.showinfo(
                    APP_TITLE,
                    f"{count}件のリネームと完了フォルダへの移動が完了しました。"
                    f"\n\n完了フォルダ:\n{completed_dir}\n\n履歴:\n{history}",
                )

        # パスを最終位置へ追随させ、二重実行を防ぐ。
        for r in self.records:
            if (
                r.enabled
                and not r.protected
                and not r.variant_excluded
                and not r.region_excluded
                and r.new_name
                and r.new_name.casefold() != r.old_name.casefold()
            ):
                final = completed_target_path(root, r, completed_dir, per_directory_completed)
                r.path = str(final.resolve())
                try:
                    r.relative_path = str(final.relative_to(root))
                except ValueError:
                    r.relative_path = str(final)
                r.old_name = r.new_name
                r.enabled = False
                r.status = "適用済み・完了へ移動" if completed_dir is not None else "適用済み"
        self._refresh_tree()

    def _move_already_renamed(self) -> None:
        if self.busy:
            return
        root = self._root_path()
        if not root:
            return
        completed_dir = self._configured_completed_dir(root)
        per_directory_completed = self.per_directory_completed_var.get()
        if not per_directory_completed and completed_dir == root:
            messagebox.showerror(APP_TITLE, "完了フォルダにはROMフォルダそのもの以外を指定してください。")
            return

        candidates = collect_already_renamed_candidates(
            root, self.records, completed_dir, per_directory_completed, self.japan_only_var.get()
        )
        if not candidates:
            messagebox.showinfo(
                APP_TITLE,
                "移動できるリネーム済みROMが見つかりませんでした。\n\n"
                "以前このアプリでリネームしたファイルは履歴から自動検出します。\n"
                "履歴がないファイルは『候補を自動作成』で確認済みにすると移動対象になります。",
            )
            return

        preview_lines = []
        for source, target, reason in candidates[:12]:
            try:
                src_label = str(source.relative_to(root))
            except ValueError:
                src_label = str(source)
            try:
                dst_label = str(target.relative_to(root if per_directory_completed else completed_dir))
            except ValueError:
                dst_label = str(target)
            preview_lines.append(f"・{src_label}  →  {dst_label}  [{reason}]")
        if len(candidates) > 12:
            preview_lines.append(f"…ほか {len(candidates) - 12}件")

        preview = "\n".join(preview_lines)
        destination_text = (
            f"元サブフォルダの親に作る『サブフォルダ名＋{DEFAULT_COMPLETED_FOLDER_NAME}』"
            if per_directory_completed
            else str(completed_dir)
        )
        if not messagebox.askyesno(
            APP_TITLE,
            f"リネーム済みと確認できた {len(candidates)}件を完了フォルダへ移動します。\n\n"
            f"移動先:\n{destination_text}\n\n{preview}\n\n"
            "同名ファイルがある場合は上書きせず中止します。実行しますか？",
        ):
            return

        history, errors = move_already_renamed_to_completed(
            root, candidates, completed_dir, per_directory_completed
        )
        if errors:
            messagebox.showerror(APP_TITLE, "移動を実行できませんでした:\n\n" + "\n".join(errors[:20]))
            self._log("リネーム済みROM移動中止: " + errors[0])
            return

        moved_map = {_casefold_path(source): target for source, target, _reason in candidates}
        for record in self.records:
            current_key = _casefold_path(Path(record.path))
            target = moved_map.get(current_key)
            if target is None:
                continue
            record.path = str(target.resolve())
            try:
                record.relative_path = str(target.relative_to(root))
            except ValueError:
                record.relative_path = str(target)
            record.enabled = False
            record.status = "リネーム済み・完了へ移動"
        self._refresh_tree()

        if per_directory_completed:
            self._log(
                f"リネーム済みROMをサブフォルダ名付き完了へ移動: {len(candidates)}件 / 履歴: {history}"
            )
            messagebox.showinfo(
                APP_TITLE,
                f"{len(candidates)}件のリネーム済みROMを『サブフォルダ名＋{DEFAULT_COMPLETED_FOLDER_NAME}』フォルダへ移動しました。"
                f"\n\n履歴:\n{history}",
            )
        else:
            self._log(f"リネーム済みROMを完了へ移動: {len(candidates)}件 / {completed_dir} / 履歴: {history}")
            messagebox.showinfo(
                APP_TITLE,
                f"{len(candidates)}件のリネーム済みROMを完了フォルダへ移動しました。\n\n"
                f"完了フォルダ:\n{completed_dir}\n\n履歴:\n{history}",
            )

    def _undo_last(self) -> None:
        root = self._root_path()
        if not root:
            return
        history = latest_history(root)
        if not history:
            messagebox.showinfo(APP_TITLE, "戻せる変更履歴がありません。")
            return
        if not messagebox.askyesno(APP_TITLE, f"直近の変更を元に戻しますか？\n\n{history.name}"):
            return
        errors = undo_history(history)
        if errors:
            messagebox.showwarning(APP_TITLE, "一部または全部を戻せませんでした:\n\n" + "\n".join(errors[:20]))
            self._log("Undoでエラー: " + errors[0])
        else:
            self._log(f"Undo完了: {history.name}")
            messagebox.showinfo(APP_TITLE, "直近の変更を元に戻しました。")
            self.records.clear()
            self._refresh_tree()

    def _on_close(self) -> None:
        data = {
            "rom_dir": self.rom_dir_var.get().strip(),
            "dat_paths": [str(p) for p in self.dat_paths],
            "db_sources": self.settings.get("db_sources", ["no-intro", "redump"]),
            "model": self.model_var.get().strip(),
            "recursive": self.recursive_var.get(),
            "large_hash": self.large_hash_var.get(),
            "protect_multi_zip": self.protect_multi_zip_var.get(),
            "main_rom_only": self.main_rom_only_var.get(),
            "japan_only": self.japan_only_var.get(),
            "move_completed": self.move_completed_var.get(),
            "per_directory_completed": self.per_directory_completed_var.get(),
            "completed_dir": self.completed_dir_var.get().strip(),
            "min_confidence": self.min_conf_var.get(),
        }
        # APIキーはsettings.jsonへ保存しない。永続化する場合はWindows資格情報マネージャーのみを使う。
        try:
            save_settings(data)
        except Exception:
            pass
        self.destroy()


def main() -> None:
    app = RomRenamerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
