#!/usr/bin/env python3
"""gpkg -- package, sign and verify a Ghost Proxifier plugin.

The developer side of docs/plugin-sdk/spec-release.md §5:

    python tools/plugin/gpkg.py keygen --out %USERPROFILE%\\.ghost-plugin-keys\\dev
    python tools/plugin/gpkg.py pack   --src ./plugin --out dist/ [--min-app-version 1.2.0]
    python tools/plugin/gpkg.py sign   --key %USERPROFILE%\\.ghost-plugin-keys\\dev\\dev-private.pem --dist dist/
    python tools/plugin/gpkg.py verify --dist dist/ --pubkey %USERPROFILE%\\.ghost-plugin-keys\\dev\\dev-public.b64

and the registry maintainer's two pre-publish checks (spec-release.md §2),
which need only the PUBLIC root key -- nothing here ever holds the private one:

    python tools/plugin/gpkg.py check-registry --file registry.json
    python tools/plugin/gpkg.py verify-sig --pubkey root-public.b64 --file registry.json

`pack` and `check-registry` need only the standard library. `keygen`, `sign`,
`verify` and `verify-sig` need Python `cryptography` (pip install
cryptography); without it they print that and exit 2.

THIS IS A CONVENIENCE, NOT THE GATE. Every rule here is a Python mirror of the
C++ one that the host actually enforces -- the manifest rules of
spec-manifest.md (src/modules/plugin/domain/plugin_docs.h), the registry
rules of the same header's ParseRegistry, the id/version
syntax of src/shared/policy_plugin_id.h, the entry-name rule of
src/shared/policy_zip_name.h and the archive layout of src/platform/zip_read.h
-- so a developer finds out on their own machine instead of from a user's
install failing. The two must change together; when they disagree, the C++
side is right.

Exit codes: 0 ok, 1 a check failed ("error: <spec-errors code>: <why>"),
2 usage error or `cryptography` missing.
"""

import argparse
import base64
import datetime
import hashlib
import io
import json
import math
import os
import re
import stat
import struct
import sys
import zipfile
import zlib

# ---- limits: docs/plugin-sdk/spec-limits.md (src/shared/contract_plugin_limits.h) ----
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 4096
MAX_ICON_BYTES = 256 * 1024
MAX_NAME_CHARS = 64
MAX_DESC_CHARS = 240
MAX_AUTHOR_CHARS = 64
MAX_ARGS = 16
MAX_ARG_BYTES = 256
MAX_ZIP_NAME_BYTES = 240
MAX_ID_BYTES = 64
MAX_REGISTRY_BYTES = 1024 * 1024        # kMaxRegistryBytes: the most a client downloads
MAX_SIG_BYTES = 256                     # kMaxSigBytes
MAX_REVOKED_REASON_CHARS = 200
MAX_TRIAL_DAYS = 90
MAX_SEQ = 2 ** 63 - 1                   # LLONG_MAX

# spec-plugin-api.md §2 -- the v1 permission names.
PERMISSIONS = frozenset({
    "events.read.control", "events.read.data", "stats.read",
    "config.read", "log.write", "plugin.assets",
})
CATEGORIES = frozenset({"network", "dev", "security", "productivity", "other"})
ENTRY_EXTENSIONS = (".exe", ".cmd", ".bat", ".py", ".js", ".mjs")
ICON_EXTENSIONS = (".png", ".webp")
RUNTIME_KINDS = frozenset({"none", "python", "node"})
MAX_NOTES_CHARS = 2000
VCS_DIRS = frozenset({".git", ".hg", ".svn"})
# Skipped at the package ROOT only: a repository's CI configuration
# (.github/workflows/release.yml, its hashed requirements) is not part of the
# plugin, and shipping it to every user who installs the plugin says nothing
# they need. A .github deeper down is the plugin's own business.
ROOT_SKIP_DIRS = frozenset({".github"})
SECRET_EXTENSIONS = (".pem", ".key")

RELEASE_FILE = "ghost-plugin.json"
REGISTRY_FILE = "registry.json"
SIG_FILE = RELEASE_FILE + ".sig"
DEFAULT_MIN_APP_VERSION = "1.2.0"


class GpkgError(Exception):
    """A refused input. `code` is the docs/plugin-sdk/spec-errors.md code."""

    def __init__(self, code, message):
        super().__init__("%s: %s" % (code, message))
        self.code = code
        self.message = message


class UsageError(GpkgError):
    """The command line was wrong (a bad --pubkey, --out inside --src): exit 2."""


# ---- ids, versions, names (policy_plugin_id.h, policy_zip_name.h) ------------------

_ID_SEGMENT = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?\Z")
_DEVICE_ID = re.compile(r"(con|prn|aux|nul|com[0-9]|lpt[0-9])\Z")


def is_plugin_id(s):
    """policy_plugin_id.h IsPluginId: >= 3 lower-case segments, <= 64 bytes,
    the first segment not a Windows device name."""
    if not isinstance(s, str) or not s or len(s.encode("utf-8")) > MAX_ID_BYTES:
        return False
    segs = s.split(".")
    if len(segs) < 3 or not all(_ID_SEGMENT.match(g) for g in segs):
        return False
    return not _DEVICE_ID.match(segs[0])


def is_plugin_version(s):
    """policy_plugin_id.h IsPluginVersion: three canonical parts (no leading
    zero except a lone 0), each <= 65535."""
    if not isinstance(s, str):
        return False
    parts = s.split(".")
    if len(parts) != 3:
        return False
    for p in parts:
        if not re.fullmatch(r"0|[1-9][0-9]{0,4}", p) or int(p) > 65535:
            return False
    return True


def version_key(s):
    return tuple(int(p) for p in s.split("."))


def _ascii_lower(s):
    """A-Z to a-z and nothing else -- the C++ side's Lower(). str.lower() also
    folds non-ASCII (U+212A KELVIN SIGN -> "k", U+0130 -> "i" + U+0307), which
    would make a name the host accepts a name this tool refuses."""
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in s)


def _is_device_segment(seg):
    stem = _ascii_lower(seg.split(".", 1)[0].rstrip(" "))
    if stem in ("con", "prn", "aux", "nul", "conin$", "conout$", "clock$"):
        return True
    if len(stem) == 4 and stem[:3] in ("com", "lpt") and stem[3].isascii() and stem[3].isdigit():
        return True
    return len(stem) == 4 and stem[:3] in ("com", "lpt") and stem[3] in "¹²³"


def is_safe_zip_entry_name(name):
    """policy_zip_name.h IsSafeZipEntryName, on a str (the UTF-8 bytes are what
    is limited to 240)."""
    if not isinstance(name, str) or not name:
        return False
    try:
        raw = name.encode("utf-8")          # a lone surrogate is not well-formed UTF-8
    except UnicodeEncodeError:
        return False
    if len(raw) > MAX_ZIP_NAME_BYTES:
        return False
    for ch in name:
        if ord(ch) < 0x20 or ord(ch) == 0x7F or ch in '\\:<>"|?*':
            return False
    body = name[:-1] if name.endswith("/") else name
    for seg in body.split("/"):
        if not seg or seg[-1] in ". ":
            return False
        if _is_device_segment(seg):
            return False
        if re.search(r"~[0-9]", seg):          # an 8.3 short-name alias
            return False
    return True


def is_package_name(s):
    """spec-limits.md §5, and no ".." anywhere (plugin_docs.h IsPackageName)."""
    return (isinstance(s, str) and ".." not in s
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.gpkg", s) is not None)


def package_name(plugin_id, version):
    return "%s-%s.gpkg" % (plugin_id, version)


def _is_https_url(s):
    return (isinstance(s, str) and s.startswith("https://") and len(s) > len("https://")
            and not any(ord(c) <= 0x20 or ord(c) == 0x7F for c in s))


# ---- manifest.json (spec-manifest.md) ------------------------------------------------

def _refuse_constant(name):
    raise ValueError("%s is not JSON" % name)


def _has_lone_surrogate(v):
    """True when any string in the document -- key or value, at any depth --
    holds a lone surrogate. json.loads accepts "\\ud800" and hands back a str
    that is not Unicode text; nlohmann (the C++ side) refuses the document.
    Iterative, with an explicit stack: nesting depth must not be able to raise
    RecursionError here."""
    stack = [v]
    while stack:
        x = stack.pop()
        if isinstance(x, str):
            try:
                x.encode("utf-8")
            except UnicodeEncodeError:
                return True
        elif isinstance(x, dict):
            stack.extend(x.keys())
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return False


# Three places where json.loads is laxer than nlohmann, found by running the
# same inputs through both (PR ⑥). Each would let this tool pass a document
# every client refuses, so each is closed here:
#   * a number whose value overflows a double -- 1e400, or an integer of 400
#     digits -- is "number overflow" to nlohmann (out_of_range.406) and inf or
#     a big int to Python. An integer that merely overflows 64 bits is NOT
#     refused by nlohmann (it becomes a finite double) and is not here.
#   * a duplicate key: both keep the LAST value, but nlohmann refuses a lone
#     surrogate in the value it is about to drop, which never reaches the
#     scan over the finished dict.
# Two places where this tool stays STRICTER than nlohmann, on purpose: a
# nesting depth past Python's recursion limit (nlohmann has none), and a NUL
# byte after the top-level value (nlohmann stops reading at it and ignores
# whatever follows; nothing a real writer produces).

def _refuse_overflow(text):
    if math.isinf(float(text)):
        raise ValueError("number overflow parsing %s" % text[:24])


def _parse_float(text):
    _refuse_overflow(text)
    return float(text)


def _parse_int(text):
    _refuse_overflow(text)
    return int(text)


def _object_from_pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out and _has_lone_surrogate(out[k]):
            raise ValueError("a string holds a lone surrogate (U+D800..U+DFFF)")
        out[k] = v
    return out


def _load_json_object(data, code):
    """Strict JSON: UTF-8 without a BOM, no NaN/Infinity, no number that
    overflows a double, no lone surrogates, an object at the top."""
    if not isinstance(data, (bytes, bytearray)):
        raise GpkgError(code, "expected bytes")
    # RecursionError: json.loads recurses once per nesting level, so a deep
    # enough document (in an unknown field, say) exceeds the interpreter's
    # limit. That is a refusal of the document, never a traceback. The
    # surrogate scan runs inside the same handler.
    try:
        obj = json.loads(bytes(data).decode("utf-8"), parse_constant=_refuse_constant,
                         parse_float=_parse_float, parse_int=_parse_int, object_pairs_hook=_object_from_pairs)
        lone = isinstance(obj, dict) and _has_lone_surrogate(obj)
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise GpkgError(code, "not strict UTF-8 JSON: %s" % e)
    if not isinstance(obj, dict):
        raise GpkgError(code, "not a JSON object")
    if lone:
        raise GpkgError(code, "a string holds a lone surrogate (U+D800..U+DFFF)")
    return obj


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _text_pair(obj, key, lo, hi, required):
    """{zh, en}: `en` required; each value a string of lo..hi characters."""
    if key not in obj:
        if required:
            raise GpkgError("manifest_malformed", "'%s' is required" % key)
        return
    v = obj[key]
    if not isinstance(v, dict) or "en" not in v:
        raise GpkgError("manifest_malformed", "'%s' must be an object with at least 'en'" % key)
    for lang in ("zh", "en"):
        if lang in v:
            t = v[lang]
            if not isinstance(t, str) or not (lo <= len(t) <= hi):
                raise GpkgError("manifest_malformed", "'%s.%s' must be a string of %d-%d characters" % (key, lang, lo, hi))


def validate_manifest(data):
    """Parses and checks manifest.json bytes; returns the dict or raises
    GpkgError(manifest_malformed | unknown_permission). Unknown fields are
    ignored; a KNOWN field of the wrong type refuses the whole manifest."""
    m = _load_json_object(data, "manifest_malformed")
    bad = lambda why: GpkgError("manifest_malformed", why)

    if not _is_int(m.get("v")) or m["v"] != 1:
        raise bad("'v' must be the integer 1")
    if not is_plugin_id(m.get("id")):
        raise bad("'id' must be a reverse-domain id of >= 3 lower-case segments (spec-limits §4)")
    if not is_plugin_version(m.get("version")):
        raise bad("'version' must be three canonical numbers, e.g. 1.0.0")
    _text_pair(m, "name", 1, MAX_NAME_CHARS, True)
    _text_pair(m, "description", 0, MAX_DESC_CHARS, True)

    author = m.get("author")
    if not isinstance(author, dict) or not isinstance(author.get("name"), str) or len(author["name"]) > MAX_AUTHOR_CHARS:
        raise bad("'author' must be an object with 'name' (a string of <= 64 characters)")
    if "url" in author and not _is_https_url(author["url"]):
        raise bad("'author.url' must be an https URL")
    if "homepage" in m and not _is_https_url(m["homepage"]):
        raise bad("'homepage' must be an https URL")
    if not isinstance(m.get("category"), str):
        raise bad("'category' is required and must be a string (unknown values read as 'other')")

    # icon: the extension rule belongs to the manifest. Whether the file is in
    # the package and within 256 KB is checked by `pack` (this tool is strict);
    # the HOST does not refuse an install over it -- it falls back to the
    # default icon.
    if "icon" in m:
        icon = m["icon"]
        if not is_safe_zip_entry_name(icon) or icon.endswith("/") or not _ascii_lower(icon).endswith(ICON_EXTENSIONS):
            raise bad("'icon' must be a package-relative .png or .webp path")
    entry = m.get("entry")
    if not is_safe_zip_entry_name(entry) or entry.endswith("/"):
        raise bad("'entry' must be a package-relative file path ('/' only, no '..', no drive)")
    # _ascii_lower here (and for icon) is consistency, not a behaviour change:
    # the only non-ASCII code points str.lower() folds to ASCII letters are
    # U+0130 ("i" + U+0307, never at the end) and U+212A ("k"), and no
    # extension contains an i or a k.
    if not _ascii_lower(entry).endswith(ENTRY_EXTENSIONS):
        raise bad("'entry' must end in one of %s" % ", ".join(ENTRY_EXTENSIONS))

    if "args" in m:
        args = m["args"]
        if not isinstance(args, list) or len(args) > MAX_ARGS:
            raise bad("'args' must be a list of at most %d strings" % MAX_ARGS)
        for a in args:
            if not isinstance(a, str) or len(a.encode("utf-8")) > MAX_ARG_BYTES:
                raise bad("each of 'args' must be a string of at most %d bytes" % MAX_ARG_BYTES)
    if "runtime" in m:
        rt = m["runtime"]
        if not isinstance(rt, dict):
            raise bad("'runtime' must be an object")
        if "kind" in rt and (not isinstance(rt["kind"], str) or rt["kind"] not in RUNTIME_KINDS):
            raise bad("'runtime.kind' must be one of none, python, node")
        if "minVersion" in rt and not (isinstance(rt["minVersion"], str) and
                                       re.fullmatch(r"[0-9]{1,5}(\.[0-9]{1,5}){0,2}", rt["minVersion"])):
            raise bad("'runtime.minVersion' must be 1-3 dot-separated numbers, e.g. 3.9")
    if "ui" in m:
        ui = m["ui"]
        if not isinstance(ui, dict) or ("embedded" in ui and not isinstance(ui["embedded"], bool)):
            raise bad("'ui' must be an object whose 'embedded' is a boolean")
    if "standalone" in m and not isinstance(m["standalone"], bool):
        raise bad("'standalone' must be a boolean")
    if "permissions" in m:
        perms = m["permissions"]
        if not isinstance(perms, list) or not all(isinstance(p, str) for p in perms):
            raise bad("'permissions' must be a list of strings")
        unknown = [p for p in perms if p not in PERMISSIONS]
        if unknown:
            raise GpkgError("unknown_permission", "not a v1 permission: %s" % ", ".join(unknown))
    return m


# ---- ghost-plugin.json (spec-release.md §3) ------------------------------------------

def validate_release(data):
    r = _load_json_object(data, "release_malformed")
    bad = lambda why: GpkgError("release_malformed", why)
    if not _is_int(r.get("v")) or r["v"] != 1:
        raise bad("'v' must be the integer 1")
    if not is_plugin_id(r.get("id")):
        raise bad("'id' is not a plugin id")
    if not is_plugin_version(r.get("version")):
        raise bad("'version' is not a canonical version")
    if not is_plugin_version(r.get("minAppVersion")):
        raise bad("'minAppVersion' is not a canonical version")
    pkg = r.get("package")
    if not isinstance(pkg, dict):
        raise bad("'package' must be an object")
    if not is_package_name(pkg.get("name")):
        raise bad("'package.name' must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\\.gpkg$")
    if not _is_int(pkg.get("size")) or pkg["size"] < 0:
        raise bad("'package.size' must be a non-negative integer")
    if not isinstance(pkg.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", pkg["sha256"]):
        raise bad("'package.sha256' must be 64 lower-case hex digits")
    if "releasedAt" in r and not isinstance(r["releasedAt"], str):
        raise bad("'releasedAt' must be a string")
    # notes: {zh, en} as spec-release section 3.2 and spec-limits section 5
    # define it; each value a string of at most 2000 code points, both optional.
    if "notes" in r:
        notes = r["notes"]
        if not isinstance(notes, dict):
            raise bad("'notes' must be an object {zh, en}")
        for lang in ("zh", "en"):
            if lang in notes and (not isinstance(notes[lang], str) or len(notes[lang]) > MAX_NOTES_CHARS):
                raise bad("'notes.%s' must be a string of at most %d characters" % (lang, MAX_NOTES_CHARS))
    return r


def build_release(manifest, package_bytes, min_app_version, released_at=None):
    """The ghost-plugin.json bytes for a package. Signed as they are: nothing
    re-serialises them later (spec-release §1, no canonicalisation)."""
    if released_at is None:
        released_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {
        "v": 1,
        "id": manifest["id"],
        "version": manifest["version"],
        "minAppVersion": min_app_version,
        "releasedAt": released_at,
        "package": {
            "name": package_name(manifest["id"], manifest["version"]),
            "size": len(package_bytes),
            "sha256": hashlib.sha256(package_bytes).hexdigest(),
        },
    }
    out = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    validate_release(out)                   # what we write is held to what we read
    return out


# ---- registry.json (spec-release.md §2.2; plugin_docs.h ParseRegistry) ---------------
#
# Only the maintainer writes it, so this is a pre-publish check rather than a
# developer's convenience -- but the rule is the one of this whole file: a
# line-by-line mirror of the C++ (ParseRegistry, IsRepo, DecodeKeyOrSig64,
# IsPluginId, IsPluginVersion), and where they disagree the C++ is right.
# Strict where a lax reading would change a trust decision (a duplicate id:
# which devKey?), lax where it would not (an empty `versions`) -- a registry
# the client refuses is one whose NEW revocations never land.

_REPO_PART = re.compile(r"[A-Za-z0-9._-]+\Z")


def is_repo(s):
    """plugin_docs.h IsRepo: owner/name, 1-39 and 1-100 of [A-Za-z0-9._-],
    neither half starting with '.', no ".." anywhere."""
    if not isinstance(s, str) or ".." in s or "/" not in s:
        return False
    owner, name = s.split("/", 1)
    return all(1 <= len(p) <= n and p[0] != "." and _REPO_PART.match(p) is not None
               for p, n in ((owner, 39), (name, 100)))


def validate_registry(data):
    """Parses and checks registry.json bytes; returns the dict or raises
    GpkgError(registry_malformed). Unknown fields are ignored; a KNOWN field of
    the wrong type -- null included -- refuses the whole document."""
    r = _load_json_object(data, "registry_malformed")
    bad = lambda why: GpkgError("registry_malformed", why)

    if not _is_int(r.get("v")) or r["v"] != 1:
        raise bad("'v' must be the integer 1")
    if not _is_int(r.get("seq")) or not (0 <= r["seq"] <= MAX_SEQ):
        raise bad("'seq' must be an integer from 0 to %d" % MAX_SEQ)
    if "updatedAt" in r and not isinstance(r["updatedAt"], str):
        raise bad("'updatedAt' must be a string")

    plugins = r.get("plugins")
    if not isinstance(plugins, list):
        raise bad("'plugins' must be an array")
    seen = set()
    for i, p in enumerate(plugins):
        where = "plugins[%d]" % i
        if not isinstance(p, dict):
            raise bad("%s must be an object" % where)
        pid = p.get("id")
        if not is_plugin_id(pid):
            raise bad("%s.id is not a plugin id (spec-limits §4)" % where)
        if pid in seen:
            raise bad("%s: id %s appears twice (which devKey would a client use?)" % (where, pid))
        seen.add(pid)
        if not is_repo(p.get("repo")):
            raise bad("%s.repo must be owner/name (spec-limits §5)" % where)
        if not isinstance(p.get("devKey"), str):
            raise bad("%s.devKey must be a string" % where)
        decode_b64_64(p["devKey"], "%s.devKey" % where, code="registry_malformed")
        if not isinstance(p.get("status"), str) or p["status"] not in ("listed", "delisted"):
            raise bad("%s.status must be listed or delisted" % where)
        if "paid" in p:
            paid = p["paid"]
            if paid is True:
                raise bad('%s.paid is true, which says nothing about the trial; write {"trialDays": 1..%d}'
                          % (where, MAX_TRIAL_DAYS))
            if paid is not False:
                if not isinstance(paid, dict):
                    raise bad('%s.paid must be false or {"trialDays": 1..%d}' % (where, MAX_TRIAL_DAYS))
                d = paid.get("trialDays")
                if not _is_int(d) or not (1 <= d <= MAX_TRIAL_DAYS):
                    raise bad("%s.paid.trialDays must be an integer from 1 to %d" % (where, MAX_TRIAL_DAYS))
        if "minVersion" in p and not is_plugin_version(p["minVersion"]):
            raise bad("%s.minVersion must be three canonical numbers" % where)

    if "revoked" in r:
        revoked = r["revoked"]
        if not isinstance(revoked, list):
            raise bad("'revoked' must be an array")
        for i, x in enumerate(revoked):
            where = "revoked[%d]" % i
            if not isinstance(x, dict):
                raise bad("%s must be an object" % where)
            if not is_plugin_id(x.get("id")):
                raise bad("%s.id is not a plugin id" % where)
            vs = x.get("versions")
            if not isinstance(vs, list):
                raise bad("%s.versions must be an array" % where)
            for v in vs:
                if v != "*" and not is_plugin_version(v):
                    raise bad('%s.versions: %r is neither "*" nor a canonical version' % (where, v))
            if "reason" in x and not (isinstance(x["reason"], str) and len(x["reason"]) <= MAX_REVOKED_REASON_CHARS):
                raise bad("%s.reason must be a string of at most %d characters" % (where, MAX_REVOKED_REASON_CHARS))
    return r


def check_registry(path, strict=False):
    """`check-registry`: the file as a client takes it -- within the size a
    client downloads (the transport's cap, not a ParseRegistry rule, but a
    registry over it is one no client ever reads), then validate_registry.

    strict (`--strict`, what the registry repository's publish workflow runs):
    additionally refuse a devKey of 64 zero bytes -- the registry template's
    placeholder -- with the tool-only code placeholder_dev_key. NOT a client
    rule: ParseRegistry accepts it (it is a well-formed key string that no
    signature ever verifies against), and validate_registry stays its exact
    mirror; this is the maintainer's "did I forget to fill it in" check."""
    with open(path, "rb") as f:
        data = f.read(MAX_REGISTRY_BYTES + 1)
    if len(data) > MAX_REGISTRY_BYTES:
        raise GpkgError("registry_malformed", "%s is over %d bytes; clients download at most that"
                        % (path, MAX_REGISTRY_BYTES))
    r = validate_registry(data)
    if strict:
        for i, p in enumerate(r["plugins"]):
            if decode_b64_64(p["devKey"], "plugins[%d].devKey" % i, code="registry_malformed") == bytes(64):
                raise GpkgError("placeholder_dev_key",
                                "plugins[%d] (%s): devKey is the all-zero placeholder; put the developer's "
                                "dev-public.b64 in it" % (i, p["id"]))
    return r


# ---- case-insensitive names, as Windows compares them --------------------------------
#
# zip_read.cpp's NamesConflict compares with CompareStringOrdinal(..., TRUE):
# UTF-16 code unit by code unit, each through the OS uppercase table, one unit
# to one unit. That is NOT Python's str.upper()/casefold(): "SS" and U+00DF,
# "k" and U+212A KELVIN SIGN, "I" and U+0131 are all distinct to Windows, and
# the Windows table is an older Unicode than any current Python (249 BMP code
# points differ against Python 3.14's). So the table is carried here as runs
# (first, last, step, delta): every step-th code point in [first, last] maps
# to itself + delta, everything else to itself -- including both halves of a
# surrogate pair, so characters outside the BMP never fold.
#
# Extracted from ntdll!RtlUpcaseUnicodeChar on Windows 11 build 26100 and
# checked there against CompareStringOrdinal for every mapped code point.
# test_gpkg_offline compares this table with the live one on every Windows
# run; windows_upcase_runs() prints a fresh one when they differ.
_UPCASE_RUNS = (
    (0x0061, 0x007A, 1, -32), (0x00E0, 0x00F6, 1, -32), (0x00F8, 0x00FE, 1, -32), (0x00FF, 0x00FF, 1, 121),
    (0x0101, 0x012F, 2, -1), (0x0133, 0x0137, 2, -1), (0x013A, 0x0148, 2, -1), (0x014B, 0x0177, 2, -1),
    (0x017A, 0x017E, 2, -1), (0x0180, 0x0180, 1, 195), (0x0183, 0x0185, 2, -1), (0x0188, 0x0188, 1, -1),
    (0x018C, 0x018C, 1, -1), (0x0192, 0x0192, 1, -1), (0x0195, 0x0195, 1, 97), (0x0199, 0x0199, 1, -1),
    (0x019A, 0x019A, 1, 163), (0x019E, 0x019E, 1, 130), (0x01A1, 0x01A5, 2, -1), (0x01A8, 0x01A8, 1, -1),
    (0x01AD, 0x01AD, 1, -1), (0x01B0, 0x01B0, 1, -1), (0x01B4, 0x01B6, 2, -1), (0x01B9, 0x01B9, 1, -1),
    (0x01BD, 0x01BD, 1, -1), (0x01BF, 0x01BF, 1, 56), (0x01C6, 0x01C6, 1, -2), (0x01C9, 0x01C9, 1, -2),
    (0x01CC, 0x01CC, 1, -2), (0x01CE, 0x01DC, 2, -1), (0x01DD, 0x01DD, 1, -79), (0x01DF, 0x01EF, 2, -1),
    (0x01F3, 0x01F3, 1, -2), (0x01F5, 0x01F5, 1, -1), (0x01F9, 0x021F, 2, -1), (0x0223, 0x0233, 2, -1),
    (0x023C, 0x023C, 1, -1), (0x0242, 0x0242, 1, -1), (0x0247, 0x024F, 2, -1), (0x0250, 0x0250, 1, 10783),
    (0x0251, 0x0251, 1, 10780), (0x0253, 0x0253, 1, -210), (0x0254, 0x0254, 1, -206),
    (0x0256, 0x0257, 1, -205), (0x0259, 0x0259, 1, -202), (0x025B, 0x025B, 1, -203),
    (0x0260, 0x0260, 1, -205), (0x0263, 0x0263, 1, -207), (0x0268, 0x0268, 1, -209),
    (0x0269, 0x0269, 1, -211), (0x026B, 0x026B, 1, 10743), (0x026F, 0x026F, 1, -211),
    (0x0271, 0x0271, 1, 10749), (0x0272, 0x0272, 1, -213), (0x0275, 0x0275, 1, -214),
    (0x027D, 0x027D, 1, 10727), (0x0280, 0x0280, 1, -218), (0x0283, 0x0283, 1, -218),
    (0x0288, 0x0288, 1, -218), (0x0289, 0x0289, 1, -69), (0x028A, 0x028B, 1, -217), (0x028C, 0x028C, 1, -71),
    (0x0292, 0x0292, 1, -219), (0x0371, 0x0373, 2, -1), (0x0377, 0x0377, 1, -1), (0x037B, 0x037D, 1, 130),
    (0x03AC, 0x03AC, 1, -38), (0x03AD, 0x03AF, 1, -37), (0x03B1, 0x03C1, 1, -32), (0x03C3, 0x03CB, 1, -32),
    (0x03CC, 0x03CC, 1, -64), (0x03CD, 0x03CE, 1, -63), (0x03D7, 0x03D7, 1, -8), (0x03D9, 0x03EF, 2, -1),
    (0x03F2, 0x03F2, 1, 7), (0x03F8, 0x03F8, 1, -1), (0x03FB, 0x03FB, 1, -1), (0x0430, 0x044F, 1, -32),
    (0x0450, 0x045F, 1, -80), (0x0461, 0x0481, 2, -1), (0x048B, 0x04BF, 2, -1), (0x04C2, 0x04CE, 2, -1),
    (0x04CF, 0x04CF, 1, -15), (0x04D1, 0x0523, 2, -1), (0x0561, 0x0586, 1, -48), (0x1D79, 0x1D79, 1, 35332),
    (0x1D7D, 0x1D7D, 1, 3814), (0x1E01, 0x1E95, 2, -1), (0x1EA1, 0x1EFF, 2, -1), (0x1F00, 0x1F07, 1, 8),
    (0x1F10, 0x1F15, 1, 8), (0x1F20, 0x1F27, 1, 8), (0x1F30, 0x1F37, 1, 8), (0x1F40, 0x1F45, 1, 8),
    (0x1F51, 0x1F57, 2, 8), (0x1F60, 0x1F67, 1, 8), (0x1F70, 0x1F71, 1, 74), (0x1F72, 0x1F75, 1, 86),
    (0x1F76, 0x1F77, 1, 100), (0x1F78, 0x1F79, 1, 128), (0x1F7A, 0x1F7B, 1, 112), (0x1F7C, 0x1F7D, 1, 126),
    (0x1F80, 0x1F87, 1, 8), (0x1F90, 0x1F97, 1, 8), (0x1FA0, 0x1FA7, 1, 8), (0x1FB0, 0x1FB1, 1, 8),
    (0x1FB3, 0x1FB3, 1, 9), (0x1FC3, 0x1FC3, 1, 9), (0x1FD0, 0x1FD1, 1, 8), (0x1FE0, 0x1FE1, 1, 8),
    (0x1FE5, 0x1FE5, 1, 7), (0x1FF3, 0x1FF3, 1, 9), (0x214E, 0x214E, 1, -28), (0x2170, 0x217F, 1, -16),
    (0x2184, 0x2184, 1, -1), (0x24D0, 0x24E9, 1, -26), (0x2C30, 0x2C5E, 1, -48), (0x2C61, 0x2C61, 1, -1),
    (0x2C65, 0x2C65, 1, -10795), (0x2C66, 0x2C66, 1, -10792), (0x2C68, 0x2C6C, 2, -1),
    (0x2C73, 0x2C73, 1, -1), (0x2C76, 0x2C76, 1, -1), (0x2C81, 0x2CE3, 2, -1), (0x2D00, 0x2D25, 1, -7264),
    (0xA641, 0xA65F, 2, -1), (0xA663, 0xA66D, 2, -1), (0xA681, 0xA697, 2, -1), (0xA723, 0xA72F, 2, -1),
    (0xA733, 0xA76F, 2, -1), (0xA77A, 0xA77C, 2, -1), (0xA77F, 0xA787, 2, -1), (0xA78C, 0xA78C, 1, -1),
    (0xFF41, 0xFF5A, 1, -32),
)
_UPCASE = {}
for _first, _last, _step, _delta in _UPCASE_RUNS:
    for _c in range(_first, _last + 1, _step):
        _UPCASE[_c] = _c + _delta
del _first, _last, _step, _delta, _c


def windows_fold(name):
    """The key under which CompareStringOrdinal(ignoreCase=TRUE) finds two
    names equal: the UTF-16 code units, each uppercased through the table."""
    units = name.encode("utf-16-le", "surrogatepass")
    out = []
    for i in range(0, len(units), 2):
        u = units[i] | (units[i + 1] << 8)
        out.append(_UPCASE.get(u, u))
    return tuple(out)


def windows_upcase_runs():
    """The live table on this Windows machine, spelt as _UPCASE_RUNS (for
    regenerating it). Windows only."""
    import ctypes
    up = ctypes.WinDLL("ntdll").RtlUpcaseUnicodeChar
    up.restype = ctypes.c_uint16
    up.argtypes = [ctypes.c_uint16]
    runs = []
    for c in range(0x10000):
        d = up(c) - c
        if d == 0:
            continue
        if runs:
            first, last, step, delta = runs[-1]
            gap = c - last
            if delta == d and ((first == last and gap in (1, 2)) or (first != last and gap == step)):
                runs[-1] = (first, c, gap if first == last else step, delta)
                continue
        runs.append((c, c, 1, d))
    return tuple(runs)


# ---- the archive (platform/zip_read.h) -----------------------------------------------

_FLAG_ENCRYPTION = 0x0001 | 0x0040 | 0x2000
_FLAG_DESCRIPTOR = 0x0008
_FLAG_UTF8 = 0x0800
_UNIX_TYPE_MASK = 0o170000
_UNIX_SYMLINK = 0o120000
_DOS_REPARSE_POINT = 0x400


def check_zip_layout(data, max_entry=MAX_ENTRY_BYTES, max_total=MAX_PACKAGE_BYTES, max_entries=MAX_ENTRIES):
    """The rules platform/zip_read.cpp's Index applies, in the same order of
    concern: EOCD in the last 22 bytes (no archive comment), store only, no
    encryption, first local header at 0, entries contiguous in central order,
    the last one ending at the central directory, local and central names
    identical, safe names, no links, no case-insensitive duplicates. Returns
    [{name, offset, size, crc}] or raises GpkgError(zip_invalid |
    zip_unsafe_path | zip_too_large | zip_too_many_entries)."""
    b = bytes(data)
    invalid = lambda why: GpkgError("zip_invalid", why)
    u16 = lambda o: struct.unpack_from("<H", b, o)[0]
    u32 = lambda o: struct.unpack_from("<I", b, o)[0]

    if len(b) < 22:
        raise invalid("too short to be a zip")
    eocd = len(b) - 22
    if u32(eocd) != 0x06054B50:
        raise invalid("no end-of-central-directory record in the last 22 bytes (an archive comment?)")
    if u16(eocd + 20) != 0:
        raise invalid("the archive has a comment")
    total = u16(eocd + 10)
    if u16(eocd + 4) != 0 or u16(eocd + 6) != 0 or u16(eocd + 8) != total:
        raise invalid("multi-disk archive")
    cd_size, cd_off = u32(eocd + 12), u32(eocd + 16)
    if cd_off + cd_size != eocd:
        raise invalid("the central directory does not end at the EOCD (zip64, or data after it)")
    if total > max_entries:
        raise GpkgError("zip_too_many_entries", "%d entries, the limit is %d" % (total, max_entries))

    central = []
    p, cd_end, total_bytes = cd_off, cd_off + cd_size, 0
    for i in range(total):
        if cd_end - p < 46 or u32(p) != 0x02014B50:
            raise invalid("central record %d is malformed" % i)
        flags, method = u16(p + 8), u16(p + 10)
        crc, csize, usize = u32(p + 16), u32(p + 20), u32(p + 24)
        nlen, xlen, clen = u16(p + 28), u16(p + 30), u16(p + 32)
        disk, ext_attr, lho = u16(p + 34), u32(p + 38), u32(p + 42)
        nxt = p + 46 + nlen + xlen + clen
        if nxt > cd_end:
            raise invalid("central record %d runs past the directory" % i)
        if flags & _FLAG_ENCRYPTION:
            raise invalid("entry %d is encrypted" % i)
        if method != 0:
            raise invalid("entry %d uses compression method %d; v1 packages are store-only" % (i, method))
        if disk != 0 or csize != usize:
            raise invalid("entry %d: bad disk number or sizes" % i)
        raw = b[p + 46:p + 46 + nlen]
        if flags & _FLAG_UTF8:
            try:
                name = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise GpkgError("zip_unsafe_path", "entry %d name is not well-formed UTF-8" % i)
        else:
            if any(c >= 0x80 for c in raw):
                raise invalid("entry %d has a non-ASCII name without the UTF-8 flag (bit 11)" % i)
            name = raw.decode("ascii")
        if not is_safe_zip_entry_name(name):
            raise GpkgError("zip_unsafe_path", "unsafe entry name %r" % name)
        if ((ext_attr >> 16) & _UNIX_TYPE_MASK) == _UNIX_SYMLINK or (ext_attr & _DOS_REPARSE_POINT):
            raise GpkgError("zip_unsafe_path", "entry %r is a link" % name)
        if name.endswith("/") and usize != 0:
            raise invalid("directory entry %r has data" % name)
        if usize > max_entry:
            raise GpkgError("zip_too_large", "entry %r is %d bytes" % (name, usize))
        total_bytes += usize
        if total_bytes > max_total:
            raise GpkgError("zip_too_large", "the entries total more than %d bytes" % max_total)
        central.append(dict(name=name, raw=raw, flags=flags, crc=crc, size=usize, lho=lho))
        p = nxt
    if p != cd_end:
        raise invalid("the entry count does not use up the central directory")

    cursor, out = 0, []
    for c in central:
        if c["lho"] != cursor:
            raise invalid("entry %r: local header at %d, expected %d (not canonical: prefix, gap or reorder)"
                          % (c["name"], c["lho"], cursor))
        l = cursor
        if l + 30 > cd_off or u32(l) != 0x04034B50:
            raise invalid("entry %r: no local header" % c["name"])
        lflags, lmethod, lnlen, lxlen = u16(l + 6), u16(l + 8), u16(l + 26), u16(l + 28)
        if lflags & _FLAG_ENCRYPTION or lmethod != 0:
            raise invalid("entry %r: local header says encrypted or compressed" % c["name"])
        if (lflags ^ c["flags"]) & _FLAG_UTF8:
            raise invalid("entry %r: local and central disagree on UTF-8" % c["name"])
        if b[l + 30:l + 30 + lnlen] != c["raw"]:
            raise invalid("entry %r: local and central names differ" % c["name"])
        data_off = l + 30 + lnlen + lxlen
        end = data_off + c["size"]
        if end > cd_off:
            raise invalid("entry %r runs into the central directory" % c["name"])
        if lflags & _FLAG_DESCRIPTOR:
            lcrc, lcs, lus = u32(l + 14), u32(l + 18), u32(l + 22)
            if lcrc not in (0, c["crc"]) or lcs not in (0, c["size"]) or lus not in (0, c["size"]):
                raise invalid("entry %r: local CRC/sizes disagree" % c["name"])
            if end + 16 <= cd_off and b[end:end + 16] == struct.pack("<IIII", 0x08074B50, c["crc"], c["size"], c["size"]):
                cursor = end + 16
            elif end + 12 <= cd_off and b[end:end + 12] == struct.pack("<III", c["crc"], c["size"], c["size"]):
                cursor = end + 12
            else:
                raise invalid("entry %r: data descriptor missing or disagreeing" % c["name"])
        else:
            if (u32(l + 14), u32(l + 18), u32(l + 22)) != (c["crc"], c["size"], c["size"]):
                raise invalid("entry %r: local CRC/sizes disagree with the central directory" % c["name"])
            cursor = end
        out.append(dict(name=c["name"], offset=data_off, size=c["size"], crc=c["crc"]))
    if cursor != cd_off:
        raise invalid("bytes between the last entry and the central directory")

    # Same file twice (case-insensitively, as Windows compares: windows_fold),
    # or a file that is also a directory of another entry.
    seen = {}
    for e in out:
        key = windows_fold(e["name"][:-1] if e["name"].endswith("/") else e["name"])
        is_file = not e["name"].endswith("/")
        if key in seen and (is_file or seen[key]):
            raise invalid("two entries name %r" % e["name"])
        seen[key] = seen.get(key, False) or is_file
    slash = windows_fold("/")
    for key, is_file in seen.items():
        if is_file and any(k[:len(key) + 1] == key + slash for k in seen):
            raise invalid("an entry is both a file and a directory")
    return out


def check_crcs(data, entries):
    for e in entries:
        if zlib.crc32(data[e["offset"]:e["offset"] + e["size"]]) & 0xFFFFFFFF != e["crc"]:
            raise GpkgError("zip_invalid", "CRC mismatch in %r" % e["name"])


# ---- pack ------------------------------------------------------------------------------

def _collect_files(src):
    """Package-relative '/'-separated names of every file under src, manifest
    first. VCS directories (.git, .hg, .svn) are skipped, and .github at the
    root (CI configuration, not the plugin). Refused: links and
    junctions (the host refuses to extract them), and anything that looks like
    a private key -- a *.pem / *.key file, or a file whose bytes contain
    "PRIVATE KEY" -- because a package is published to everyone, and keygen's
    output is one careless copy away from a plugin's source tree."""
    # Every name is judged BEFORE its path is stat'ed, opened or made absolute.
    # Before Windows 11 24H2, "...\nul.txt" (a device name plus an extension,
    # as the last component) is the NUL DEVICE, not the file os.walk listed:
    # lstat and open reach the device (the "PRIVATE KEY" scan reads nothing),
    # and GetFullPathNameW -- os.path.abspath, and so os.path.relpath -- turns
    # the whole path into "\\.\nul". So the package-relative name is built
    # lexically from os.walk's own strings (each root is src + a suffix), never
    # with relpath, and a device name is refused on every Windows version
    # without the device being touched.
    names = []
    for root, dirs, files in os.walk(src):
        rel_dir = root[len(src):].replace(os.sep, "/").strip("/")
        prefix = rel_dir + "/" if rel_dir else ""
        dirs[:] = [d for d in dirs if d not in VCS_DIRS and not (not rel_dir and d in ROOT_SKIP_DIRS)]
        for d in dirs:
            if not is_safe_zip_entry_name(prefix + d + "/"):
                raise GpkgError("zip_unsafe_path",
                                "directory name %r is not allowed in a package (policy_zip_name.h)" % (prefix + d))
            full = os.path.join(root, d)
            if os.path.islink(full) or _is_reparse(full):
                raise GpkgError("zip_unsafe_path", "%s is a link or junction" % full)
        dirs.sort()
        for f in sorted(files):
            full = os.path.join(root, f)
            rel = prefix + f
            if not is_safe_zip_entry_name(rel):
                raise GpkgError("zip_unsafe_path", "file name %r is not allowed in a package (policy_zip_name.h)" % rel)
            if os.path.islink(full) or _is_reparse(full):
                raise GpkgError("zip_unsafe_path", "%s is a link" % full)
            if f.lower().endswith(SECRET_EXTENSIONS):
                raise GpkgError("secret_in_package", "%s looks like a key file; move it out of --src" % rel)
            with open(full, "rb") as fh:
                if b"PRIVATE KEY" in fh.read():
                    raise GpkgError("secret_in_package", "%s contains a private key; move it out of --src" % rel)
            names.append(rel)
    names.sort(key=lambda n: (n != "manifest.json", n))
    return names


def _is_reparse(path):
    attrs = getattr(os.lstat(path), "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def pack(src, out, min_app_version=DEFAULT_MIN_APP_VERSION, released_at=None):
    """Validates src/manifest.json, writes out/<id>-<version>.gpkg (store-only,
    canonical layout, no comment) and out/ghost-plugin.json. Returns the two
    paths. Nothing is written when a check fails."""
    if not is_plugin_version(min_app_version):
        raise GpkgError("release_malformed", "--min-app-version %r is not a canonical version" % min_app_version)
    manifest_path = os.path.join(src, "manifest.json")
    if not os.path.isfile(manifest_path):
        raise GpkgError("manifest_missing", "%s does not exist" % manifest_path)
    with open(manifest_path, "rb") as f:
        manifest = validate_manifest(f.read())

    real_src = os.path.normcase(os.path.realpath(src))
    real_out = os.path.normcase(os.path.realpath(out))
    if real_out == real_src or real_out.startswith(real_src.rstrip("\\/") + os.sep):
        raise UsageError("out_inside_src", "--out %s is inside --src %s; the package would contain itself" % (out, src))
    names = _collect_files(src)
    if len(names) > MAX_ENTRIES:
        raise GpkgError("zip_too_many_entries", "%d files, the limit is %d" % (len(names), MAX_ENTRIES))
    # Every name already passed is_safe_zip_entry_name in _collect_files.
    # Case collisions (possible on a case-sensitive file system) are caught by
    # check_zip_layout on the finished archive, with the Windows comparison.
    if manifest["entry"] not in names:
        raise GpkgError("plugin_entry_missing", "entry %r is not in %s" % (manifest["entry"], src))
    if "icon" in manifest:
        if manifest["icon"] not in names:
            raise GpkgError("manifest_malformed", "icon %r is not in %s" % (manifest["icon"], src))
        if os.path.getsize(os.path.join(src, manifest["icon"])) > MAX_ICON_BYTES:
            raise GpkgError("manifest_malformed", "icon is larger than %d bytes" % MAX_ICON_BYTES)

    buf = io.BytesIO()                      # seekable: no data descriptors
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as z:
        for n in names:
            with open(os.path.join(src, *n.split("/")), "rb") as f:
                content = f.read()
            if len(content) > MAX_ENTRY_BYTES:
                raise GpkgError("zip_too_large", "%s is larger than %d bytes" % (n, MAX_ENTRY_BYTES))
            info = zipfile.ZipInfo(n, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            z.writestr(info, content)
        # No z.comment: zip_read refuses an archive comment.
    package = buf.getvalue()
    if len(package) > MAX_PACKAGE_BYTES:
        raise GpkgError("zip_too_large", "the package is %d bytes, the limit is %d" % (len(package), MAX_PACKAGE_BYTES))
    check_crcs(package, check_zip_layout(package))   # our own output, held to the host's rules

    release = build_release(manifest, package, min_app_version, released_at)
    os.makedirs(out, exist_ok=True)
    pkg_path = os.path.join(out, package_name(manifest["id"], manifest["version"]))
    rel_path = os.path.join(out, RELEASE_FILE)
    with open(pkg_path, "wb") as f:
        f.write(package)
    with open(rel_path, "wb") as f:       # binary: these are the bytes that get signed
        f.write(release)
    stale = os.path.join(out, SIG_FILE)
    if os.path.exists(stale):               # it signed the previous ghost-plugin.json
        os.remove(stale)
    return pkg_path, rel_path


def verify_package(dist):
    """Everything `verify` checks except the signature: the release document,
    the package's size and SHA-256 against it, the archive layout and CRCs,
    and the manifest inside against the release. Returns the release dict."""
    with open(os.path.join(dist, RELEASE_FILE), "rb") as f:
        release = validate_release(f.read())
    pkg = release["package"]
    if pkg["size"] > MAX_PACKAGE_BYTES:
        raise GpkgError("package_size_mismatch", "declared size %d is over the limit" % pkg["size"])
    path = os.path.join(dist, pkg["name"])
    if not os.path.isfile(path):
        raise GpkgError("package_unreachable", "%s does not exist" % path)
    with open(path, "rb") as f:
        data = f.read()
    if len(data) != pkg["size"]:
        raise GpkgError("package_size_mismatch", "%d bytes on disk, %d declared" % (len(data), pkg["size"]))
    if hashlib.sha256(data).hexdigest() != pkg["sha256"]:
        raise GpkgError("package_hash_mismatch", "SHA-256 differs from ghost-plugin.json")
    entries = check_zip_layout(data)
    check_crcs(data, entries)
    by_name = {e["name"]: e for e in entries}
    if "manifest.json" not in by_name:
        raise GpkgError("manifest_missing", "no manifest.json at the package root")
    e = by_name["manifest.json"]
    manifest = validate_manifest(data[e["offset"]:e["offset"] + e["size"]])
    if manifest["id"] != release["id"] or manifest["version"] != release["version"]:
        raise GpkgError("manifest_mismatch", "manifest %s %s vs release %s %s"
                        % (manifest["id"], manifest["version"], release["id"], release["version"]))
    if manifest["entry"] not in by_name:
        raise GpkgError("plugin_entry_missing", "entry %r is not in the package" % manifest["entry"])
    return release


# ---- signatures (need `cryptography`) ----------------------------------------------

def _crypto():
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
    except ImportError:
        sys.stderr.write("gpkg: this command needs Python `cryptography`: pip install cryptography\n")
        sys.exit(2)
    return dict(InvalidSignature=InvalidSignature, hashes=hashes, serialization=serialization, ec=ec,
                decode=decode_dss_signature, encode=encode_dss_signature)


def decode_b64_64(text, what, error=GpkgError, code="release_bad_sig"):
    """Standard base64 (padded) of exactly 64 bytes. Trailing space, tab, CR
    and LF are allowed (spec-release section 1) and nothing else is stripped.
    The unused low bits of the last data character are NOT required to be
    zero: base64 decoders commonly ignore them, and the C++ decoder accepts the
    same set of strings rather than refuse a signature another encoder wrote."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("ascii", "replace")
    t = text.rstrip(" \t\r\n")
    if len(t) != 88:
        raise error(code, "%s must be 88 base64 characters, got %d" % (what, len(t)))
    try:
        raw = base64.b64decode(t, validate=True)
    except ValueError:
        raise error(code, "%s is not valid base64" % what)
    if len(raw) != 64:
        raise error(code, "%s does not decode to 64 bytes" % what)
    return raw


def keygen(out):
    c = _crypto()
    os.makedirs(out, exist_ok=True)
    priv_path = os.path.join(out, "dev-private.pem")
    pub_path = os.path.join(out, "dev-public.b64")
    if os.path.exists(priv_path):
        raise GpkgError("io_error", "%s already exists; refusing to overwrite a private key" % priv_path)
    key = c["ec"].generate_private_key(c["ec"].SECP256R1())
    pem = key.private_bytes(c["serialization"].Encoding.PEM, c["serialization"].PrivateFormat.PKCS8,
                            c["serialization"].NoEncryption())
    fd = os.open(priv_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    nums = key.public_key().public_numbers()
    xy = nums.x.to_bytes(32, "big") + nums.y.to_bytes(32, "big")
    with open(pub_path, "wb") as f:
        f.write(base64.b64encode(xy) + b"\n")
    return priv_path, pub_path


def sign_bytes(key_pem, data):
    """The .sig file content for `data`: base64 of raw r||s (P1363) + newline."""
    c = _crypto()
    key = c["serialization"].load_pem_private_key(key_pem, password=None)
    if not isinstance(key, c["ec"].EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise GpkgError("release_bad_sig", "the key is not a P-256 EC private key")
    r, s = c["decode"](key.sign(data, c["ec"].ECDSA(c["hashes"].SHA256())))
    return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")) + b"\n"


def verify_bytes(pub_xy, data, sig_text, code="release_bad_sig"):
    c = _crypto()
    sig = decode_b64_64(sig_text, "the signature", code=code)
    try:
        pub = c["ec"].EllipticCurvePublicNumbers(int.from_bytes(pub_xy[:32], "big"), int.from_bytes(pub_xy[32:], "big"),
                                                 c["ec"].SECP256R1()).public_key()
        der = c["encode"](int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
        pub.verify(der, data, c["ec"].ECDSA(c["hashes"].SHA256()))
        return True
    except (ValueError, c["InvalidSignature"]):
        return False


def sign(key_path, dist):
    with open(key_path, "rb") as f:
        pem = f.read()
    with open(os.path.join(dist, RELEASE_FILE), "rb") as f:
        data = f.read()                    # the raw bytes, exactly as they will be served
    validate_release(data)
    sig = sign_bytes(pem, data)
    path = os.path.join(dist, SIG_FILE)
    with open(path, "wb") as f:
        f.write(sig)
    return path


def _read_pubkey(arg):
    if os.path.isfile(arg):
        with open(arg, "rb") as f:
            text = f.read().decode("ascii", "replace")
        if not text.strip():
            # The registry template ships root-public.b64 EMPTY on purpose:
            # say so, rather than "must be 88 base64 characters, got 0".
            raise UsageError("bad_public_key", "the public key file %s is empty" % arg)
        arg = text
    return decode_b64_64(arg, "the public key", UsageError, "bad_public_key")


def verify(dist, pubkey):
    """Signature first, as the host does (spec-release §4), then verify_package."""
    pub = _read_pubkey(pubkey)             # a bad --pubkey is a usage error, reported first
    _crypto()
    with open(os.path.join(dist, RELEASE_FILE), "rb") as f:
        data = f.read()
    sig_path = os.path.join(dist, SIG_FILE)
    if not os.path.isfile(sig_path):
        raise GpkgError("release_bad_sig", "%s does not exist; run `gpkg.py sign`" % sig_path)
    with open(sig_path, "rb") as f:
        sig = f.read()
    if not verify_bytes(pub, data, sig):
        raise GpkgError("release_bad_sig", "ghost-plugin.json.sig does not verify with that public key")
    return verify_package(dist)


def verify_sig(pubkey, path, sig_path=None):
    """`verify-sig`: sig_path (default <path>.sig) over the RAW bytes of path
    -- nothing stripped, decoded or normalised (spec-release §1). The code is
    the one a client would give: registry_bad_sig for a file named
    registry.json, release_bad_sig for anything else. The .sig is held to the
    client's kMaxSigBytes."""
    pub = _read_pubkey(pubkey)             # a bad --pubkey is a usage error, reported first
    _crypto()
    code = "registry_bad_sig" if os.path.basename(path) == REGISTRY_FILE else "release_bad_sig"
    with open(path, "rb") as f:
        data = f.read()
    sig_path = sig_path or path + ".sig"
    if not os.path.isfile(sig_path):
        raise GpkgError(code, "%s does not exist" % sig_path)
    with open(sig_path, "rb") as f:
        sig = f.read(MAX_SIG_BYTES + 1)
    if len(sig) > MAX_SIG_BYTES:
        raise GpkgError(code, "%s is over %d bytes; clients refuse it" % (sig_path, MAX_SIG_BYTES))
    if not verify_bytes(pub, data, sig, code=code):
        raise GpkgError(code, "%s does not verify %s with that public key" % (sig_path, path))


# ---- CLI --------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="gpkg.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("keygen", help="make a developer key pair")
    p.add_argument("--out", required=True)
    p = sub.add_parser("pack", help="validate manifest.json and build the .gpkg + ghost-plugin.json")
    p.add_argument("--src", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--min-app-version", default=DEFAULT_MIN_APP_VERSION)
    p = sub.add_parser("sign", help="sign ghost-plugin.json")
    p.add_argument("--key", required=True)
    p.add_argument("--dist", required=True)
    p = sub.add_parser("verify", help="check a dist/ directory the way the host will")
    p.add_argument("--dist", required=True)
    p.add_argument("--pubkey", required=True, help="dev-public.b64 file, or the base64 text itself")
    p = sub.add_parser("verify-sig", help="check a detached .sig over a file's raw bytes")
    p.add_argument("--pubkey", required=True, help="a public key file (root-public.b64), or the base64 text itself")
    p.add_argument("--file", required=True)
    p.add_argument("--sig", help="the signature file (default: <file>.sig)")
    p = sub.add_parser("check-registry", help="check registry.json the way a client parses it")
    p.add_argument("--file", required=True)
    p.add_argument("--strict", action="store_true",
                   help="also refuse the template's all-zero devKey placeholder (placeholder_dev_key)")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "keygen":
            priv, pub = keygen(a.out)
            print("wrote %s (keep it secret, do not commit it)\nwrote %s (send this to the registry)" % (priv, pub))
        elif a.cmd == "pack":
            pkg, rel = pack(a.src, a.out, a.min_app_version)
            print("wrote %s\nwrote %s\nnext: gpkg.py sign --key <dev-private.pem> --dist %s" % (pkg, rel, a.out))
        elif a.cmd == "sign":
            print("wrote %s" % sign(a.key, a.dist))
        elif a.cmd == "verify":
            r = verify(a.dist, a.pubkey)
            print("ok: %s %s (%s, %d bytes)" % (r["id"], r["version"], r["package"]["name"], r["package"]["size"]))
        elif a.cmd == "verify-sig":
            verify_sig(a.pubkey, a.file, a.sig)
            print("ok: %s" % a.file)
        elif a.cmd == "check-registry":
            r = check_registry(a.file, a.strict)
            print("ok: %s: seq %d, %d plugins, %d revocations"
                  % (a.file, r["seq"], len(r["plugins"]), len(r.get("revoked", []))))
    except UsageError as e:
        sys.stderr.write("error: %s: %s\n" % (e.code, e.message))
        return 2
    except GpkgError as e:
        sys.stderr.write("error: %s: %s\n" % (e.code, e.message))
        return 1
    except (ValueError, TypeError, KeyError, UnicodeError) as e:
        # A validation path that met a shape it did not expect: still a
        # refusal, never a traceback.
        sys.stderr.write("error: invalid_input: %s\n" % e)
        return 1
    except OSError as e:
        sys.stderr.write("error: io_error: %s\n" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
