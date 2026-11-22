#!/usr/bin/env python3
"""Tape Launcher - search, download, enable/disable and remove Minecraft mods.

Uses the public Modrinth API (no API key needed). Python 3.8+, standard library only.
"""
import base64
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import zipfile
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox

if platform.system() == "Windows":  # crisp text on high-DPI screens
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

API = "https://api.modrinth.com/v2"
UA = {"User-Agent": "tape-launcher/2.0 (personal project)"}
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE_DIR, "launcher_config.json")
DATA_DIR = os.path.join(BASE_DIR, "tape_data")          # everything the launcher downloads lives here
GAME_DIR = os.path.join(DATA_DIR, "game")               # saves, mods, options.txt
VERSIONS_DIR = os.path.join(DATA_DIR, "versions")
LIBS_DIR = os.path.join(DATA_DIR, "libraries")
ASSETS_DIR = os.path.join(DATA_DIR, "assets")
RUNTIME_DIR = os.path.join(DATA_DIR, "runtime")         # Java downloaded from Mojang
LOG_FILE = os.path.join(DATA_DIR, "game_log.txt")

BG = "#000000"
SIDEBAR = "#08080c"
PANEL = "#0f0f16"
PANEL2 = "#181822"
BORDER = "#23233a"
ACCENT = "#7c5cff"
ACCENT_H = "#9a82ff"
TEXT = "#f4f4f8"
MUTED = "#8b8b9c"
DANGER = "#ff5c7a"
GREEN = "#3ddc84"
PALETTE = ["#7c5cff", "#4c8dff", "#22b8a0", "#ff8a4c", "#ff5c9a", "#b45cff", "#3bb7e8", "#e0a93a"]

FONT = "Segoe UI"
S = 1.0


# ---------------------------------------------------------------- helpers
def px(n):
    return int(round(n * S))


def F(size, bold=False):
    return (FONT, -max(8, px(size)), "bold" if bold else "normal")


def mix(c1, c2, t):
    t = max(0.0, min(1.0, t))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rr(x1, y1, x2, y2, r):
    """Points for a rounded rectangle (use with smooth=True)."""
    r = max(1, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y1 + r, x2, y2 - r,
            x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2, x1, y2, x1, y2 - r,
            x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]


_fonts = {}


def measure(font, text):
    f = _fonts.get(font)
    if f is None:
        f = _fonts[font] = tkfont.Font(font=font)
    return f.measure(text)


def fit(text, font, maxw):
    if maxw <= 10:
        return ""
    if measure(font, text) <= maxw:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(font, text[:mid] + "\u2026") <= maxw:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "\u2026"


def compact(n):
    n = int(n)
    if n >= 1000000:
        return "%.1fM" % (n / 1000000)
    if n >= 1000:
        return "%.1fK" % (n / 1000)
    return str(n)


def pretty(name):
    base = re.sub(r"\.jar(\.disabled)?$", "", name, flags=re.I)
    head = re.split(r"[-_+ ]v?\d", base, maxsplit=1)[0] or base
    words = [w for w in re.split(r"[-_ ]+", head) if w]
    return " ".join(w[:1].upper() + w[1:] for w in words) or base


def default_mods_dir():
    return os.path.join(GAME_DIR, "mods")


def load_config():
    cfg = {"mods_dir": default_mods_dir(), "version": "1.20.1", "loader": "fabric", "player": "Player",
           "ram": 4096, "java": "", "client_id": ""}
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


def api_get(path, params=None):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def dark_titlebar(win):
    if platform.system() != "Windows":
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        for attr in (20, 19):
            v = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass


def draw_icon(c, kind, cx, cy, s, col):
    lw = max(2, px(2))
    if kind == "home":
        c.create_polygon(cx, cy - s, cx + s, cy - s * 0.05, cx + s * 0.7, cy - s * 0.05, cx + s * 0.7, cy + s,
                         cx - s * 0.7, cy + s, cx - s * 0.7, cy - s * 0.05, cx - s, cy - s * 0.05,
                         fill=col, outline="")
    elif kind == "mods":
        g = s * 0.14
        for dx in (-1, 1):
            for dy in (-1, 1):
                x1, x2 = (cx + g, cx + s) if dx > 0 else (cx - s, cx - g)
                y1, y2 = (cy + g, cy + s) if dy > 0 else (cy - s, cy - g)
                c.create_rectangle(x1, y1, x2, y2, fill=col, outline="")
    elif kind == "browse":
        r = s * 0.7
        c.create_oval(cx - s * 0.2 - r, cy - s * 0.2 - r, cx - s * 0.2 + r, cy - s * 0.2 + r,
                      outline=col, width=lw)
        c.create_line(cx + s * 0.3, cy + s * 0.3, cx + s, cy + s, fill=col, width=lw + 1, capstyle="round")
    elif kind == "settings":
        for yy, kx in ((-0.65, -0.35), (0.0, 0.45), (0.65, -0.1)):
            y = cy + s * yy
            r = s * 0.24
            c.create_line(cx - s, y, cx + s, y, fill=col, width=lw)
            c.create_oval(cx + s * kx - r, y - r, cx + s * kx + r, y + r, fill=col, outline="")


# ---------------------------------------------------------------- game engine
# Downloads and starts Minecraft by itself (official Mojang servers). No installed Minecraft needed.
MANIFEST_URLS = ["https://piston-meta.mojang.com/mc/game/version_manifest_v2.json",
                 "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"]
_JAVA_HASH = "2ec0cc96c44e5a76b9c8b7c39df7210883d12871"
JAVA_ALL_URLS = ["https://launchermeta.mojang.com/v1/products/java-runtime/%s/all.json" % _JAVA_HASH,
                 "https://piston-meta.mojang.com/v1/products/java-runtime/%s/all.json" % _JAVA_HASH]
ASSET_URL = "https://resources.download.minecraft.net/"
LIB_URL = "https://libraries.minecraft.net/"
LAUNCHER_NAME, LAUNCHER_VER = "tape-launcher", "2.0"
FEATURES = {}  # optional launcher features (demo mode, custom resolution...) - none enabled


class LaunchError(Exception):
    pass


def http_json(urls):
    if isinstance(urls, str):
        urls = [urls]
    last = None
    for u in urls:
        try:
            req = urllib.request.Request(u, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:
            last = exc
    raise last


def json_cached(urls, path):
    """Fetch JSON and keep a copy on disk, so the game still starts without internet."""
    try:
        data = http_json(urls)
    except Exception:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        raise
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass
    return data


def file_ok(path, size=None):
    try:
        return os.path.isfile(path) and (size is None or os.path.getsize(path) == size)
    except OSError:
        return False


def download_file(url, path, sha1=None, size=None, tries=3):
    if file_ok(path, size):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    last = None
    for _ in range(tries):
        try:
            h = hashlib.sha1()
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r, open(path + ".part", "wb") as out:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
                    h.update(chunk)
            if sha1 and h.hexdigest().lower() != sha1.lower():
                raise OSError("checksum mismatch for %s" % os.path.basename(path))
            os.replace(path + ".part", path)
            return
        except Exception as exc:
            last = exc
    raise LaunchError("Download failed:\n%s\n(%s)" % (url, last))


def download_all(tasks, progress, label, workers=16):
    """tasks = [(url, path, sha1, size)] - downloaded in parallel, skipped when already present."""
    tasks = [t for t in {t[1]: t for t in tasks}.values() if not file_ok(t[1], t[3])]
    total = len(tasks)
    if not total:
        return
    done, lock = [0], threading.Lock()

    def one(t):
        download_file(*t)
        with lock:
            done[0] += 1
            n = done[0]
        if n % 5 == 0 or n == total:
            progress("%s  %d/%d" % (label, n, total), n / total)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, t) for t in tasks]
        try:
            for f in futs:
                f.result()
        except Exception:
            for f in futs:
                f.cancel()
            raise


def os_name():
    s = platform.system()
    return "windows" if s == "Windows" else ("osx" if s == "Darwin" else "linux")


def arch_bits():
    return 64 if sys.maxsize > 2 ** 32 else 32


def rule_os_match(o):
    if "name" in o and o["name"] != os_name():
        return False
    if "arch" in o:
        want, m = o["arch"], platform.machine().lower()
        if want == "x86" and arch_bits() != 32:
            return False
        if want in ("arm64", "aarch64") and m not in ("arm64", "aarch64"):
            return False
    if "version" in o:
        try:
            if not re.search(o["version"], platform.version()):
                return False
        except re.error:
            pass
    return True


def rules_ok(rules):
    if not rules:
        return True
    allowed = False
    for r in rules:
        match = True
        if "os" in r:
            match = rule_os_match(r["os"])
        if match and "features" in r:
            match = all(FEATURES.get(k, False) == v for k, v in r["features"].items())
        if match:
            allowed = (r.get("action") == "allow")
    return allowed


def maven_parts(name):
    """'group:artifact:version[:classifier][@ext]' -> (relative path, dedupe key)"""
    ext = "jar"
    if "@" in name:
        name, ext = name.split("@", 1)
    p = name.split(":")
    g, a, v = p[0], p[1], p[2]
    cl = p[3] if len(p) > 3 else ""
    fn = "%s-%s%s.%s" % (a, v, "-" + cl if cl else "", ext)
    return "%s/%s/%s/%s" % (g.replace(".", "/"), a, v, fn), "%s:%s:%s" % (g, a, cl)


def resolve_libraries(libs):
    """-> (classpath, downloads, natives_to_extract)"""
    chosen = {}
    for lib in libs:
        if rules_ok(lib.get("rules")):
            chosen[maven_parts(lib["name"])[1]] = lib  # later entries (the mod loader) override earlier ones
    cp, dl, nat = [], [], []
    for lib in chosen.values():
        dls = lib.get("downloads")
        if dls is None:  # Fabric / Quilt style: maven coordinates + base url
            base = lib.get("url") or LIB_URL
            if not base.endswith("/"):
                base += "/"
            rel = maven_parts(lib["name"])[0]
            path = os.path.join(LIBS_DIR, *rel.split("/"))
            dl.append((base + rel, path, lib.get("sha1"), lib.get("size")))
            cp.append(path)
            continue
        art = dls.get("artifact")
        if art:
            path = os.path.join(LIBS_DIR, *art["path"].split("/"))
            dl.append((art["url"], path, art.get("sha1"), art.get("size")))
            cp.append(path)
        nm = lib.get("natives")
        if nm and os_name() in nm:  # old versions (<= 1.18): natives are separate "classifier" jars
            cl = nm[os_name()].replace("${arch}", str(arch_bits()))
            a2 = (dls.get("classifiers") or {}).get(cl)
            if a2:
                path = os.path.join(LIBS_DIR, *a2["path"].split("/"))
                dl.append((a2["url"], path, a2.get("sha1"), a2.get("size")))
                nat.append((path, (lib.get("extract") or {}).get("exclude", [])))
    return cp, dl, nat


def extract_natives(items, dest):
    os.makedirs(dest, exist_ok=True)
    for path, exclude in items:
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.endswith("/") or n.startswith("META-INF") or any(n.startswith(e) for e in exclude):
                    continue
                target = os.path.join(dest, os.path.basename(n))
                info = z.getinfo(n)
                if os.path.isfile(target) and os.path.getsize(target) == info.file_size:
                    continue
                with z.open(n) as src_f, open(target, "wb") as out:
                    shutil.copyfileobj(src_f, out)


def prepare_assets(vj, progress):
    ai = vj["assetIndex"]
    idx_path = os.path.join(ASSETS_DIR, "indexes", ai["id"] + ".json")
    download_file(ai["url"], idx_path, ai.get("sha1"), ai.get("size"))
    with open(idx_path, "r", encoding="utf-8") as f:
        idx = json.load(f)
    objs = idx.get("objects", {})
    tasks = []
    for o in objs.values():
        h = o["hash"]
        tasks.append((ASSET_URL + h[:2] + "/" + h, os.path.join(ASSETS_DIR, "objects", h[:2], h), h, o.get("size")))
    download_all(tasks, progress, "Downloading assets")
    legacy = None
    if idx.get("virtual") or idx.get("map_to_resources"):  # very old versions want real file names
        legacy = os.path.join(ASSETS_DIR, "virtual", ai["id"])
        for name, o in objs.items():
            h = o["hash"]
            src_p = os.path.join(ASSETS_DIR, "objects", h[:2], h)
            dst = os.path.join(legacy, *name.split("/"))
            if not os.path.isfile(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src_p, dst)
    return ai["id"], legacy


def runtime_platform():
    m, s = platform.machine().lower(), platform.system()
    if s == "Windows":
        if m in ("arm64", "aarch64"):
            return "windows-arm64"
        return "windows-x64" if m in ("amd64", "x86_64") else "windows-x86"
    if s == "Darwin":
        return "mac-os-arm64" if m in ("arm64", "aarch64") else "mac-os"
    return "linux" if m in ("x86_64", "amd64") else "linux-i386"


def java_bin(root):
    if platform.system() == "Windows":
        return os.path.join(root, "bin", "java.exe")
    if platform.system() == "Darwin":
        return os.path.join(root, "jre.bundle", "Contents", "Home", "bin", "java")
    return os.path.join(root, "bin", "java")


def install_runtime(component, progress):
    plat = runtime_platform()
    cands = [plat] + (["windows-x64"] if plat.startswith("windows") and plat != "windows-x64" else [])
    for p in cands:
        root = os.path.join(RUNTIME_DIR, component, p)
        if os.path.isfile(os.path.join(root, ".tape_ok")) and os.path.isfile(java_bin(root)):
            return java_bin(root)
    progress("Preparing Java (one time)\u2026", 0.0)
    allj = json_cached(JAVA_ALL_URLS, os.path.join(DATA_DIR, "java_runtimes.json"))
    entry, used = None, None
    for p in cands:
        lst = (allj.get(p) or {}).get(component)
        if lst:
            entry, used = lst[0], p
            break
    if not entry:
        raise LaunchError("Mojang has no Java runtime '%s' for %s." % (component, plat))
    root = os.path.join(RUNTIME_DIR, component, used)
    files = http_json(entry["manifest"]["url"])["files"]
    tasks, links, execs = [], [], []
    for rel, info in files.items():
        path = os.path.join(root, *rel.split("/"))
        kind = info.get("type")
        if kind == "directory":
            os.makedirs(path, exist_ok=True)
        elif kind == "file":
            raw = info["downloads"]["raw"]
            tasks.append((raw["url"], path, raw.get("sha1"), raw.get("size")))
            if info.get("executable"):
                execs.append(path)
        elif kind == "link":
            links.append((path, info.get("target", "")))
    download_all(tasks, progress, "Downloading Java")
    if platform.system() != "Windows":
        for p in execs:
            os.chmod(p, 0o755)
        for path, target in links:
            if not os.path.lexists(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                os.symlink(target, path)
    exe = java_bin(root)
    if not os.path.isfile(exe):
        raise LaunchError("Java download finished but %s is missing." % exe)
    with open(os.path.join(root, ".tape_ok"), "w") as f:
        f.write("ok")
    return exe


def ensure_java(vj, cfg, progress):
    custom = (cfg.get("java") or "").strip().strip('"')
    if custom:
        if not os.path.isfile(custom):
            raise LaunchError("The Java path from Settings does not exist:\n%s" % custom)
        return custom
    comp = (vj.get("javaVersion") or {}).get("component", "jre-legacy")
    try:
        return install_runtime(comp, progress)
    except Exception as exc:
        found = shutil.which("java.exe" if platform.system() == "Windows" else "java")
        if found:
            return found
        raise LaunchError("Could not get Java: %s" % exc)


def loader_profile(loader, mc):
    meta = ("https://meta.fabricmc.net/v2/versions/loader/" if loader == "fabric"
            else "https://meta.quiltmc.org/v3/versions/loader/")
    lst = json_cached(meta + urllib.parse.quote(mc), os.path.join(DATA_DIR, "loader_%s_%s.json" % (loader, mc)))
    if not lst:
        raise LaunchError("%s has no build for Minecraft %s.\nChange the version or loader in Settings."
                          % (loader.capitalize(), mc))
    ver = lst[0]["loader"]["version"]
    for e in lst:
        v = e["loader"]["version"]
        if e["loader"].get("stable", True) and not re.search(r"alpha|beta|pre|rc", v, re.I):
            ver = v
            break
    url = meta + urllib.parse.quote(mc) + "/" + urllib.parse.quote(ver) + "/profile/json"
    return json_cached(url, os.path.join(DATA_DIR, "loader_%s_%s_%s_profile.json" % (loader, mc, ver)))


def merge_profiles(vj, prof):
    if not prof:
        return vj
    m = dict(vj)
    m["id"] = prof.get("id", vj["id"])
    m["mainClass"] = prof.get("mainClass", vj["mainClass"])
    m["libraries"] = list(vj.get("libraries", [])) + list(prof.get("libraries", []))
    pa = prof.get("arguments")
    if pa:
        base = vj.get("arguments") or {}
        m["arguments"] = {"jvm": list(base.get("jvm", [])) + list(pa.get("jvm", [])),
                          "game": list(base.get("game", [])) + list(pa.get("game", []))}
    return m


def expand_args(items, variables):
    out = []
    for it in items:
        if isinstance(it, str):
            vals = [it]
        else:
            if not rules_ok(it.get("rules")):
                continue
            v = it.get("value", [])
            vals = [v] if isinstance(v, str) else list(v)
        out.extend(re.sub(r"\$\{(\w+)\}", lambda m: variables.get(m.group(1), m.group(0)), s) for s in vals)
    return out


def offline_uuid(name):
    h = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest())
    h[6] = (h[6] & 0x0F) | 0x30
    h[8] = (h[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(h)))


def prepare_game(cfg, progress):
    """Download everything that is missing and build the Java command line."""
    mc = (cfg.get("version") or "").strip()
    loader = cfg.get("loader", "fabric")
    if loader in ("forge", "neoforge"):
        raise LaunchError("The built-in launcher can start Fabric and Quilt.\n"
                          "Open Settings and set Mod loader to Fabric (Forge/NeoForge are only used for "
                          "searching mods).")
    for d in (DATA_DIR, VERSIONS_DIR, LIBS_DIR, ASSETS_DIR):
        os.makedirs(d, exist_ok=True)

    progress("Reading Minecraft version list\u2026", 0.0)
    manifest = json_cached(MANIFEST_URLS, os.path.join(DATA_DIR, "version_manifest_v2.json"))
    entry = next((v for v in manifest["versions"] if v["id"] == mc), None)
    if not entry:
        raise LaunchError("Minecraft version '%s' does not exist.\nCheck the version in Settings (e.g. 1.20.1)." % mc)
    vj = json_cached(entry["url"], os.path.join(VERSIONS_DIR, mc, mc + ".json"))

    prof = None
    if loader in ("fabric", "quilt"):
        progress("Preparing %s\u2026" % loader.capitalize(), 0.0)
        prof = loader_profile(loader, mc)
    full = merge_profiles(vj, prof)

    java = ensure_java(vj, cfg, progress)

    client = vj["downloads"]["client"]
    client_path = os.path.join(VERSIONS_DIR, mc, mc + ".jar")
    cp, dl, nat = resolve_libraries(full.get("libraries", []))
    download_all([(client["url"], client_path, client.get("sha1"), client.get("size"))], progress,
                 "Downloading Minecraft")
    download_all(dl, progress, "Downloading libraries")
    assets_id, legacy = prepare_assets(vj, progress)
    natives_dir = os.path.join(VERSIONS_DIR, mc, "natives")
    extract_natives(nat, natives_dir)
    cp.append(client_path)

    mods_dir = os.path.normpath(cfg.get("mods_dir") or default_mods_dir())
    game_dir = os.path.dirname(mods_dir) if os.path.basename(mods_dir).lower() == "mods" else GAME_DIR
    os.makedirs(game_dir, exist_ok=True)
    os.makedirs(mods_dir, exist_ok=True)

    auth = cfg.get("auth")
    if auth:  # signed in with Microsoft
        name, uid, token, utype = auth["name"], auth["uuid"], auth["token"], "msa"
    else:  # offline mode
        name = re.sub(r"[^A-Za-z0-9_]", "", cfg.get("player") or "")[:16] or "Player"
        uid, token, utype = offline_uuid(name).replace("-", ""), "0", "legacy"
    variables = {
        "auth_player_name": name, "version_name": full["id"], "game_directory": game_dir,
        "assets_root": ASSETS_DIR, "assets_index_name": assets_id,
        "auth_uuid": uid, "auth_access_token": token, "clientid": "0",
        "auth_xuid": "0", "user_type": utype, "version_type": full.get("type", "release"),
        "user_properties": "{}", "game_assets": legacy or ASSETS_DIR, "natives_directory": natives_dir,
        "launcher_name": LAUNCHER_NAME, "launcher_version": LAUNCHER_VER, "classpath": os.pathsep.join(cp),
        "classpath_separator": os.pathsep, "library_directory": LIBS_DIR,
    }
    if "arguments" in full:
        jvm = expand_args(full["arguments"].get("jvm", []), variables)
        game = expand_args(full["arguments"].get("game", []), variables)
    else:  # old format (Minecraft <= 1.12)
        jvm = ["-Djava.library.path=" + natives_dir]
        game = expand_args(full.get("minecraftArguments", "").split(), variables)
    if "-cp" not in jvm and "-classpath" not in jvm:
        jvm += ["-cp", variables["classpath"]]
    try:
        ram = max(1024, int(cfg.get("ram") or 4096))
    except (TypeError, ValueError):
        ram = 4096
    cmd = [java, "-Xmx%dM" % ram, "-Xms512M"] + jvm + [full["mainClass"]] + game
    return cmd, game_dir


def run_game(cfg, progress):
    cmd, cwd = prepare_game(cfg, progress)
    progress("Starting Minecraft\u2026", 1.0)
    flags = 0x08000000 if platform.system() == "Windows" else 0  # CREATE_NO_WINDOW
    log = open(LOG_FILE, "wb")
    try:
        return subprocess.Popen(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    finally:
        log.close()


# ---------------------------------------------------------------- Microsoft login
# Device-code flow (like Prism / Fabric launcher): the browser opens, you type a short code, done.
# Afterwards the refresh token is kept in tape_data/account.json and login is silent.
MS_CLIENT_ID = ""  # optional: put your Azure Application (client) ID here instead of in Settings
ACCOUNT_FILE = os.path.join(DATA_DIR, "account.json")
MS_SCOPE = "XboxLive.signin offline_access"
MS_BASE = "https://login.microsoftonline.com/consumers/oauth2/v2.0/"
XERR = {2148916233: "This Microsoft account has no Xbox profile. Sign in once at xbox.com to create it.",
        2148916235: "Xbox Live is not available in your country.",
        2148916238: "This is a child account. An adult must add it to a Microsoft family first."}
SETUP_HELP = ("Sign-in needs the launcher's own Microsoft app ID. Launchers like Feather or Lunar have one built in;\n"
              "for Tape Launcher you create it once (about 3 minutes):\n\n"
              "1. portal.azure.com > App registrations > New registration.\n"
              "    Account type: 'Personal Microsoft accounts only'.\n"
              "    Redirect URI: platform 'Mobile and desktop applications', value http://localhost\n"
              "2. Authentication > turn on 'Allow public client flows'.\n"
              "3. Paste the 'Application (client) ID' into MS_CLIENT_ID at the top of the login section in the\n"
              "    code (or into Settings). After that the field disappears from Settings.\n"
              "4. Ask Mojang to allow the app: aka.ms/mce-reviewappid\n"
              "    (until it is approved, Minecraft login can say 'Invalid app registration').\n\n"
              "From then on everyone only clicks Sign in - the browser returns to the launcher by itself.")


class AuthError(Exception):
    pass


def client_id(cfg):
    return (cfg.get("client_id") or os.environ.get("TAPE_MS_CLIENT_ID") or MS_CLIENT_ID or "").strip()


def _request(req):
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8"))
        except Exception:
            data = {}
        data["_status"] = e.code
        return data


def _post(url, data, as_json=False):
    if as_json:
        body, ctype = json.dumps(data).encode("utf-8"), "application/json"
    else:
        body, ctype = urllib.parse.urlencode(data).encode("utf-8"), "application/x-www-form-urlencoded"
    headers = dict(UA)
    headers.update({"Content-Type": ctype, "Accept": "application/json"})
    return _request(urllib.request.Request(url, data=body, headers=headers))


def _get_auth(url, token):
    headers = dict(UA)
    headers.update({"Authorization": "Bearer " + token, "Accept": "application/json"})
    return _request(urllib.request.Request(url, headers=headers))


def ms_device_start(cid):
    d = _post(MS_BASE + "devicecode", {"client_id": cid, "scope": MS_SCOPE})
    if "user_code" not in d:
        msg = (d.get("error_description") or "Microsoft refused the request. Check the Client ID in Settings.")
        raise AuthError(msg.splitlines()[0])
    return d


def ms_device_wait(cid, d, cancelled):
    interval = int(d.get("interval", 5))
    end = time.time() + int(d.get("expires_in", 900))
    while time.time() < end:
        for _ in range(interval):
            if cancelled():
                raise AuthError("Sign-in cancelled.")
            time.sleep(1)
        t = _post(MS_BASE + "token", {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                      "client_id": cid, "device_code": d["device_code"]})
        if "access_token" in t:
            return t
        err = t.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        raise AuthError((t.get("error_description") or err or "Sign-in failed.").splitlines()[0])
    raise AuthError("The sign-in code expired. Try again.")


def ms_login_loopback(cid, on_ready, cancelled):
    """Browser login like Feather/Lunar: Microsoft redirects back to a tiny local server, no code to type."""
    import http.server
    box = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in q or "error" in q:
                box["q"] = {k: v[0] for k, v in q.items()}
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body style='background:#000;color:#f4f4f8;font-family:sans-serif;"
                             b"text-align:center;padding-top:20vh'><h1>Signed in</h1>"
                             b"<p>You can close this tab and go back to Tape Launcher.</p></body></html>")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)  # OSError here -> caller falls back to a code
    srv.timeout = 1
    redirect = "http://localhost:%d" % srv.server_address[1]
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = uuid.uuid4().hex
    url = MS_BASE + "authorize?" + urllib.parse.urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": redirect, "response_mode": "query",
        "scope": MS_SCOPE, "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "prompt": "select_account"})
    try:
        on_ready(url)
        end = time.time() + 300
        while "q" not in box:
            if cancelled():
                raise AuthError("Sign-in cancelled.")
            if time.time() > end:
                raise AuthError("Sign-in timed out. Try again.")
            srv.handle_request()
    finally:
        srv.server_close()
    q = box["q"]
    if "code" not in q or q.get("state") != state:
        raise AuthError((q.get("error_description") or q.get("error") or "Sign-in was refused.").splitlines()[0])
    t = _post(MS_BASE + "token", {"grant_type": "authorization_code", "client_id": cid, "code": q["code"],
                                  "redirect_uri": redirect, "code_verifier": verifier, "scope": MS_SCOPE})
    if "access_token" not in t:
        raise AuthError((t.get("error_description") or "Token exchange failed.").splitlines()[0])
    return t


def minecraft_login(ms_access):
    """Microsoft token -> Xbox Live -> XSTS -> Minecraft token + profile."""
    x = _post("https://user.auth.xboxlive.com/user/authenticate", {
        "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com", "RpsTicket": "d=" + ms_access},
        "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT"}, as_json=True)
    if "Token" not in x:
        raise AuthError("Xbox Live login failed.")
    uhs = x["DisplayClaims"]["xui"][0]["uhs"]
    xs = _post("https://xsts.auth.xboxlive.com/xsts/authorize", {
        "Properties": {"SandboxId": "RETAIL", "UserTokens": [x["Token"]]},
        "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"}, as_json=True)
    if "Token" not in xs:
        raise AuthError(XERR.get(xs.get("XErr"), "Xbox security token was refused (%s)." % xs.get("XErr", "?")))
    m = _post("https://api.minecraftservices.com/authentication/login_with_xbox",
              {"identityToken": "XBL3.0 x=%s;%s" % (uhs, xs["Token"])}, as_json=True)
    if "access_token" not in m:
        raise AuthError(m.get("errorMessage") or m.get("error") or
                        "Minecraft login failed. Is your app approved by Mojang? (aka.ms/mce-reviewappid)")
    p = _get_auth("https://api.minecraftservices.com/minecraft/profile", m["access_token"])
    if "id" not in p:
        raise AuthError("This account does not own Minecraft: Java Edition.")
    return {"name": p["name"], "uuid": p["id"], "token": m["access_token"]}


def save_account(refresh, prof):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(ACCOUNT_FILE, "w", encoding="utf-8") as f:
            json.dump({"refresh_token": refresh, "name": prof["name"], "uuid": prof["uuid"]}, f)
    except OSError:
        pass


def load_account():
    try:
        with open(ACCOUNT_FILE, "r", encoding="utf-8") as f:
            a = json.load(f)
        return a if a.get("refresh_token") else None
    except (OSError, ValueError):
        return None


def clear_account():
    try:
        os.remove(ACCOUNT_FILE)
    except OSError:
        pass


def sign_in_silent(cfg):
    """Saved refresh token -> fresh Minecraft token (no browser)."""
    acct = load_account()
    cid = client_id(cfg)
    if not acct or not cid:
        raise AuthError("Not signed in.")
    t = _post(MS_BASE + "token", {"grant_type": "refresh_token", "client_id": cid,
                                  "refresh_token": acct["refresh_token"], "scope": MS_SCOPE})
    if "access_token" not in t:
        raise AuthError((t.get("error_description") or "Session expired.").splitlines()[0])
    prof = minecraft_login(t["access_token"])
    save_account(t.get("refresh_token") or acct["refresh_token"], prof)
    return prof


# ---------------------------------------------------------------- automatic mod install
def pick_version(project, mc, loader):
    vs = api_get("/project/%s/version" % project, {"game_versions": json.dumps([mc]),
                                                    "loaders": json.dumps([loader])})
    return vs[0] if vs else None


def download_mod_file(ver, dest, on_progress=None):
    files = ver["files"]
    f = next((x for x in files if x.get("primary")), files[0])
    os.makedirs(dest, exist_ok=True)
    path = os.path.join(dest, os.path.basename(f["filename"]))
    if os.path.isfile(path):
        return f["filename"]
    req = urllib.request.Request(f["url"], headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r, open(path + ".part", "wb") as out:
        total = int(r.headers.get("Content-Length") or f.get("size") or 0)
        got = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            out.write(chunk)
            got += len(chunk)
            if total and on_progress:
                on_progress(min(got / total, 1.0))
    os.replace(path + ".part", path)
    return f["filename"]


def install_mod(project, mc, loader, dest, seen=None, on_progress=None):
    """Download a mod and, automatically, every REQUIRED dependency it declares."""
    seen = set() if seen is None else seen
    if project in seen:
        return None
    seen.add(project)
    ver = pick_version(project, mc, loader)
    if not ver:
        return None
    name = download_mod_file(ver, dest, on_progress)
    for dep in ver.get("dependencies", []):
        if dep.get("dependency_type") == "required" and dep.get("project_id"):
            try:
                install_mod(dep["project_id"], mc, loader, dest, seen)
            except Exception:
                pass
    return ver, name


def ensure_fabric_api(cfg, progress):
    """Most Fabric mods need Fabric API - install it by itself when mods are present but it is missing."""
    if cfg.get("loader") not in ("fabric", "quilt"):
        return
    d = cfg.get("mods_dir") or default_mods_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return
    if not any(n.lower().endswith((".jar", ".jar.disabled")) for n in names):
        return
    if any(n.lower().startswith("fabric-api") for n in names):
        return
    progress("Installing Fabric API (needed by most mods)\u2026")
    try:
        install_mod("fabric-api", cfg["version"], cfg["loader"], d)
    except Exception:
        pass


# ---------------------------------------------------------------- animation
class Tween:
    """Eases a value toward a target (0..1) and calls render(value) every frame."""

    def __init__(self, widget, render, rate=0.25):
        self.w, self.render, self.rate = widget, render, rate
        self.t = self.target = 0.0
        self.job = None

    def to(self, target):
        self.target = float(target)
        if self.job is None:
            self._step()

    def snap(self, value):
        if self.job is not None:
            try:
                self.w.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None
        self.t = self.target = float(value)
        self._draw()

    def _draw(self):
        try:
            self.render(self.t)
        except tk.TclError:
            pass

    def _step(self):
        d = self.target - self.t
        if abs(d) < 0.004:
            self.t, self.job = self.target, None
            self._draw()
            return
        self.t += d * self.rate
        self._draw()
        try:
            self.job = self.w.after(16, self._step)
        except tk.TclError:
            self.job = None


def hover(widget, tween):
    widget.bind("<Enter>", lambda e: tween.to(1), add="+")
    widget.bind("<Leave>", lambda e: tween.to(0), add="+")


# ---------------------------------------------------------------- widgets
class CButton(tk.Canvas):
    PAD = 3

    def __init__(self, master, text, command, primary=False, danger=False, w=140, h=46, bg=BG, fsize=13):
        self.pad, self.bw, self.bh = px(self.PAD), px(w), px(h)
        super().__init__(master, width=self.bw + 2 * self.pad, height=self.bh + 2 * self.pad, bg=bg,
                         highlightthickness=0, cursor="hand2")
        self.command, self.fsize = command, fsize
        self.base = ACCENT if primary else PANEL2
        self.hot = ACCENT_H if primary else "#2b2b3e"
        fg = "#ffffff" if primary else (DANGER if danger else TEXT)
        self.shape = self.create_polygon(0, 0, 1, 1, 2, 2, smooth=True, fill=self.base, outline="")
        self.label = self.create_text(self.pad + self.bw / 2, self.pad + self.bh / 2, text=text, fill=fg,
                                      font=F(fsize, True))
        self.tw = Tween(self, self.render)
        hover(self, self.tw)
        self.bind("<Button-1>", lambda e: self.command())
        self.render(0)

    def render(self, t):
        g, p = t * px(3), self.pad
        self.coords(self.shape, *rr(p - g, p - g, p + self.bw + g, p + self.bh + g, px(13)))
        self.itemconfig(self.shape, fill=mix(self.base, self.hot, t))
        self.itemconfig(self.label, font=F(self.fsize + 1.2 * t, True))

    def set_text(self, text):
        self.itemconfig(self.label, text=text)


class Pill(tk.Canvas):
    def __init__(self, master, text, command=None, on=False, bg=BG, h=36, fsize=13):
        self.fnt = F(fsize, True)
        self.pad = px(3)
        self.pw, self.ph = measure(self.fnt, text) + px(32), px(h)
        super().__init__(master, width=self.pw + 2 * self.pad, height=self.ph + 2 * self.pad, bg=bg,
                         highlightthickness=0, cursor="hand2")
        self.command = command
        self.shape = self.create_polygon(0, 0, 1, 1, 2, 2, smooth=True, outline="")
        self.label = self.create_text(self.pad + self.pw / 2, self.pad + self.ph / 2, text=text, font=self.fnt)
        self.hv = Tween(self, lambda t: self.draw())
        self.sel = Tween(self, lambda t: self.draw())
        hover(self, self.hv)
        self.bind("<Button-1>", lambda e: self.command and self.command())
        self.sel.snap(1 if on else 0)

    def set_on(self, on):
        self.sel.to(1 if on else 0)

    def draw(self):
        h, s, p, g = self.hv.t, self.sel.t, self.pad, self.hv.t * px(2)
        self.coords(self.shape, *rr(p - g, p - g, p + self.pw + g, p + self.ph + g, self.ph / 2))
        self.itemconfig(self.shape, fill=mix(mix(PANEL2, ACCENT, s), mix("#2b2b3e", ACCENT_H, s), h))
        self.itemconfig(self.label, fill=mix(MUTED, "#ffffff", max(s, h)))


class ChipGroup(tk.Frame):
    def __init__(self, master, options, value, on_change, bg=BG):
        super().__init__(master, bg=bg)
        self.value, self.on_change, self.pills = value, on_change, {}
        for label, val in options:
            p = Pill(self, label, command=lambda v=val: self.choose(v), on=(val == value), bg=bg)
            p.pack(side="left", padx=(0, px(6)))
            self.pills[val] = p

    def set(self, value):
        self.value = value
        for k, p in self.pills.items():
            p.set_on(k == value)

    def choose(self, value):
        self.set(value)
        self.on_change(value)


class RoundEntry(tk.Canvas):
    """Rounded text field with focus glow, placeholder, optional search icon and clear button."""

    def __init__(self, master, placeholder="", icon=False, clearable=False, on_change=None, on_enter=None,
                 width=None, height=46, bg=BG):
        self.pad, self.eh = px(3), px(height)
        super().__init__(master, height=self.eh + 2 * self.pad, bg=bg, highlightthickness=0)
        if width:
            self.config(width=px(width) + 2 * self.pad)
        self.ph, self.ph_on, self.icon, self.clearable = placeholder, False, icon, clearable
        self.on_change, self.on_enter, self.cw, self.over_clear = on_change, on_enter, 0, False
        self.shape = self.create_polygon(0, 0, 1, 1, 2, 2, smooth=True, fill=PANEL2, outline=BORDER,
                                         width=max(1, px(2)))
        self.entry = tk.Entry(self, bg=PANEL2, fg=TEXT, insertbackground=ACCENT_H, relief="flat", bd=0,
                              highlightthickness=0, font=F(14), selectbackground=ACCENT,
                              selectforeground="#ffffff")
        self.win = self.create_window(0, 0, window=self.entry, anchor="w")
        self.fo = Tween(self, lambda t: self.draw())
        self.cl = Tween(self, lambda t: self.draw())
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        self.entry.bind("<KeyRelease>", self._key)
        self.entry.bind("<Return>", lambda e: self.on_enter and self.on_enter())
        self.bind("<Configure>", self._layout)
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_over(False))
        self._show_ph()

    # text API
    def get(self):
        return "" if self.ph_on else self.entry.get().strip()

    def set(self, text):
        self.entry.delete(0, "end")
        self.ph_on = False
        if text:
            self.entry.insert(0, text)
            self.entry.config(fg=TEXT)
        else:
            self._show_ph()
        self.draw()

    def _show_ph(self):
        if not self.ph or self.entry.get():
            return
        self.entry.insert(0, self.ph)
        self.entry.config(fg=MUTED)
        self.ph_on = True

    def _focus_in(self, e):
        if self.ph_on:
            self.entry.delete(0, "end")
            self.entry.config(fg=TEXT)
            self.ph_on = False
        self.fo.to(1)

    def _focus_out(self, e):
        if not self.entry.get():
            self._show_ph()
        self.fo.to(0)

    def _key(self, e):
        if e.keysym == "Return":
            return
        self.draw()
        if self.on_change and not self.ph_on:
            self.on_change(self.get())

    # layout & drawing
    def _layout(self, e):
        self.cw = e.width
        p, h = self.pad, self.eh
        left = px(52) if self.icon else px(18)
        right = px(48) if self.clearable else px(16)
        self.coords(self.shape, *rr(p, p, self.cw - p, p + h, px(15)))
        self.coords(self.win, p + left, p + h / 2)
        self.itemconfig(self.win, width=max(20, self.cw - 2 * p - left - right))
        self.draw()

    def draw(self):
        if not self.cw:
            return
        f, c, p, h = self.fo.t, self.cl.t, self.pad, self.eh
        self.itemconfig(self.shape, outline=mix(BORDER, ACCENT, f))
        self.delete("deco")
        cy = p + h / 2
        if self.icon:
            col, cx, r = mix(MUTED, ACCENT_H, f), p + px(27), px(7)
            self.create_oval(cx - r - px(2), cy - r - px(2), cx + r - px(2), cy + r - px(2), outline=col,
                             width=max(2, px(2)), tags="deco")
            self.create_line(cx + px(3), cy + px(3), cx + px(9), cy + px(9), fill=col, width=max(2, px(2)),
                             capstyle="round", tags="deco")
        if self.clearable and self.get():
            cx, r = self.cw - p - px(26), px(11)
            self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=mix(PANEL2, "#3a3a52", c), outline="",
                             tags="deco")
            k, xc = px(4), mix(MUTED, "#ffffff", c)
            self.create_line(cx - k, cy - k, cx + k, cy + k, fill=xc, width=max(2, px(2)), tags="deco")
            self.create_line(cx - k, cy + k, cx + k, cy - k, fill=xc, width=max(2, px(2)), tags="deco")

    def _hit_clear(self, x, y):
        return (self.clearable and self.get() and abs(x - (self.cw - self.pad - px(26))) < px(14)
                and abs(y - (self.pad + self.eh / 2)) < px(14))

    def _set_over(self, over):
        if over != self.over_clear:
            self.over_clear = over
            self.cl.to(1 if over else 0)
            self.config(cursor="hand2" if over else "")

    def _motion(self, e):
        self._set_over(bool(self._hit_clear(e.x, e.y)))

    def _click(self, e):
        if self._hit_clear(e.x, e.y):
            self.set("")
            if self.on_change:
                self.on_change("")
        self.entry.focus_set()


class ProgressBar(tk.Canvas):
    def __init__(self, master, w=520, h=8):
        self.bw, self.bh = px(w), px(h)
        super().__init__(master, width=self.bw, height=self.bh + px(2), bg=BG, highlightthickness=0)
        self.val = 0.0
        self.draw()

    def set(self, v):
        self.val = max(0.0, min(1.0, v))
        self.draw()

    def draw(self):
        self.delete("all")
        r = self.bh / 2
        self.create_polygon(rr(0, 1, self.bw, 1 + self.bh, r), smooth=True, fill=PANEL2, outline="")
        w = self.bw * self.val
        if w > self.bh:
            self.create_polygon(rr(0, 1, w, 1 + self.bh, r), smooth=True, fill=ACCENT, outline="")


class NavItem(tk.Canvas):
    W, H = 230, 56

    def __init__(self, master, kind, label, command):
        super().__init__(master, width=px(self.W), height=px(self.H), bg=SIDEBAR, highlightthickness=0,
                         cursor="hand2")
        self.kind, self.text_label, self.command = kind, label, command
        self.hv = Tween(self, lambda t: self.draw())
        self.ac = Tween(self, lambda t: self.draw())
        hover(self, self.hv)
        self.bind("<Button-1>", lambda e: self.command())
        self.draw()

    def set_active(self, on):
        self.ac.to(1 if on else 0)

    def draw(self):
        h, a = self.hv.t, self.ac.t
        w, H = px(self.W), px(self.H)
        self.delete("all")
        self.create_polygon(rr(px(12), px(6), w - px(12), H - px(6), px(13)), smooth=True,
                            fill=mix(SIDEBAR, "#1a1535", max(h * 0.7, a)), outline="")
        if a > 0.03:
            self.create_rectangle(0, H / 2 - px(15) * a, px(4), H / 2 + px(15) * a, fill=ACCENT, outline="")
        col = mix(mix(MUTED, "#ffffff", h), ACCENT_H, a)
        draw_icon(self, self.kind, px(40), H / 2, px(9) + px(4) * h, col)
        self.create_text(px(68), H / 2, anchor="w", text=self.text_label, fill=col, font=F(14 + 0.8 * h, True))


class StatCard(tk.Canvas):
    def __init__(self, master, caption, w=200, h=94):
        self.cwid, self.chei = px(w), px(h)
        super().__init__(master, width=self.cwid, height=self.chei, bg=BG, highlightthickness=0)
        self.caption, self.value = caption, "-"
        self.hv = Tween(self, lambda t: self.draw())
        hover(self, self.hv)
        self.draw()

    def set_value(self, v):
        self.value = v
        self.draw()

    def draw(self):
        h, g = self.hv.t, px(1)
        self.delete("all")
        self.create_polygon(rr(g, g, self.cwid - g, self.chei - g, px(17)), smooth=True,
                            fill=mix(PANEL, "#1a1830", h), outline=mix(BORDER, ACCENT, h))
        self.create_text(px(22), px(31), anchor="w", text=self.caption, fill=MUTED, font=F(11, True))
        self.create_text(px(22), px(64), anchor="w", text=self.value, fill=mix(TEXT, ACCENT_H, h),
                         font=F(26 + 3 * h, True))


class ScrollList(tk.Frame):
    """Vertical list with smooth (eased) wheel scrolling and a slim scroll indicator."""

    def __init__(self, master):
        super().__init__(master, bg=BG)
        self.cv = tk.Canvas(self, bg=BG, highlightthickness=0)
        self.cv.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.cv, bg=BG)
        self.win = self.cv.create_window(0, 0, window=self.inner, anchor="nw")
        self.bar = self.cv.create_polygon(0, 0, 1, 1, 2, 2, fill="#2a2a3d", outline="", smooth=True,
                                          state="hidden")
        self.cur = self.target = 0.0
        self.job = None
        self.cv.bind("<Configure>", self._cfg)
        self.inner.bind("<Configure>", self._inner_cfg)

    def total(self):
        return max(self.inner.winfo_reqheight(), 1)

    def maxy(self):
        return max(0, self.total() - self.cv.winfo_height())

    def _cfg(self, e):
        self.cv.itemconfig(self.win, width=max(10, e.width - px(14)))
        self._inner_cfg(None)

    def _inner_cfg(self, e):
        self.cv.configure(scrollregion=(0, 0, self.cv.winfo_width(), self.total()))
        self.target = min(self.target, self.maxy())
        self.cur = min(self.cur, self.maxy())
        self._apply()

    def clear(self):
        for ch in self.inner.winfo_children():
            ch.destroy()
        self.cur = self.target = 0.0
        self.cv.yview_moveto(0)
        self._indicator()

    def set_message(self, text):
        self.clear()
        tk.Label(self.inner, text=text, bg=BG, fg=MUTED, font=F(14), pady=px(50), justify="center").pack()

    def add(self, widget):
        widget.pack(fill="x", pady=(0, px(8)))

    def wheel(self, delta):
        if abs(delta) < 30:
            delta *= 40
        self.target = min(max(self.target - px(delta * 0.8), 0), self.maxy())
        if self.job is None:
            self._tick()

    def _tick(self):
        d = self.target - self.cur
        if abs(d) < 0.6:
            self.cur, self.job = self.target, None
        else:
            self.cur += d * 0.22
            self.job = self.after(16, self._tick)
        self._apply()

    def _apply(self):
        self.cv.yview_moveto(self.cur / self.total())
        self._indicator()

    def _indicator(self):
        vh, tot, w = self.cv.winfo_height(), self.total(), self.cv.winfo_width()
        if tot <= vh + 1:
            self.cv.itemconfig(self.bar, state="hidden")
            return
        y1 = self.cur + (self.cur / tot) * vh
        y2 = y1 + max(px(30), vh * vh / tot)
        self.cv.coords(self.bar, *rr(w - px(7), y1, w - px(3), y2, px(2)))
        self.cv.itemconfig(self.bar, state="normal")


# ---------------------------------------------------------------- cards
class Card(tk.Canvas):
    H = 88

    def __init__(self, master, title):
        super().__init__(master, height=px(self.H), bg=BG, highlightthickness=0)
        self.title_text = title
        self.hue = PALETTE[sum(map(ord, title)) % len(PALETTE)]
        self.cw, self.hits, self.over, self.tws = 0, [], None, {}
        self.hv = Tween(self, lambda t: self.draw())
        self.bind("<Configure>", self._cfg)
        self.bind("<Enter>", lambda e: self.hv.to(1))
        self.bind("<Leave>", self._leave)
        self.bind("<Motion>", self._motion)
        self.bind("<Button-1>", self._click)

    def tw(self, name):
        if name not in self.tws:
            self.tws[name] = Tween(self, lambda t: self.draw())
        return self.tws[name]

    def hv_of(self, name):
        return self.tw(name).t

    def _hit(self, x, y):
        for name, x1, y1, x2, y2 in self.hits:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return name
        return None

    def _motion(self, e):
        hit = self._hit(e.x, e.y)
        if hit != self.over:
            if self.over:
                self.tw(self.over).to(0)
            if hit:
                self.tw(hit).to(1)
            self.over = hit
            self.config(cursor="hand2" if hit else "")

    def _leave(self, e):
        self.hv.to(0)
        if self.over:
            self.tw(self.over).to(0)
            self.over = None
        self.config(cursor="")

    def _click(self, e):
        hit = self._hit(e.x, e.y)
        if hit:
            self.on_hit(hit)

    def on_hit(self, name):
        pass

    def _cfg(self, e):
        self.cw = e.width
        self.draw()

    def tile(self, cx, cy, h, dim=0.0):
        s = px(50) + px(6) * h
        self.create_polygon(rr(cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2, px(14)), smooth=True,
                            fill=mix(self.hue, "#2a2a35", dim * 0.85), outline="")
        self.create_text(cx, cy, text=(self.title_text[:1] or "?").upper(), fill=mix("#ffffff", MUTED, dim),
                         font=F(22 + 3 * h, True))

    def draw(self):
        if self.cw < 60:
            return
        self.delete("all")
        self.hits = []
        h, w, H, g = self.hv.t, self.cw, px(self.H), px(1)
        self.create_polygon(rr(g, g, w - g, H - g, px(17)), smooth=True, fill=mix(PANEL, "#19172e", h),
                            outline=mix(BORDER, ACCENT, h * 0.8))
        self.body(w, H, h)

    def body(self, w, H, h):
        pass


class BrowseCard(Card):
    def __init__(self, master, mod, on_download, done=False):
        super().__init__(master, mod["title"])
        self.mod, self.on_download = mod, on_download
        self.mode, self.prog = ("done" if done else "idle"), 0.0

    def set_state(self, mode, prog=0.0):
        self.mode, self.prog = mode, prog
        self.draw()

    def on_hit(self, name):
        if name == "btn":
            self.on_download(self)

    def body(self, w, H, h):
        self.tile(px(46), H / 2, h)
        bw, bh = px(118), px(40)
        bx2 = w - px(18)
        bx1, by1 = bx2 - bw, H / 2 - bh / 2
        tx = px(92)
        maxw = bx1 - tx - px(18)
        desc = (self.mod.get("description") or "").replace("\n", " ")
        meta = "by %s   \u00b7   \u2193 %s downloads" % (self.mod.get("author", "?"),
                                                        compact(self.mod.get("downloads", 0)))
        self.create_text(tx, px(27), anchor="w", text=fit(self.mod["title"], F(16, True), maxw), fill=TEXT,
                         font=F(16, True))
        self.create_text(tx, px(50), anchor="w", text=fit(desc, F(12), maxw), fill=MUTED, font=F(12))
        self.create_text(tx, px(70), anchor="w", text=fit(meta, F(11), maxw), fill="#6d6d82", font=F(11))

        bt = self.hv_of("btn")
        gr = px(2) * bt if self.mode in ("idle", "fail") else 0
        fill, fg, label = mix(ACCENT, ACCENT_H, bt), "#ffffff", "Download"
        if self.mode == "busy":
            fill, fg, label = PANEL2, TEXT, "%d%%" % int(self.prog * 100)
        elif self.mode == "done":
            fill, fg, label = "#12301f", GREEN, "Installed"
        elif self.mode == "na":
            fill, fg, label = PANEL2, MUTED, "Unavailable"
        elif self.mode == "fail":
            fill, fg, label = mix("#3a1620", "#521c2c", bt), DANGER, "Retry"
        self.create_polygon(rr(bx1 - gr, by1 - gr, bx2 + gr, by1 + bh + gr, px(13)), smooth=True, fill=fill,
                            outline="")
        if self.mode == "busy":
            self.create_rectangle(bx1 + px(12), by1 + bh - px(8), bx1 + px(12) + (bw - px(24)) * self.prog,
                                  by1 + bh - px(5), fill=ACCENT_H, outline="")
        self.create_text((bx1 + bx2) / 2, H / 2 - (px(2) if self.mode == "busy" else 0), text=label, fill=fg,
                         font=F(13 + 0.6 * bt, True))
        if self.mode in ("idle", "fail"):
            self.hits.append(("btn", bx1, by1, bx2, by1 + bh))


class InstalledCard(Card):
    def __init__(self, master, file, size_text, on_toggle, on_delete):
        super().__init__(master, pretty(file))
        self.file, self.size_text = file, size_text
        self.on_toggle, self.on_delete = on_toggle, on_delete
        self.enabled = not file.endswith(".disabled")
        self.en = Tween(self, lambda t: self.draw(), rate=0.22)
        self.en.t = self.en.target = 1.0 if self.enabled else 0.0

    def set_enabled(self, on):
        self.enabled = on
        self.en.to(1.0 if on else 0.0)

    def on_hit(self, name):
        if name == "sw":
            self.on_toggle(self)
        elif name == "del":
            self.on_delete(self)

    def body(self, w, H, h):
        en = self.en.t
        self.tile(px(46), H / 2, h, 1 - en)

        dh = self.hv_of("del")
        dcx, dr = w - px(38), px(16) + px(2) * dh
        self.create_oval(dcx - dr, H / 2 - dr, dcx + dr, H / 2 + dr, fill=mix(PANEL2, "#3d1722", dh),
                         outline="")
        k, xc = px(5), mix(MUTED, DANGER, dh)
        self.create_line(dcx - k, H / 2 - k, dcx + k, H / 2 + k, fill=xc, width=max(2, px(2)))
        self.create_line(dcx - k, H / 2 + k, dcx + k, H / 2 - k, fill=xc, width=max(2, px(2)))
        self.hits.append(("del", dcx - dr, H / 2 - dr, dcx + dr, H / 2 + dr))

        sh = self.hv_of("sw")
        tw_, th = px(50), px(28)
        sx2 = dcx - dr - px(20)
        sx1, sy1 = sx2 - tw_, H / 2 - th / 2
        track = mix(mix("#2c2c3e", "#383850", sh), mix(ACCENT, ACCENT_H, sh), en)
        self.create_polygon(rr(sx1, sy1, sx2, sy1 + th, th / 2), smooth=True, fill=track, outline="")
        kr = px(10.5) + px(1.2) * sh
        kx = sx1 + th / 2 + (tw_ - th) * en
        self.create_oval(kx - kr, H / 2 - kr, kx + kr, H / 2 + kr, fill="#ffffff", outline="")
        self.hits.append(("sw", sx1, sy1, sx2, sy1 + th))
        self.create_text(sx1 - px(14), H / 2, anchor="e", text="Enabled" if self.enabled else "Disabled",
                         fill=mix(MUTED, GREEN, en), font=F(12, True))

        tx = px(92)
        maxw = sx1 - px(110) - tx
        self.create_text(tx, px(34), anchor="w", text=fit(self.title_text, F(16, True), maxw),
                         fill=mix("#6d6d82", TEXT, en), font=F(16, True))
        sub = "%s   \u00b7   %s" % (self.file, self.size_text)
        self.create_text(tx, px(57), anchor="w", text=fit(sub, F(12), maxw), fill=mix("#4f4f63", MUTED, en),
                         font=F(12))


# ---------------------------------------------------------------- app
class App(tk.Tk):
    def __init__(self):
        global FONT, S
        super().__init__()
        S = max(1.0, self.winfo_fpixels("1i") / 96.0)
        families = set(tkfont.families(self))
        for cand in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "Ubuntu", "DejaVu Sans"):
            if cand in families:
                FONT = cand
                break
        self.title("Tape Launcher")
        self.geometry("%dx%d" % (px(1100), px(700)))
        self.minsize(px(920), px(600))
        self.configure(bg=BG)
        ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tape.ico")
        if os.path.exists(ico):
            try:
                self.iconbitmap(ico)
            except tk.TclError:
                pass

        self.cfg = load_config()
        self.status = tk.StringVar(value="Ready.")
        self.page, self.sort, self.req = "home", "downloads", 0
        self._sjob, self._last_q, self._browse_key = None, None, None
        self.done_projects, self.mod_cards, self.fades = set(), [], {}
        self.launching = False
        self.auth, self.prewarming, self.want_play, self.login_state = None, False, False, None
        self._build()
        self.fade = Tween(self, self._apply_fade, rate=0.18)
        self.bind_all("<MouseWheel>", lambda e: self._wheel(e.delta))
        self.bind_all("<Button-4>", lambda e: self._wheel(120))
        self.bind_all("<Button-5>", lambda e: self._wheel(-120))
        self.show("home")
        self.refresh_account_ui()
        self.after(400, self.auto_login)
        self.after(120, lambda: dark_titlebar(self))

    # ----- structure
    def _build(self):
        side = tk.Frame(self, bg=SIDEBAR, width=px(NavItem.W))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        logo = tk.Canvas(side, width=px(NavItem.W), height=px(88), bg=SIDEBAR, highlightthickness=0)
        logo.pack()
        logo.create_polygon(rr(px(24), px(26), px(24) + px(40), px(26) + px(40), px(12)), smooth=True,
                            fill=ACCENT, outline="")
        logo.create_text(px(44), px(46), text="T", fill="#ffffff", font=F(22, True))
        logo.create_text(px(76), px(36), anchor="w", text="TAPE", fill=TEXT, font=F(15, True))
        logo.create_text(px(76), px(56), anchor="w", text="LAUNCHER", fill=MUTED, font=F(11, True))

        self.nav = {}
        for key, label in (("home", "Home"), ("mods", "My mods"), ("browse", "Browse mods"),
                           ("settings", "Settings")):
            n = NavItem(side, key, label, lambda k=key: self.show(k))
            n.pack()
            self.nav[key] = n

        main = tk.Frame(self, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        tk.Label(main, textvariable=self.status, anchor="w", bg=SIDEBAR, fg=MUTED, padx=px(18), pady=px(8),
                 font=F(12)).pack(side="bottom", fill="x")
        box = tk.Frame(main, bg=BG)
        box.pack(fill="both", expand=True)
        box.grid_rowconfigure(0, weight=1)
        box.grid_columnconfigure(0, weight=1)
        self.pages = {}
        for key, builder in (("home", self._build_home), ("mods", self._build_mods),
                             ("browse", self._build_browse), ("settings", self._build_settings)):
            f = tk.Frame(box, bg=BG)
            f.grid(row=0, column=0, sticky="nsew")
            builder(f)
            self.pages[key] = f

    def _header(self, parent, key, title, sub):
        t = tk.Label(parent, text=title, bg=BG, fg=TEXT, font=F(30, True))
        t.pack(anchor="w")
        s = tk.Label(parent, text=sub, bg=BG, fg=MUTED, font=F(13))
        s.pack(anchor="w", pady=(px(2), px(16)))
        self.fades.setdefault(key, []).extend([(t, TEXT), (s, MUTED)])
        return t, s

    def _apply_fade(self, t):
        for lbl, col in self.fades.get(self.page, []):
            lbl.config(fg=mix(BG, col, t))

    def show(self, key):
        self.page = key
        self.pages[key].tkraise()
        for k, n in self.nav.items():
            n.set_active(k == key)
        self.fade.snap(0)
        self.fade.to(1)
        if key == "home":
            self.update_home()
        elif key == "mods":
            self.refresh_installed()
        elif key == "browse":
            self.refresh_pill()
            self.sync()
            if self._browse_key != (self.cfg["version"], self.cfg["loader"]):
                self.search()

    def _wheel(self, delta):
        lst = {"mods": getattr(self, "mods_list", None), "browse": getattr(self, "browse_list", None)}.get(self.page)
        if lst:
            lst.wheel(delta)

    # ----- pages
    def _build_home(self, p):
        wrap = tk.Frame(p, bg=BG)
        wrap.pack(fill="both", expand=True, padx=px(56), pady=px(40))
        t1 = tk.Label(wrap, text="TAPE", bg=BG, fg=TEXT, font=F(80, True))
        t2 = tk.Label(wrap, text="LAUNCHER", bg=BG, fg=ACCENT, font=F(32, True))
        t3 = tk.Label(wrap, text="Download, switch and manage your Minecraft mods in one place.", bg=BG,
                      fg=MUTED, font=F(15))
        t1.pack(anchor="w")
        t2.pack(anchor="w", pady=(0, px(10)))
        t3.pack(anchor="w")
        self.fades["home"] = [(t1, TEXT), (t2, ACCENT), (t3, MUTED)]
        acc = tk.Frame(wrap, bg=BG)
        acc.pack(anchor="w", pady=(px(20), 0))
        self.acc_label = tk.Label(acc, text="", bg=BG, fg=MUTED, font=F(13))
        self.acc_label.pack(side="left")
        self.acc_btn = CButton(acc, "Sign in with Microsoft", self.account_click, w=210, h=40, fsize=12)
        self.acc_btn.pack(side="left", padx=px(14))
        row = tk.Frame(wrap, bg=BG)
        row.pack(anchor="w", pady=(px(14), px(26)))
        self.play_btn = CButton(row, "PLAY", self.launch_game, primary=True, w=150, h=52, fsize=14)
        self.play_btn.pack(side="left")
        CButton(row, "MY MODS", lambda: self.show("mods"), w=140, h=52, fsize=14).pack(side="left", padx=px(10))
        CButton(row, "FIND MODS", lambda: self.show("browse"), w=150, h=52, fsize=14).pack(side="left")
        self.pbar = ProgressBar(wrap)
        self.pbar.pack(anchor="w")
        cards = tk.Frame(wrap, bg=BG)
        cards.pack(side="bottom", anchor="w", fill="x")
        self.stat = {}
        for key, cap in (("version", "MINECRAFT VERSION"), ("loader", "MOD LOADER"), ("mods", "ENABLED MODS")):
            c = StatCard(cards, cap)
            c.pack(side="left", padx=(0, px(14)))
            self.stat[key] = c

    def _build_mods(self, p):
        wrap = tk.Frame(p, bg=BG)
        wrap.pack(fill="both", expand=True, padx=px(40), pady=px(32))
        _, self.mods_sub = self._header(wrap, "mods", "My mods", "")
        row = tk.Frame(wrap, bg=BG)
        row.pack(fill="x")
        CButton(row, "Open folder", self.open_folder, w=120).pack(side="right")
        CButton(row, "Add .jar", self.add_local, w=100).pack(side="right", padx=px(8))
        self.e_filter = RoundEntry(row, placeholder="Filter your mods...", icon=True, clearable=True,
                                   on_change=lambda v: self.refresh_installed())
        self.e_filter.pack(side="left", fill="x", expand=True, padx=(0, px(8)))
        self.mods_list = ScrollList(wrap)
        self.mods_list.pack(fill="both", expand=True, pady=(px(14), 0))

    def _build_browse(self, p):
        wrap = tk.Frame(p, bg=BG)
        wrap.pack(fill="both", expand=True, padx=px(40), pady=px(32))
        self._header(wrap, "browse", "Browse mods", "Search Modrinth. Results match the version and loader from Settings.")
        self.e_search = RoundEntry(wrap, placeholder="Search mods  (sodium, jei, journeymap...)", icon=True,
                                   clearable=True, on_change=lambda v: self.schedule_search(),
                                   on_enter=self.search_now)
        self.e_search.pack(fill="x")
        row = tk.Frame(wrap, bg=BG)
        row.pack(fill="x", pady=(px(10), 0))
        ChipGroup(row, (("Popular", "downloads"), ("Relevance", "relevance"), ("Newest", "newest"),
                        ("Updated", "updated")), self.sort, self.set_sort).pack(side="left")
        self.pill = tk.Label(row, text="", bg=PANEL2, fg=ACCENT_H, font=F(12, True), padx=px(14), pady=px(7),
                             cursor="hand2")
        self.pill.pack(side="right")
        self.pill.bind("<Button-1>", lambda e: self.show("settings"))
        self.browse_list = ScrollList(wrap)
        self.browse_list.pack(fill="both", expand=True, pady=(px(14), 0))

    def _build_settings(self, p):
        wrap = tk.Frame(p, bg=BG)
        wrap.pack(fill="both", expand=True, padx=px(44), pady=px(34))
        self._header(wrap, "settings", "Settings", "Changes are saved automatically.")

        def label(text):
            tk.Label(wrap, text=text, bg=BG, fg=MUTED, font=F(11, True)).pack(anchor="w", pady=(px(16), px(6)))

        label("MODS FOLDER")
        row = tk.Frame(wrap, bg=BG)
        row.pack(fill="x")
        self.e_dir = RoundEntry(row, on_change=lambda v: self.sync())
        self.e_dir.pack(side="left", fill="x", expand=True)
        CButton(row, "Browse", self.browse_dir, w=100).pack(side="left", padx=(px(8), 0))
        self.e_dir.set(self.cfg["mods_dir"])

        cols = tk.Frame(wrap, bg=BG)
        cols.pack(anchor="w", fill="x")

        def col(text, width, value, **kw):
            f = tk.Frame(cols, bg=BG)
            f.pack(side="left", padx=(0, px(14)))
            tk.Label(f, text=text, bg=BG, fg=MUTED, font=F(11, True)).pack(anchor="w", pady=(px(16), px(6)))
            e = RoundEntry(f, width=width, **kw)
            e.pack(anchor="w")
            e.set(value)
            return e

        self.e_ver = col("MINECRAFT VERSION", 180, self.cfg["version"],
                         on_change=lambda v: (self.sync(), self.refresh_pill()))
        self.e_player = col("PLAYER NAME", 240, self.cfg["player"], on_change=lambda v: self.sync())
        self.e_ram = col("RAM (MB)", 140, str(self.cfg["ram"]), on_change=lambda v: self.sync())

        label("MOD LOADER")
        self.loader_group = ChipGroup(wrap, (("Fabric", "fabric"), ("Forge", "forge"), ("NeoForge", "neoforge"),
                                             ("Quilt", "quilt")), self.cfg["loader"],
                                      lambda v: (self.sync(), self.refresh_pill()))
        self.loader_group.pack(anchor="w")
        tk.Label(wrap, text="PLAY works with Fabric and Quilt. Forge / NeoForge are only used for searching mods.",
                 bg=BG, fg="#6d6d82", font=F(11)).pack(anchor="w", pady=(px(6), 0))

        label("JAVA PATH  (optional - leave empty and Java is downloaded automatically)")
        row = tk.Frame(wrap, bg=BG)
        row.pack(fill="x")
        self.e_java = RoundEntry(row, on_change=lambda v: self.sync())
        self.e_java.pack(side="left", fill="x", expand=True)
        CButton(row, "Browse", self.browse_java, w=100).pack(side="left", padx=(px(8), 0))
        self.e_java.set(self.cfg["java"])

        if not MS_CLIENT_ID:  # once the ID is built into the code, users never see this field
            label("MICROSOFT CLIENT ID  (needed once for sign-in - see the help when you click Sign in)")
            self.e_client = RoundEntry(wrap, on_change=lambda v: self.sync())
            self.e_client.pack(fill="x")
            self.e_client.set(self.cfg.get("client_id", ""))

    # ----- helpers
    def set_status(self, text):
        self.after(0, lambda: self.status.set(text))

    def sync(self):
        try:
            ram = max(1024, int(self.e_ram.get() or 4096))
        except ValueError:
            ram = self.cfg.get("ram", 4096)
        self.cfg.update(mods_dir=self.e_dir.get() or default_mods_dir(),
                        version=self.e_ver.get() or "1.20.1", loader=self.loader_group.value,
                        player=self.e_player.get() or "Player", ram=ram, java=self.e_java.get(),
                        client_id=self.e_client.get().strip() if hasattr(self, "e_client") else self.cfg.get("client_id", ""))
        save_config(self.cfg)

    def refresh_pill(self):
        self.sync()
        self.pill.config(text="Minecraft %s  \u00b7  %s" % (self.cfg["version"], self.cfg["loader"].capitalize()))

    def run_bg(self, fn, on_error=None):
        def wrapper():
            try:
                fn()
            except Exception as exc:
                self.after(0, lambda e=exc: self._bg_error(e, on_error))
        threading.Thread(target=wrapper, daemon=True).start()

    def _bg_error(self, exc, cb):
        self.status.set("Error: %s" % exc)
        if cb:
            cb(exc)

    def count_jars(self):
        try:
            return sum(1 for x in os.listdir(self.cfg["mods_dir"]) if x.lower().endswith(".jar"))
        except OSError:
            return 0

    def update_home(self):
        self.sync()
        self.stat["version"].set_value(self.cfg["version"])
        self.stat["loader"].set_value(self.cfg["loader"].capitalize())
        self.stat["mods"].set_value(str(self.count_jars()))
        self.refresh_account_ui()

    # ----- folders / installed mods
    def browse_dir(self):
        d = filedialog.askdirectory(initialdir=self.e_dir.get() or None)
        if d:
            self.e_dir.set(d)
            self.sync()

    def browse_java(self):
        path = filedialog.askopenfilename(title="Select java.exe / javaw.exe")
        if path:
            self.e_java.set(path)
            self.sync()

    def open_folder(self):
        self.sync()
        d = self.cfg["mods_dir"]
        os.makedirs(d, exist_ok=True)
        system = platform.system()
        if system == "Windows":
            os.startfile(d)  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", d])
        else:
            subprocess.Popen(["xdg-open", d])

    def _count_label(self):
        try:
            names = [n for n in os.listdir(self.cfg["mods_dir"]) if n.lower().endswith((".jar", ".jar.disabled"))]
        except OSError:
            names = []
        off = sum(1 for n in names if n.endswith(".disabled"))
        self.mods_sub.config(text="%d mods  \u00b7  %d disabled" % (len(names), off) if names
                             else "Your mods folder is empty.")

    def refresh_installed(self):
        self.sync()
        d = self.cfg["mods_dir"]
        self.mods_list.clear()
        self.mod_cards = []
        self._count_label()
        if not os.path.isdir(d):
            self.mods_list.set_message("Mods folder doesn't exist yet.\nIt will be created with your first download.")
            return
        q = self.e_filter.get().lower()
        names = sorted((n for n in os.listdir(d) if n.lower().endswith((".jar", ".jar.disabled"))), key=str.lower)
        for n in names:
            if q and q not in n.lower() and q not in pretty(n).lower():
                continue
            size = os.path.getsize(os.path.join(d, n)) / 1024
            size_text = "%.1f MB" % (size / 1024) if size >= 1024 else "%d KB" % size
            card = InstalledCard(self.mods_list.inner, n, size_text, self.toggle_mod, self.delete_mod)
            self.mods_list.add(card)
            self.mod_cards.append(card)
        if not self.mod_cards:
            self.mods_list.set_message("No mods match your filter." if q else
                                       "No mods yet.\nFind some in Browse mods.")

    def toggle_mod(self, card):
        d = self.cfg["mods_dir"]
        src = os.path.join(d, card.file)
        dst = src[:-9] if card.file.endswith(".disabled") else src + ".disabled"
        try:
            os.replace(src, dst)
        except OSError as exc:
            self.status.set("Could not rename file: %s" % exc)
            return
        card.file = os.path.basename(dst)
        card.set_enabled(not card.file.endswith(".disabled"))
        self._count_label()

    def delete_mod(self, card):
        if not messagebox.askyesno("Delete mod", "Delete %s?" % card.file):
            return
        try:
            os.remove(os.path.join(self.cfg["mods_dir"], card.file))
        except OSError as exc:
            self.status.set("Could not delete file: %s" % exc)
            return
        card.destroy()
        self.mod_cards.remove(card)
        self._count_label()

    def add_local(self):
        paths = filedialog.askopenfilenames(filetypes=[("Mod files", "*.jar")])
        if not paths:
            return
        d = self.cfg["mods_dir"]
        os.makedirs(d, exist_ok=True)
        for p in paths:
            with open(p, "rb") as src, open(os.path.join(d, os.path.basename(p)), "wb") as dst:
                dst.write(src.read())
        self.refresh_installed()

    # ----- Modrinth search & download
    def set_sort(self, value):
        self.sort = value
        self.search_now()

    def schedule_search(self):
        if self.e_search.get() == self._last_q:
            return
        if self._sjob:
            self.after_cancel(self._sjob)
        self._sjob = self.after(450, self.search)

    def search_now(self):
        if self._sjob:
            self.after_cancel(self._sjob)
            self._sjob = None
        self.search()

    def search(self):
        self._sjob = None
        self.sync()
        q = self.e_search.get()
        version, loader = self.cfg["version"], self.cfg["loader"]
        self._last_q, self._browse_key = q, (version, loader)
        self.req += 1
        rid = self.req
        self.browse_list.set_message("Searching\u2026")
        self.status.set("Searching Modrinth\u2026")

        def work():
            facets = [["project_type:mod"], ["versions:%s" % version], ["categories:%s" % loader]]
            data = api_get("/search", {"query": q, "facets": json.dumps(facets), "limit": 30,
                                       "index": self.sort})
            self.after(0, lambda: self._results(rid, data.get("hits", []), version, loader))

        self.run_bg(work, lambda exc: self._search_failed(rid))

    def _search_failed(self, rid):
        if rid == self.req:
            self._browse_key = None
            self.browse_list.set_message("Couldn't reach Modrinth.\nCheck your internet connection and try again.")

    def _results(self, rid, hits, version, loader):
        if rid != self.req:
            return
        self.browse_list.clear()
        if not hits:
            self.browse_list.set_message("No mods found for Minecraft %s on %s." % (version, loader.capitalize()))
            self.status.set("No results.")
            return
        for h in hits:
            card = BrowseCard(self.browse_list.inner, h, self.download, done=h["project_id"] in self.done_projects)
            self.browse_list.add(card)
        self.status.set("%d result(s) for Minecraft %s / %s." % (len(hits), version, loader))

    def download(self, card):
        if card.mode == "busy":
            return
        self.sync()
        mod = card.mod
        version, loader, dest = self.cfg["version"], self.cfg["loader"], self.cfg["mods_dir"]
        card.set_state("busy", 0.0)
        self.status.set("Downloading %s\u2026" % mod["title"])

        def work():
            res = install_mod(mod["project_id"], version, loader, dest,
                              on_progress=lambda p: self.after(0, lambda q=p: card.set_state("busy", q)))
            if not res:
                self.after(0, lambda: card.set_state("na"))
                self.set_status("No %s build of %s for Minecraft %s." % (loader, mod["title"], version))
                return
            self.done_projects.add(mod["project_id"])
            self.after(0, lambda: card.set_state("done"))
            self.set_status("Downloaded %s (required dependencies were installed automatically)." % res[1])

        self.run_bg(work, lambda exc: card.set_state("fail"))

    # ----- account
    def refresh_account_ui(self):
        if not hasattr(self, "acc_label"):
            return
        if self.auth:
            self.acc_label.config(text="Signed in as %s" % self.auth["name"], fg=GREEN)
            self.acc_btn.set_text("Sign out")
            return
        acct = load_account()
        if acct:
            self.acc_label.config(text="%s - not connected (offline)" % acct.get("name", "Account"), fg=MUTED)
        else:
            self.acc_label.config(text="Offline mode - playing as %s" % (self.cfg.get("player") or "Player"),
                                  fg=MUTED)
        self.acc_btn.set_text("Sign in with Microsoft")

    def account_click(self):
        if self.auth:
            if messagebox.askyesno("Sign out", "Sign out of %s?" % self.auth["name"]):
                clear_account()
                self.auth = None
                self.refresh_account_ui()
                self.status.set("Signed out.")
        else:
            self.start_login()

    def auto_login(self):
        """On start: silent login with the saved account, then get the game ready in the background."""
        if not load_account() or not client_id(self.cfg):
            return
        self.status.set("Signing in\u2026")

        def work():
            try:
                auth = sign_in_silent(self.cfg)
            except AuthError as exc:
                self.after(0, lambda e=exc: self.status.set("Session expired - click Sign in. (%s)" % e))
                return
            except Exception:
                self.after(0, lambda: self.status.set("No connection - could not sign in."))
                return
            self.after(0, lambda: self._signed_in(auth))

        threading.Thread(target=work, daemon=True).start()

    def _signed_in(self, auth):
        self.auth = auth
        self.refresh_account_ui()
        self.status.set("Signed in as %s." % auth["name"])
        self.prewarm()

    def start_login(self):
        if self.login_state:
            return
        self.sync()
        cid = client_id(self.cfg)
        if not cid:
            messagebox.showinfo("Microsoft sign-in", SETUP_HELP)
            self.show("settings")
            return
        st = self.login_state = {"cancel": False, "dlg": None}
        self.status.set("Contacting Microsoft\u2026")

        def work():
            try:
                try:
                    tok = ms_login_loopback(cid, lambda u: self.after(0, lambda: self._login_dialog(st, {"url": u})),
                                            lambda: st["cancel"])
                except OSError:  # local server impossible -> classic code login
                    d = ms_device_start(cid)
                    self.after(0, lambda: self._login_dialog(st, d))
                    tok = ms_device_wait(cid, d, lambda: st["cancel"])
                self.after(0, lambda: self.status.set("Signing in to Minecraft\u2026"))
                prof = minecraft_login(tok["access_token"])
                save_account(tok.get("refresh_token", ""), prof)
            except Exception as exc:
                self.after(0, lambda e=exc: self._login_end(st, None, e))
                return
            self.after(0, lambda: self._login_end(st, prof, None))

        threading.Thread(target=work, daemon=True).start()

    def _login_dialog(self, st, d):
        if st["cancel"]:
            return
        dlg = st["dlg"] = tk.Toplevel(self)
        dlg.title("Sign in with Microsoft")
        dlg.configure(bg=PANEL)
        dlg.resizable(False, False)
        dlg.transient(self)

        def cancel():
            st["cancel"] = True
            if self.login_state is st:
                self.login_state = None
            dlg.destroy()
            self.status.set("Sign-in cancelled.")

        if "user_code" in d:
            tk.Label(dlg, text="Your browser opened. Enter this code there:", bg=PANEL, fg=TEXT,
                     font=F(14)).pack(padx=px(40), pady=(px(28), px(6)))
            tk.Label(dlg, text=d["user_code"], bg=PANEL, fg=ACCENT_H, font=F(46, True)).pack(padx=px(40))
            tk.Label(dlg, text=d.get("verification_uri", "https://www.microsoft.com/link"), bg=PANEL, fg=MUTED,
                     font=F(12)).pack(pady=(px(4), px(14)))
            tk.Label(dlg, text="The code is already copied. This window closes by itself when you finish.",
                     bg=PANEL, fg=MUTED, font=F(11)).pack(padx=px(24))
            link = d.get("verification_uri_complete") or d["verification_uri"]
        else:
            tk.Label(dlg, text="Sign in in the browser window that just opened", bg=PANEL, fg=TEXT,
                     font=F(15, True)).pack(padx=px(40), pady=(px(30), px(6)))
            tk.Label(dlg, text="When you are done, this window closes by itself and the launcher does the rest.",
                     bg=PANEL, fg=MUTED, font=F(12)).pack(padx=px(30))
            link = d["url"]
        CButton(dlg, "Cancel", cancel, w=120, h=40, bg=PANEL, fsize=12).pack(pady=px(20))
        dlg.protocol("WM_DELETE_WINDOW", cancel)
        dlg.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_y() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry("+%d+%d" % (max(0, x), max(0, y)))
        dark_titlebar(dlg)
        if "user_code" in d:
            try:
                self.clipboard_clear()
                self.clipboard_append(d["user_code"])
            except tk.TclError:
                pass
        try:
            webbrowser.open(link)
        except Exception:
            pass
        self.status.set("Waiting for you to sign in in the browser\u2026")

    def _login_end(self, st, prof, exc):
        dlg = st.get("dlg")
        if dlg:
            try:
                dlg.destroy()
            except tk.TclError:
                pass
        if self.login_state is st:
            self.login_state = None
        if st["cancel"]:
            return
        if exc:
            self.status.set("Sign-in failed: %s" % str(exc).splitlines()[0])
            messagebox.showerror("Tape Launcher", "Sign-in failed:\n\n%s" % exc)
            return
        self._signed_in(prof)

    # ----- launch
    def launch_cfg(self):
        cfg = dict(self.cfg)
        if self.auth:
            cfg["auth"] = self.auth
        return cfg

    def prewarm(self):
        """Download Java, the game, the mod loader and assets in the background, so PLAY is instant."""
        if self.launching or self.prewarming:
            return
        self.sync()
        self.prewarming = True
        cfg = self.launch_cfg()
        self.play_btn.set_text("PREPARING\u2026")

        def progress(text, frac=None):
            def ui():
                self.status.set(text)
                if frac is not None:
                    self.pbar.set(frac)
            self.after(0, ui)

        def work():
            err = None
            try:
                ensure_fabric_api(cfg, progress)
                prepare_game(cfg, progress)
            except Exception as exc:
                err = exc
            self.after(0, lambda: self._prewarm_done(err))

        threading.Thread(target=work, daemon=True).start()

    def _prewarm_done(self, err):
        self.prewarming = False
        self.pbar.set(0)
        self.play_btn.set_text("PLAY")
        self.update_home()
        if err:
            self.want_play = False
            self.status.set("Could not prepare the game: %s" % str(err).splitlines()[0])
        elif self.want_play:
            self.want_play = False
            self.launch_game()
        else:
            self.status.set("Everything is ready - press PLAY.")

    def _refresh_auth(self, cfg, progress):
        if not load_account():
            cfg.pop("auth", None)
            return
        progress("Signing in\u2026")
        try:
            cfg["auth"] = sign_in_silent(cfg)
            self.auth = cfg["auth"]
        except AuthError as exc:
            raise LaunchError("Microsoft sign-in failed: %s\nClick Sign in again (or use Sign out to play offline)."
                              % exc)
        except Exception:  # no internet: still allow singleplayer
            cfg.pop("auth", None)
            progress("Offline - starting without online login\u2026")

    def launch_game(self):
        if self.launching:
            return
        if self.prewarming:  # clicked PLAY while files are still downloading: start as soon as they are ready
            self.want_play = True
            self.status.set("Almost there - Minecraft starts by itself when the files are ready.")
            return
        self.sync()
        cfg = self.launch_cfg()
        self.launching = True
        self.play_btn.set_text("WORKING\u2026")
        self.pbar.set(0)
        self.status.set("Preparing Minecraft %s\u2026 (the first launch downloads the game files)" % cfg["version"])

        def progress(text, frac=None):
            def ui():
                self.status.set(text)
                if frac is not None:
                    self.pbar.set(frac)
            self.after(0, ui)

        def work():
            try:
                self._refresh_auth(cfg, progress)
                ensure_fabric_api(cfg, progress)
                proc = run_game(cfg, progress)
            except Exception as exc:
                self.after(0, lambda e=exc: self._launch_failed(e))
                return
            self.after(0, self._launch_started)
            code = proc.wait()
            self.after(0, lambda: self._game_exited(code))

        threading.Thread(target=work, daemon=True).start()

    def _launch_started(self):
        self.pbar.set(1.0)
        self.play_btn.set_text("PLAYING")
        self.status.set("Minecraft is starting\u2026 the game window appears in a few seconds.")

    def _launch_failed(self, exc):
        self.launching = False
        self.play_btn.set_text("PLAY")
        self.pbar.set(0)
        self.status.set("Could not start Minecraft: %s" % str(exc).splitlines()[0])
        messagebox.showerror("Tape Launcher", "Could not start Minecraft:\n\n%s" % exc)

    def _game_exited(self, code):
        self.launching = False
        self.play_btn.set_text("PLAY")
        self.pbar.set(0)
        self.update_home()
        if code == 0:
            self.status.set("Minecraft closed.")
            return
        self.status.set("Minecraft closed with an error (code %s)." % code)
        tail = ""
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                tail = "".join(f.readlines()[-14:])[-1500:]
        except OSError:
            pass
        messagebox.showerror("Tape Launcher", "Minecraft crashed or closed with an error (code %s).\n\n"
                             "Last log lines:\n%s\n\nFull log: %s" % (code, tail, LOG_FILE))

if __name__ == "__main__":
    if sys.version_info < (3, 8):
        sys.exit("Python 3.8+ required")
    App().mainloop()