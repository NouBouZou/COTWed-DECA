"""
Bink videos (DECA type bik). theHunter: COTW has 3, all without a name (105 MB): 1280x720 Bink 1 revision 'i'.
Bink 2 (KB2, DECA type kb2; none in COTW) has more header fields from revision 'j' on and is not read here.

COTWed DECA, 2026-10-10, from 2 of the 3 files, read to the frame index's last offset (= file size):

    'BIK', revision letter; u32 file size - 8; u32 frame count; u32 largest frame size;
    u32 frame count (again); u32 width; u32 height; u32 frame rate numerator; u32 frame rate denominator;
    u32 video flags (0x00100000 alpha plane, 0x00020000 grey); u32 audio track count T;
    T x (u16 [max decoded audio size], u16 channels); T x (u16 sample rate, u16 flags: 0x4000 16-bit,
    0x2000 stereo, 0x1000 DCT); T x u32 track id;
    frame count + 1 x u32 frame offset (bit 0: key frame; the last offset is the file size).

Video and audio are not decoded: the Structure view shows the header, and Export raw gives a .bik that the RAD
Video Tools or ffmpeg play.
"""
import struct
from typing import BinaryIO, Dict, List

VIDEO_FLAGS = {0x00100000: 'alpha', 0x00020000: 'grey'}
AUDIO_FLAGS = {0x4000: '16-bit', 0x2000: 'stereo', 0x1000: 'DCT'}


class EBink(Exception):
    pass


class Bink:
    def __init__(self, data: bytes):
        if len(data) < 44 or data[:3] != b'BIK':
            raise EBink('not a Bink 1 file')
        self.revision = chr(data[3])
        (size8, self.frames, self.largest_frame, frames2, self.width, self.height, self.rate_num, self.rate_den,
         self.video_flags, tracks) = struct.unpack_from('<10I', data, 4)
        self.file_size = size8 + 8
        if tracks > 256 or self.frames > 10_000_000:
            raise EBink('implausible header ({} frames, {} audio tracks)'.format(self.frames, tracks))
        p = 44
        need = p + 12 * tracks + 4 * (self.frames + 1)
        if len(data) < need:
            raise EBink('header of {} bytes, {} available'.format(need, len(data)))
        a = [struct.unpack_from('<HH', data, p + 4 * i) for i in range(tracks)]
        p += 4 * tracks
        b = [struct.unpack_from('<HH', data, p + 4 * i) for i in range(tracks)]
        p += 4 * tracks
        ids = [struct.unpack_from('<I', data, p + 4 * i)[0] for i in range(tracks)]
        p += 4 * tracks
        self.tracks: List[Dict] = [{'id': ids[i], 'max_size': a[i][0], 'channels': a[i][1], 'rate': b[i][0],
                                    'flags': b[i][1]} for i in range(tracks)]
        offs = struct.unpack_from('<{}I'.format(self.frames + 1), data, p)
        self.header_size = p + 4 * (self.frames + 1)
        self.key_frames = sum(o & 1 for o in offs[:-1])
        clean = [o & ~1 for o in offs]
        if clean[0] != self.header_size or any(x > y for x, y in zip(clean, clean[1:])) or clean[-1] != self.file_size:
            raise EBink('frame index does not run from the header ({}) to the end of the file ({})'.format(
                self.header_size, self.file_size))
        self.frames_check = frames2

    @property
    def fps(self) -> float:
        return self.rate_num / self.rate_den if self.rate_den else 0.0

    @property
    def duration(self) -> float:
        return self.frames / self.fps if self.fps else 0.0


def bink_from(f: BinaryIO, size: int) -> Bink:
    """Bink header and frame index read from a file object (the video data is not read)."""
    f.seek(0)
    head = f.read(44)
    if len(head) < 44:
        raise EBink('file of {} bytes'.format(size))
    frames, tracks = struct.unpack_from('<I', head, 8)[0], struct.unpack_from('<I', head, 40)[0]
    if tracks > 256 or frames > 10_000_000:
        raise EBink('implausible header ({} frames, {} audio tracks)'.format(frames, tracks))
    f.seek(0)
    v = Bink(f.read(44 + 12 * tracks + 4 * (frames + 1)))
    if v.file_size != size:
        raise EBink('header says {} bytes, file has {}'.format(v.file_size, size))
    return v


def _flags(value: int, names: Dict[int, str]) -> str:
    parts = [n for bit, n in names.items() if value & bit]
    rest = value & ~sum(names)
    if rest:
        parts.append('0x{:x}'.format(rest))
    return ', '.join(parts) or 'none'


def dump_lines(data: bytes) -> List[str]:
    v = Bink(data)
    if v.file_size != len(data):
        raise EBink('header says {} bytes, file has {}'.format(v.file_size, len(data)))
    return header_lines(v)


def dump_lines_from(f: BinaryIO, size: int) -> List[str]:
    return header_lines(bink_from(f, size))


def header_lines(v: Bink) -> List[str]:
    lines = ['Bink revision {}: {} x {}, {} frames at {:.3f} frames/s ({:.2f} s), {} key frame(s)'.format(
        v.revision, v.width, v.height, v.frames, v.fps, v.duration, v.key_frames),
        'file {} bytes, largest frame {} bytes, video flags: {}'.format(v.file_size, v.largest_frame,
                                                                       _flags(v.video_flags, VIDEO_FLAGS)),
        '{} audio track(s)'.format(len(v.tracks))]
    for t in v.tracks:
        lines.append('  track {}: {} Hz, {} channel(s), {}'.format(t['id'], t['rate'], t['channels'],
                                                                   _flags(t['flags'], AUDIO_FLAGS)))
    return lines
