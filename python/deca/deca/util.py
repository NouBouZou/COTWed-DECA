import contextlib
import datetime
import gc
import os
import struct
import weakref
import sys
import ctypes

from deca.path import UniPath


@contextlib.contextmanager
def gc_paused():
    """
    Pause the cyclic garbage collector while building large structures.

    Building the views of a project allocates millions of small objects; each allocation burst triggers a collection
    that walks every live object (including the previous tree), which cost more than the build itself. Reference
    counting still frees memory normally; only cycle detection waits until the block ends.
    """
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()

class Logger:
    # Callables (level, message) notified of every message logged in this process. The GUI registers one while it
    # opens or scans a project, to show the log live; scan worker processes have their own interpreter and are not
    # affected.
    listeners = []

    def __init__(self, working_dir):
        self.working_dir = working_dir

    def log_base(self, level, s):
        msg = '{}: {}'.format(datetime.datetime.now(), s)
        if self.working_dir is not None:
            with open(self.working_dir + 'log.txt', 'a') as f:
                f.write(msg + '\n')
                if level <= 2:
                    print(msg)

        for listener in tuple(Logger.listeners):
            listener(level, msg)

        return msg

    def error(self, s):
        self.log_base(0, s)

    def warning(self, s):
        self.log_base(1, s)

    def log(self, s):
        self.log_base(2, s)

    def trace(self, s):
        self.log_base(3, s)

    def debug(self, s):
        self.log_base(3, s)


class DecaSignal:
    def __init__(self):
        self.callbacks = set()

    def connect(self, obj, func):
        self.callbacks.add((weakref.ref(obj), func))

    def disconnect(self, obj):
        to_erase = []
        for i in self.callbacks:
            if i[0]() == obj:
                to_erase.append(i)

        for i in to_erase:
            self.callbacks.remove(i)

    def call(self, *params, **kwargs):
        to_erase = []
        callbacks = self.callbacks.copy()
        for i in callbacks:
            obj = i[0]()
            func = i[1]
            if obj is not None:
                func(obj, *params, **kwargs)
            else:
                to_erase.append(i)

        for i in to_erase:
            self.callbacks.remove(i)


def dump_line(line, width, fmt='hex'):
    if fmt == 'hex' or len(line) != width:
        line = ''.join(['{:02x}'.format(v) for v in bytearray(line)])
    elif fmt == 'char':
        line = ['{}'.format(chr(v)) for v in bytearray(line)]
    else:
        line = struct.unpack(fmt, line)
    return '{}'.format(line)


def dump_block(blk, width, fmt='hex'):
    for i in range((len(blk) + width - 1) // width):
        line = blk[(i * width):((i + 1) * width)]
        line = dump_line(line, width, fmt)
        print(line)


def remove_prefix_if_present(prefix, s):
    if s.find(prefix) == 0:
        return s[len(prefix):]
    else:
        return None


def remove_suffix_if_present(suffix, s):
    if s.endswith(suffix):
        return s[:-len(suffix)]
    else:
        return None


def common_prefix(s0, s1):
    cnt = 0
    while len(s0) > cnt and len(s1) > cnt and s0[cnt] == s1[cnt]:
        cnt += 1
    return s0[:cnt], s0[cnt:], s1[cnt:]


def align_to(v, boundry):
    return ((v + boundry - 1) // boundry) * boundry


def make_dir_for_file(fn):
    new_dir = os.path.dirname(fn)
    os.makedirs(new_dir, exist_ok=True)
    return new_dir


def to_unicode(s):
    if isinstance(s, bytes):
        s = s.decode('utf-8')

    return s


def deca_root():
    frozen = getattr(sys, 'frozen', False)
    if frozen and hasattr(sys, '_MEIPASS'):
        # print('running in a PyInstaller bundle')
        bundle_dir = sys._MEIPASS
    else:
        # print('running in a normal Python process')
        bundle_dir = UniPath.abspath(UniPath.join(UniPath.dirname(__file__), '..', '..', '..'))

    # print(f"{__file__=}")
    # print(f"{bundle_dir=}")
    # print(f"{sys.argv[0]=}")
    # print(f"{sys.executable=}")
    # print(f"{os.getcwd()=}")
    # print(f"{frozen=}")

    return bundle_dir


# THREAD EXECUTION STATES
ES_CONTINUOUS        = 0x80000000
ES_SYSTEM_REQUIRED   = 0x00000001
ES_DISPLAY_REQUIRED  = 0x00000002
ES_USER_PRESENT      = 0x00000004 
ES_AWAYMODE_REQUIRED = 0x00000040

# Prevent the system from going to sleep
def system_sleep_prevent():
    if os.name == 'nt':
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)

# Allow the system to sleep normally
def system_sleep_allow():
    if os.name == 'nt':
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)

