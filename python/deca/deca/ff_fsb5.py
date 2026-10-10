"""
FSB5 sound banks (FMOD 5) of theHunter: COTW: the .wavc files (DECA type FSB5c: 5 328 named, 1 058 unnamed) and the
SND chunk of the FMOD Studio banks (.fmod_bankc, ff_fmod_bank.py).

COTWed DECA, 2026-10-10, from 11 .wavc samples (dialogues) and 2 banks (gui, marketing_hints): every sample is FSB5
version 1, Vorbis.

  .wavc               : u32 size of the FSB5 that follows, 12 zero bytes, then the FSB5 (to the end of the file)
  FSB5 header (0x3C)  : 'FSB5', u32 version (1; version 0 has one more u32: 0x40 bytes), u32 sample count,
                        u32 size of the sample headers, u32 size of the name table, u32 size of the sound data,
                        u32 mode (codec: 15 = Vorbis), 8 zero bytes, 16-byte hash, 8 bytes
  sample header       : u64, bit 0 a chunk follows, bits 1-4 rate index (9 = 48 000 Hz), bits 5-6 channel code
                        (1, 2, 6, 8 channels), bits 7-33 data offset / 32, bits 34-63 length in samples;
                        then chunks: u32 (bit 0 another follows, bits 1-24 size, bits 25-31 type), the chunk data
  name table          : one u32 offset per sample (from the table start), then the NUL-terminated names
  sound data          : the samples, each from its offset to the next one's (the last one: to the end)

Names: the bank samples carry their source file name (american_alligator_mating_call_01_rvb); a .wavc sample
carries a number ("1758340220", "a1036330222"), not its file name: [hypothesis] an ID of the dialogue pipeline.

Vorbis (mode 15): each sample is a sequence of (u16 size, packet) audio packets, without the three Vorbis header
packets. Chunk 11 (VORBISDATA) starts with the CRC-32 of the encoder's setup packet, found in the table of
ff_fsb5_vorbis.py; the identification packet is rebuilt from the sample (channels, rate, block sizes 256 and 2048 as
the FMOD encoder writes them: python-fsb5 and vgmstream do the same) and the comment packet is empty. Granule
positions follow the Vorbis rule: each packet after the first adds (previous block + this block) / 4 samples; the
block size of a packet is that of its mode, read from the end of the setup packet as libavcodec's vorbis_parser does.
The last page's granule is the FSB sample length, so decoders cut the end padding.
"""
import struct
import zlib
from typing import List, Optional, Tuple

MAGIC = b'FSB5'
WAVC_HEADER = 16

MODES = {0: 'none', 1: 'PCM8', 2: 'PCM16', 3: 'PCM24', 4: 'PCM32', 5: 'PCM float', 6: 'GC ADPCM', 7: 'IMA ADPCM',
         8: 'VAG', 9: 'HE-VAG', 10: 'XMA', 11: 'MPEG', 12: 'CELT', 13: 'AT9', 14: 'XWMA', 15: 'Vorbis',
         16: 'FADPCM', 17: 'Opus'}
MODE_VORBIS = 15
MODE_MPEG = 11
PCM_WIDTHS = {1: 1, 2: 2, 3: 3, 4: 4, 5: 4}          # mode -> bytes per sample (5: 32-bit float)
FREQUENCIES = {0: 4000, 1: 8000, 2: 11000, 3: 11025, 4: 16000, 5: 22050, 6: 24000, 7: 32000, 8: 44100, 9: 48000,
               10: 96000}
CHANNELS = {0: 1, 1: 2, 2: 6, 3: 8}
CHUNK_CHANNELS, CHUNK_FREQUENCY, CHUNK_LOOP, CHUNK_VORBIS, CHUNK_PEAK = 1, 2, 3, 11, 13
CHUNK_NAMES = {1: 'channels', 2: 'frequency', 3: 'loop', 4: 'comment', 6: 'XMA seek', 7: 'DSP coefficients',
               9: 'ATRAC9 config', 10: 'XWMA data', 11: 'Vorbis data', 13: 'peak volume', 14: 'Vorbis layers',
               15: 'Opus data size'}

VORBIS_BLOCKSIZES = (256, 2048)


class EFsb5(Exception):
    pass


class Sample:
    __slots__ = ('index', 'name', 'frequency', 'channels', 'length', 'data_offset', 'data_size', 'chunks')

    def __init__(self, index, frequency, channels, length, data_offset, chunks):
        self.index, self.frequency, self.channels, self.length = index, frequency, channels, length
        self.data_offset, self.chunks = data_offset, chunks
        self.name: Optional[str] = None
        self.data_size = 0

    @property
    def duration(self) -> float:
        return self.length / self.frequency if self.frequency else 0.0

    @property
    def loop(self) -> Optional[Tuple[int, int]]:
        raw = self.chunk(CHUNK_LOOP)
        return struct.unpack_from('<II', raw, 0) if raw is not None and len(raw) >= 8 else None

    @property
    def vorbis_crc(self) -> Optional[int]:
        raw = self.chunk(CHUNK_VORBIS)
        return struct.unpack_from('<I', raw, 0)[0] if raw is not None and len(raw) >= 4 else None

    def chunk(self, kind: int) -> Optional[bytes]:
        return next((c for t, c in self.chunks if t == kind), None)


class Fsb5:
    """An FSB5 at `offset` of `data` (16 for a .wavc, the start of the SND data for a bank).

    `available`: bytes of the FSB5 in its container when `data` holds only the headers and the name table (the
    Structure view of a 100 MB bank); the sound data can then not be read."""

    def __init__(self, data: bytes, offset: int = 0, available: Optional[int] = None):
        self.data, self.offset = data, offset
        if data[offset:offset + 4] != MAGIC:
            raise EFsb5('no FSB5 magic at 0x{:x}'.format(offset))
        if len(data) < offset + 0x3C:
            raise EFsb5('FSB5 header cut')
        (self.version, count, self.headers_size, self.names_size, self.data_size,
         self.mode) = struct.unpack_from('<6I', data, offset + 4)
        if self.version not in (0, 1):
            raise EFsb5('FSB5 version {} not handled'.format(self.version))
        self.header_size = 0x40 if self.version == 0 else 0x3C
        self.size = self.header_size + self.headers_size + self.names_size + self.data_size
        self.complete = available is None
        if available is None:
            available = len(data) - offset
        elif len(data) < offset + self.header_size + self.headers_size + self.names_size:
            raise EFsb5('FSB5 headers cut')
        if self.size > available:
            raise EFsb5('FSB5 of {} bytes beyond the end of the data ({} bytes from 0x{:x})'.format(
                self.size, available, offset))
        self.samples: List[Sample] = []
        p, end = offset + self.header_size, offset + self.header_size + self.headers_size
        for i in range(count):
            if p + 8 > end:
                raise EFsb5('sample header {} beyond the sample headers'.format(i))
            raw = struct.unpack_from('<Q', data, p)[0]
            p += 8
            more = raw & 1
            frequency = FREQUENCIES.get((raw >> 1) & 0xF)
            channels = CHANNELS[(raw >> 5) & 3]
            data_offset = ((raw >> 7) & 0x7FFFFFF) * 32
            length = raw >> 34
            chunks = []
            while more:
                if p + 4 > end:
                    raise EFsb5('chunk of sample {} beyond the sample headers'.format(i))
                c = struct.unpack_from('<I', data, p)[0]
                more, size, kind = c & 1, (c >> 1) & 0xFFFFFF, c >> 25
                if p + 4 + size > end:
                    raise EFsb5('chunk {} of sample {} beyond the sample headers'.format(kind, i))
                chunks.append((kind, data[p + 4:p + 4 + size]))
                p += 4 + size
            s = Sample(i, frequency, channels, length, data_offset, chunks)
            if s.chunk(CHUNK_FREQUENCY) is not None:
                s.frequency = struct.unpack_from('<I', s.chunk(CHUNK_FREQUENCY), 0)[0]
            if s.chunk(CHUNK_CHANNELS) is not None:
                s.channels = s.chunk(CHUNK_CHANNELS)[0]
            if s.frequency is None:
                raise EFsb5('sample {}: rate index {} unknown, no frequency chunk'.format(i, (raw >> 1) & 0xF))
            self.samples.append(s)
        if self.names_size:
            nt = end
            for s in self.samples:
                o = struct.unpack_from('<I', data, nt + 4 * s.index)[0]
                q = data.find(b'\0', nt + o, nt + self.names_size)
                s.name = data[nt + o:q if q >= 0 else nt + self.names_size].decode('utf-8', 'replace')
        ends = [s.data_offset for s in self.samples[1:]] + [self.data_size]
        for s, e in zip(self.samples, ends):
            if not 0 <= s.data_offset <= e <= self.data_size:
                raise EFsb5('sample {}: data 0x{:x}-0x{:x} outside the {} bytes of sound data'.format(
                    s.index, s.data_offset, e, self.data_size))
            s.data_size = e - s.data_offset

    @property
    def mode_name(self) -> str:
        return MODES.get(self.mode, 'mode {}'.format(self.mode))

    def sample_data(self, s: Sample) -> bytes:
        if not self.complete:
            raise EFsb5('FSB5 read without its sound data')
        start = self.offset + self.header_size + self.headers_size + self.names_size + s.data_offset
        return self.data[start:start + s.data_size]

    def extension(self) -> str:
        """Extension of the files sample_file writes: ogg, wav, mp3; fsb when the codec is not rebuilt."""
        if self.mode == MODE_VORBIS:
            return 'ogg'
        if self.mode in PCM_WIDTHS:
            return 'wav'
        if self.mode == MODE_MPEG:
            return 'mp3'
        return 'fsb'

    def sample_file(self, s: Sample) -> bytes:
        """The sample as a file a player opens: Ogg Vorbis, WAV (PCM) or MP3 (the frames as stored)."""
        if self.mode == MODE_VORBIS:
            return vorbis_ogg(self.sample_data(s), s.channels, s.frequency, s.length, s.vorbis_crc)
        if self.mode in PCM_WIDTHS:
            return pcm_wav(self.sample_data(s), self.mode, s.channels, s.frequency, s.length)
        if self.mode == MODE_MPEG:
            return self.sample_data(s)
        raise EFsb5('codec {} not rebuilt (export the .fsb and use vgmstream)'.format(self.mode_name))

    def raw(self) -> bytes:
        """The FSB5 alone (what vgmstream or FMOD tools open)."""
        if not self.complete:
            raise EFsb5('FSB5 read without its sound data')
        return self.data[self.offset:self.offset + self.size]

    def file_names(self) -> List[str]:
        """One file name per sample for an export folder: the sample name made safe for Windows, '~<index>' added
        to a name already taken (a bank can hold the same name twice), '<index>' for a sample without a name."""
        out, taken = [], set()
        ext = self.extension()
        for s in self.samples:
            base = safe_file_name(s.name) if s.name else '{:04d}'.format(s.index)
            name = base
            if name.lower() in taken:
                name = '{}~{}'.format(base, s.index)
            taken.add(name.lower())
            out.append('{}.{}'.format(name, ext))
        return out


_UNSAFE = set('<>:"/\\|?*')


def safe_file_name(name: str) -> str:
    cleaned = ''.join('_' if (c in _UNSAFE or ord(c) < 32) else c for c in name).strip(' .')
    return cleaned or '_'


# -- .wavc ------------------------------------------------------------------------------------------------------

def is_wavc(head: bytes) -> bool:
    return len(head) >= 20 and head[16:20] == MAGIC and head[4:16] == b'\0' * 12


def wavc(data: bytes) -> Fsb5:
    """The FSB5 of a .wavc; its 16-byte header (u32 size, 12 zeros) is checked against the file."""
    if not is_wavc(data):
        raise EFsb5('not a .wavc (16-byte header then FSB5)')
    size = struct.unpack_from('<I', data, 0)[0]
    fsb = Fsb5(data, WAVC_HEADER)
    if size != fsb.size or WAVC_HEADER + size != len(data):
        raise EFsb5('.wavc header says {} bytes, FSB5 has {}, file has {} after the header'.format(
            size, fsb.size, len(data) - WAVC_HEADER))
    return fsb


# -- Vorbis -> Ogg --------------------------------------------------------------------------------------------

def _rev_bits(b: int) -> int:
    return int('{:08b}'.format(b)[::-1], 2)


_REV = bytes(_rev_bits(i) for i in range(256))


def ogg_crc(data: bytes) -> int:
    """Ogg page CRC (polynomial 04C11DB7, MSB first, init 0, no final xor), computed with zlib's reflected CRC-32:
    reflected CRC of the bit-reversed bytes, init and final xor cancelled by the CRC of as many zero bytes."""
    r = zlib.crc32(data.translate(_REV)) ^ zlib.crc32(bytes(len(data)))
    return int('{:032b}'.format(r)[::-1], 2)


class _BitReaderMsb:
    """Reads a byte string MSB first (libavcodec's get_bits), used on the reversed setup packet."""

    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def left(self) -> int:
        return len(self.data) * 8 - self.pos

    def bits(self, n: int) -> int:
        v = 0
        for _ in range(n):
            byte = self.data[self.pos >> 3]
            v = (v << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v


def vorbis_mode_blockflags(setup: bytes) -> List[int]:
    """Block flag (0 short, 1 long) of each mode, read backwards from the end of the setup packet (the modes are
    its last field: per mode blockflag 1 bit, windowtype 16, transformtype 16, mapping 8, then the framing bit).
    Same method as libavcodec/vorbis_parser.c."""
    if len(setup) < 7 or setup[0] != 5 or setup[1:7] != b'vorbis':
        raise EFsb5('not a Vorbis setup packet')
    rev = setup[::-1]
    gb = _BitReaderMsb(rev)
    framing = 0
    while gb.left() > 97:
        if gb.bits(1):
            framing = gb.pos
            break
    if not framing:
        raise EFsb5('Vorbis setup packet: no framing bit')
    count = last = 0
    while gb.left() >= 97:
        if gb.bits(8) > 63 or gb.bits(16) or gb.bits(16):
            break
        gb.bits(1)
        count += 1
        if count > 64:
            break
        save = gb.pos
        if gb.bits(6) + 1 == count:
            last = count
        gb.pos = save
    if not last:
        raise EFsb5('Vorbis setup packet: modes not found')
    gb = _BitReaderMsb(rev)
    gb.pos = framing
    flags = [0] * last
    for i in range(last - 1, -1, -1):
        gb.bits(40)
        flags[i] = gb.bits(1)
    return flags


def _ilog(v: int) -> int:
    n = 0
    while v:
        n += 1
        v >>= 1
    return n


def vorbis_id_packet(channels: int, rate: int) -> bytes:
    exps = (VORBIS_BLOCKSIZES[1].bit_length() - 1) << 4 | (VORBIS_BLOCKSIZES[0].bit_length() - 1)
    return b'\x01vorbis' + struct.pack('<IBIiiiBB', 0, channels, rate, 0, 0, 0, exps, 1)


def vorbis_comment_packet(vendor: bytes = b'COTWed DECA (FSB5 rebuild)') -> bytes:
    return b'\x03vorbis' + struct.pack('<I', len(vendor)) + vendor + struct.pack('<I', 0) + b'\x01'


def vorbis_packets(data: bytes) -> List[bytes]:
    """Audio packets of an FSB5 Vorbis sample: (u16 size, packet) until a zero size or the end (padding)."""
    out, p = [], 0
    while p + 2 <= len(data):
        n = struct.unpack_from('<H', data, p)[0]
        if n == 0:
            break
        if p + 2 + n > len(data):
            raise EFsb5('Vorbis packet of {} bytes at 0x{:x} beyond the sample'.format(n, p))
        out.append(data[p + 2:p + 2 + n])
        p += 2 + n
    return out


class OggWriter:
    """Ogg pages of one logical stream: packets are laced into pages of about 4 KB (a packet may span pages)."""

    def __init__(self, serial: int = 1):
        self.serial, self.seq, self.out = serial, 0, bytearray()
        self._segs: List[int] = []
        self._body = bytearray()
        self._granule = -1
        self._page_continued = False     # the open page starts with the end of a packet begun on the previous one
        self._bos = True

    def _flush(self, eos: bool = False):
        if not self._segs and not eos:
            return
        flags = (1 if self._page_continued else 0) | (2 if self._bos else 0) | (4 if eos else 0)
        head = struct.pack('<4sBBqIIIB', b'OggS', 0, flags, self._granule, self.serial, self.seq, 0,
                           len(self._segs)) + bytes(self._segs)
        page = bytearray(head + self._body)
        struct.pack_into('<I', page, 22, ogg_crc(bytes(page)))
        self.out += page
        self.seq += 1
        self._bos = False
        self._segs, self._body, self._granule, self._page_continued = [], bytearray(), -1, False

    def packet(self, packet: bytes, granule: int, flush: bool = False, eos: bool = False):
        """Add a packet; its granule is written on the page where it ends. flush: end the page after it."""
        lacing = [255] * (len(packet) // 255) + [len(packet) % 255]
        pos = 0
        while lacing:
            room = 255 - len(self._segs)
            take = lacing[:room]
            lacing = lacing[room:]
            n = sum(take)
            self._segs += take
            self._body += packet[pos:pos + n]
            pos += n
            if lacing:              # page full in the middle of the packet: it continues on the next page
                self._flush()
                self._page_continued = True
        self._granule = granule
        if flush or eos or len(self._body) >= 4096:
            self._flush(eos)

    def bytes(self) -> bytes:
        return bytes(self.out)


def vorbis_granules(packets: List[bytes], setup: bytes) -> List[int]:
    """Granule position after each audio packet (the Vorbis rule, block sizes from the setup packet's modes)."""
    flags = vorbis_mode_blockflags(setup)
    mode_bits = _ilog(len(flags) - 1)
    granules, granule, prev = [], 0, 0
    for i, pk in enumerate(packets):
        if not pk or pk[0] & 1:
            raise EFsb5('packet {} is not an audio packet'.format(i))
        mode = (pk[0] >> 1) & ((1 << mode_bits) - 1) if mode_bits else 0
        if mode >= len(flags):
            raise EFsb5('packet {}: mode {} of {}'.format(i, mode, len(flags)))
        block = VORBIS_BLOCKSIZES[flags[mode]]
        if prev:
            granule += (prev + block) // 4
        prev = block
        granules.append(granule)
    return granules


def vorbis_ogg(data: bytes, channels: int, rate: int, length: int, crc: Optional[int]) -> bytes:
    from .ff_fsb5_vorbis import setup_packet
    if crc is None:
        raise EFsb5('Vorbis sample without Vorbis data chunk (CRC of its setup packet)')
    setup = setup_packet(crc)
    if setup is None:
        raise EFsb5('Vorbis setup packet {:08x} unknown (not in ff_fsb5_vorbis.py)'.format(crc))
    packets = vorbis_packets(data)
    if not packets:
        raise EFsb5('Vorbis sample without audio packet')
    granules = vorbis_granules(packets, setup)
    # End trimming: the last page says the stream ends at the FSB length. Its packets must produce at least the
    # samples to cut, and it must not also be the first audio page (a first page whose granule is smaller than its
    # samples reads as a start offset): the packets that cover the excess go on a page of their own.
    tail = len(packets)
    excess = granules[-1] - length if 0 < length < granules[-1] else 0
    if excess:
        tail = len(packets) - 1
        while tail > 1 and granules[-1] - granules[tail - 1] < excess:
            tail -= 1
    ogg = OggWriter()
    ogg.packet(vorbis_id_packet(channels, rate), 0, flush=True)
    ogg.packet(vorbis_comment_packet(), 0)
    ogg.packet(setup, 0, flush=True)
    for i, pk in enumerate(packets):
        last = i == len(packets) - 1
        ogg.packet(pk, length if last and excess else granules[i], flush=(i == tail - 1), eos=last)
    return ogg.bytes()


# -- PCM -> WAV ----------------------------------------------------------------------------------------------------

def pcm_wav(data: bytes, mode: int, channels: int, rate: int, length: int) -> bytes:
    width = PCM_WIDTHS[mode]
    body = data[:length * width * channels] if length else data
    if mode == 1:                     # FSB PCM8 is signed, WAV 8-bit is unsigned
        body = bytes((b + 128) & 0xFF for b in body)
    fmt_tag = 3 if mode == 5 else 1
    fmt = struct.pack('<HHIIHH', fmt_tag, channels, rate, rate * channels * width, channels * width, width * 8)
    chunks = b'fmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(body)) + body
    if len(body) & 1:
        chunks += b'\0'
    return b'RIFF' + struct.pack('<I', 4 + len(chunks)) + b'WAVE' + chunks


# -- text --------------------------------------------------------------------------------------------------------

def _duration(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    return '{:d}:{:06.3f}'.format(int(m), s)


def dump_lines(fsb: Fsb5, title: str = 'FSB5', limit: Optional[int] = None) -> List[str]:
    lines = ['{} version {}, {}, {} sample(s), sound data {} bytes'.format(
        title, fsb.version, fsb.mode_name, len(fsb.samples), fsb.data_size)]
    if fsb.mode == MODE_VORBIS:
        from .ff_fsb5_vorbis import setup_packet
        missing = sorted({s.vorbis_crc for s in fsb.samples if s.vorbis_crc is None or setup_packet(s.vorbis_crc) is None},
                         key=lambda c: -1 if c is None else c)
        lines.append('export: Ogg Vorbis' + ('' if not missing else ' (setup packet unknown for {}: those '
                                                                    'samples export as .fsb)'.format(
            ', '.join('none' if c is None else '{:08x}'.format(c) for c in missing))))
    else:
        lines.append('export: {}'.format(fsb.extension()))
    total = sum(s.duration for s in fsb.samples)
    lines.append('total duration {}'.format(_duration(total)))
    lines.append('')
    shown = fsb.samples if limit is None else fsb.samples[:limit]
    for s in shown:
        extra = []
        if s.loop is not None:
            extra.append('loop {}-{}'.format(*s.loop))
        for t, c in s.chunks:
            if t in (CHUNK_VORBIS, CHUNK_LOOP):
                continue
            if t == CHUNK_PEAK and len(c) == 4:
                extra.append('peak volume {:.3f}'.format(struct.unpack('<f', c)[0]))
            else:
                extra.append(CHUNK_NAMES.get(t, 'chunk {}'.format(t)))
        if s.vorbis_crc is not None:
            extra.append('setup {:08x}'.format(s.vorbis_crc))
        lines.append('[{:4d}] {}  {} Hz, {} ch, {} samples ({}), {} bytes{}'.format(
            s.index, s.name if s.name is not None else '(no name)', s.frequency, s.channels, s.length,
            _duration(s.duration), s.data_size, ('  ' + ', '.join(extra)) if extra else ''))
    if limit is not None and len(fsb.samples) > limit:
        lines.append('  ... {} more'.format(len(fsb.samples) - limit))
    return lines


def wavc_dump_lines(data: bytes) -> List[str]:
    fsb = wavc(data)
    return ['.wavc: 16-byte header (FSB5 size {}), then:'.format(fsb.size)] + dump_lines(fsb)
