"""
Videos of theHunter: COTW other than Bink (COTWed DECA, 2026-10-11): two unnamed files of game78.tab.

IVF (DECA type ivf), 633a08dd, 987 185 bytes, read to the last byte:
    'DKIF', u16 version 0, u16 header size (32), fourcc ('VP90'), u16 width, u16 height, u32 rate, u32 scale,
    u32 frame count, 4 bytes; then per frame: u32 size, u64 timestamp, the frame.
    Sample: VP9, 356 x 480, 30 frames/s, 159 frames.

WebM (DECA type webm), d370f806, 316 208 bytes: Matroska (EBML) with DocType 'webm'. Elements: ID (variable-length
integer, marker bit kept), size (variable-length integer, marker bit removed; all ones = unknown size), data. The
reader walks the element tree (masters below) to the end of the file and reports the header, segment information
and tracks. Sample: VP9, 356 x 480, 5.3 s, written by Lavf59.27.100 (ffmpeg): the same animation as the IVF (ffprobe
agrees).

Neither is decoded: Export raw gives a .ivf / .webm that ffmpeg or VLC play.
"""
import struct
from typing import Dict, List, Optional, Tuple


class EVideo(Exception):
    pass


# -- IVF ---------------------------------------------------------------------------------------------------------

def is_ivf(head: bytes) -> bool:
    return len(head) >= 32 and head[:4] == b'DKIF'


class Ivf:
    def __init__(self, data: bytes):
        if not is_ivf(data):
            raise EVideo('not an IVF file')
        (self.version, self.header_size, fourcc, self.width, self.height, self.rate, self.scale, self.frames,
         _unused) = struct.unpack_from('<HH4sHHIII4s', data, 4)
        self.fourcc = fourcc.decode('latin-1')
        p, sizes = self.header_size, []
        while p < len(data):
            if p + 12 > len(data):
                raise EVideo('frame header cut at 0x{:x}'.format(p))
            size, _pts = struct.unpack_from('<IQ', data, p)
            p += 12 + size
            sizes.append(size)
        if p != len(data):
            raise EVideo('last frame ends at 0x{:x}, file has 0x{:x} bytes'.format(p, len(data)))
        if len(sizes) != self.frames:
            raise EVideo('{} frames announced, {} in the file'.format(self.frames, len(sizes)))
        self.frame_sizes = sizes

    @property
    def fps(self) -> float:
        return self.rate / self.scale if self.scale else 0.0


def ivf_lines(data: bytes) -> List[str]:
    v = Ivf(data)
    return ['IVF version {}: {} {} x {}, {} frames at {:g} frames/s ({:.2f} s)'.format(
        v.version, v.fourcc, v.width, v.height, v.frames, v.fps, v.frames / v.fps if v.fps else 0.0),
        'frame sizes: {} to {} bytes, {} bytes in all'.format(min(v.frame_sizes), max(v.frame_sizes),
                                                             sum(v.frame_sizes))]


# -- WebM / Matroska -------------------------------------------------------------------------------------------------

EBML_MAGIC = b'\x1a\x45\xdf\xa3'
MASTERS = {0x1A45DFA3: 'EBML', 0x18538067: 'Segment', 0x114D9B74: 'SeekHead', 0x4DBB: 'Seek', 0x1549A966: 'Info',
           0x1654AE6B: 'Tracks', 0xAE: 'TrackEntry', 0xE0: 'Video', 0xE1: 'Audio', 0x1F43B675: 'Cluster',
           0xA0: 'BlockGroup', 0x1C53BB6B: 'Cues', 0xBB: 'CuePoint', 0xB7: 'CueTrackPositions',
           0x1254C367: 'Tags', 0x7373: 'Tag', 0x63C0: 'Targets', 0x67C8: 'SimpleTag', 0x1043A770: 'Chapters',
           0x1941A469: 'Attachments'}
STRINGS = {0x4282: 'DocType', 0x4D80: 'MuxingApp', 0x5741: 'WritingApp', 0x86: 'CodecID', 0x7BA9: 'Title',
           0x45A3: 'TagName', 0x4487: 'TagString', 0x22B59C: 'Language'}
UINTS = {0x4287: 'DocTypeVersion', 0x2AD7B1: 'TimecodeScale', 0xD7: 'TrackNumber', 0x83: 'TrackType', 0xB0: 'PixelWidth',
         0xBA: 'PixelHeight', 0x9F: 'Channels', 0x23E383: 'DefaultDuration'}
FLOATS = {0x4489: 'Duration', 0xB5: 'SamplingFrequency'}
TRACK_TYPES = {1: 'video', 2: 'audio', 17: 'subtitle'}


def is_webm(head: bytes) -> bool:
    return len(head) >= 4 and head[:4] == EBML_MAGIC


def _vint(data: bytes, p: int, keep_marker: bool) -> Tuple[int, int, bool]:
    """(value, next position, all value bits set). Raises EVideo on a malformed integer."""
    if p >= len(data):
        raise EVideo('variable-length integer beyond the end at 0x{:x}'.format(p))
    first = data[p]
    length = 1
    while length <= 8 and not first & (0x80 >> (length - 1)):
        length += 1
    if length > 8 or p + length > len(data):
        raise EVideo('bad variable-length integer at 0x{:x}'.format(p))
    value = first if keep_marker else first & ((0x80 >> (length - 1)) - 1)
    for b in data[p + 1:p + length]:
        value = (value << 8) | b
    all_ones = not keep_marker and value == (1 << (7 * length)) - 1
    return value, p + length, all_ones


class WebM:
    def __init__(self, data: bytes):
        if not is_webm(data):
            raise EVideo('not an EBML file')
        self.values: Dict[str, object] = {}
        self.tracks: List[Dict[str, object]] = []
        self.counts: Dict[str, int] = {}
        self._walk(data, 0, len(data), 0)
        if self.values.get('DocType') not in ('webm', 'matroska'):
            raise EVideo('EBML DocType {!r}'.format(self.values.get('DocType')))

    def _walk(self, data: bytes, p: int, end: int, depth: int):
        while p < end:
            eid, p, _ = _vint(data, p, True)
            size, p, unknown = _vint(data, p, False)
            if unknown:
                size = end - p
            if p + size > end:
                raise EVideo('element 0x{:X} of {} bytes ends beyond its parent (0x{:x})'.format(eid, size, end))
            body = data[p:p + size]
            name = MASTERS.get(eid)
            if name is not None:
                self.counts[name] = self.counts.get(name, 0) + 1
                if name == 'TrackEntry':
                    self.tracks.append({})
                if depth > 16:
                    raise EVideo('elements nested too deep')
                self._walk(data, p, p + size, depth + 1)
            else:
                if eid in (0xA3, 0xA1):           # SimpleBlock, Block
                    self.counts['blocks'] = self.counts.get('blocks', 0) + 1
                target = self.tracks[-1] if self.tracks and depth >= 3 and eid in (
                    0xD7, 0x83, 0x86, 0xB0, 0xBA, 0x9F, 0xB5, 0x23E383, 0x22B59C) else self.values
                if eid in STRINGS:
                    target.setdefault(STRINGS[eid], body.rstrip(b'\0').decode('utf-8', 'replace'))
                elif eid in UINTS:
                    target.setdefault(UINTS[eid], int.from_bytes(body, 'big'))
                elif eid in FLOATS and len(body) in (4, 8):
                    target.setdefault(FLOATS[eid], struct.unpack('>f' if len(body) == 4 else '>d', body)[0])
            p += size
        if p != end:
            raise EVideo('elements end at 0x{:x}, parent at 0x{:x}'.format(p, end))


def webm_lines(data: bytes) -> List[str]:
    w = WebM(data)
    v = w.values
    scale = v.get('TimecodeScale', 1000000)
    duration = v.get('Duration')
    lines = ['{} (EBML, DocType version {}), {} bytes'.format(
        v.get('DocType'), v.get('DocTypeVersion', '?'), len(data)),
        'muxing {}, writing {}{}'.format(v.get('MuxingApp', '?'), v.get('WritingApp', '?'),
                                         '' if duration is None else ', duration {:.2f} s'.format(
                                             duration * scale / 1e9)),
        '{} track(s), {} cluster(s), {} block(s)'.format(len(w.tracks), w.counts.get('Cluster', 0),
                                                       w.counts.get('blocks', 0))]
    for t in w.tracks:
        kind = TRACK_TYPES.get(t.get('TrackType'), 'type {}'.format(t.get('TrackType')))
        extra = ''
        if 'PixelWidth' in t:
            extra = ', {} x {}'.format(t['PixelWidth'], t.get('PixelHeight'))
        elif 'SamplingFrequency' in t:
            extra = ', {:g} Hz, {} channel(s)'.format(t['SamplingFrequency'], t.get('Channels', 1))
        lines.append('  track {}: {} {}{}'.format(t.get('TrackNumber', '?'), kind, t.get('CodecID', '?'), extra))
    return lines


def dump_lines(data: bytes) -> List[str]:
    if is_ivf(data):
        return ivf_lines(data)
    if is_webm(data):
        return webm_lines(data)
    raise EVideo('neither IVF nor WebM')


def describe(data: bytes) -> Optional[str]:
    try:
        return dump_lines(data)[0]
    except (EVideo, struct.error):
        return None
