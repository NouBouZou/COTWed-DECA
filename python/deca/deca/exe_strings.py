"""
COTWed DECA: path-like strings of the game executable.

Why: DECA only knew the executable's strings through resources/deca/ghidra_strings/<game>.txt, a list extracted
once by kk49 from an old build. Paths the current build loads by name (settings, UI, hardcoded resources) were
missing whenever they changed or were added after that list was made. Reading the executable at scan time keeps the
names in step with the installed game.

Only ASCII strings terminated by a NUL byte are read (C string literals). A string is kept as a path when it has
the shape of a virtual path: path characters only, and a file extension after the last '/'. Strings containing
printf conversions ("zoom%d/%d.ddsc") are returned separately: they are name patterns, not names
(cotw/generer_noms.py expands them).
"""
import re
from typing import Iterable, Set, Tuple

# printable ASCII run of at least 5 characters, directly followed by a NUL byte
_RX_CSTRING = re.compile(rb'[\x20-\x7e]{5,260}(?=\x00)')
# virtual path: path characters only, at least one character before the extension, extension of 1 to 24
# characters after the last '/'; '%' allowed for printf patterns
_RX_PATH = re.compile(rb'^[A-Za-z0-9_\-./\\%]*[A-Za-z0-9_\-%]\.[A-Za-z0-9_]{1,24}$')
_RX_PRINTF = re.compile(rb'%[-+ #0]*\d*(?:\.\d+)?(?:hh|h|ll|l|z|I64|I32)?[diuxXsc]')
# a path needs a directory or an extension that is not a number ("1.5", "v2.3" are not files)
_RX_NUMERIC_EXT = re.compile(rb'\.\d+$')


def c_strings(data: bytes) -> Iterable[bytes]:
    """ASCII strings terminated by NUL, at least 5 characters long."""
    return (m.group(0) for m in _RX_CSTRING.finditer(data))


def path_strings(data: bytes) -> Tuple[Set[bytes], Set[bytes]]:
    """
    (paths, patterns) found in data. Backslashes are turned into '/', as DbWrap.propose_string does.
    A lower-case copy is added when the string has capitals: COTW virtual paths are lower case, the code is not
    always (hash32 is case sensitive).
    """
    paths: Set[bytes] = set()
    patterns: Set[bytes] = set()
    for s in c_strings(data):
        s = s.replace(b'\\', b'/')
        if not _RX_PATH.match(s) or _RX_NUMERIC_EXT.search(s):
            continue
        variants = {s, s.lower()}
        if b'%' in s:
            if _RX_PRINTF.search(s):
                patterns.update(variants)
            continue
        paths.update(variants)
    return paths, patterns


def exe_path_strings(exe_path: str) -> Tuple[Set[bytes], Set[bytes]]:
    with open(exe_path, 'rb') as f:
        data = f.read()
    return path_strings(data)
