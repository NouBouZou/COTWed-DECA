"""
Fabrique de fichiers FSB5 (sons FMOD 5) pour les tests : .wavc, banques FMOD avec chunk SND (aucun fichier du jeu
n'est versionne).

Le son Vorbis est synthetique : une sinusoide de 440 Hz et un clic toutes les 0,25 s (pour avoir des blocs courts et
longs), 2,2 s, 48 000 Hz mono, encode par libvorbis en qualite 7, comme les sons de COTW (paquet de configuration
c55efa16, celui des dialogues). Commande (2026-10-10) :

    ffmpeg -f lavfi -i "aevalsrc=0.3*sin(440*2*PI*t)+0.9*lt(mod(t\\,0.25)\\,0.002):s=48000:d=2.2" -ac 1
           -c:a libvorbis -q:a 7 -fflags +bitexact -flags:a +bitexact fixture.ogg

VORBIS_AUDIO garde les 147 paquets audio de ce fichier au format FSB5 (u16 taille, paquet). LIBVORBIS_PAGES : granules
ecrites par libvorbis a la fin de ses pages (nombre de paquets du flux, en-tetes compris, granule) : la reconstruction
doit retrouver les memes aux memes paquets ; la derniere (105 600 = 2,2 s) est la longueur du son.
"""
import base64
import struct
from typing import List, Optional, Sequence, Tuple

VORBIS_CRC = 0xc55efa16
VORBIS_LENGTH = 105600
VORBIS_PACKETS = 147
LIBVORBIS_PAGES = [(70, 47872), (137, 95872), (150, 105600)]
VORBIS_AUDIO = base64.b64decode(
    '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'
)

RATE_INDEX = {8000: 1, 11000: 2, 11025: 3, 16000: 4, 22050: 5, 24000: 6, 32000: 7, 44100: 8, 48000: 9}
CHANNEL_CODE = {1: 0, 2: 1, 6: 2, 8: 3}


def sample_header(rate: int, channels: int, offset: int, length: int, chunks: Sequence[Tuple[int, bytes]]) -> bytes:
    raw = (RATE_INDEX[rate] << 1) | (CHANNEL_CODE[channels] << 5) | ((offset // 32) << 7) | (length << 34)
    if chunks:
        raw |= 1
    out = struct.pack('<Q', raw)
    for i, (kind, data) in enumerate(chunks):
        more = 1 if i < len(chunks) - 1 else 0
        out += struct.pack('<I', more | (len(data) << 1) | (kind << 25)) + data
    return out


def fsb5(samples: Sequence[dict], mode: int = 15, names: bool = True) -> bytes:
    """FSB5 version 1. Chaque echantillon : dict(data, rate, channels, length, chunks=[], name=None).
    Les donnees de chaque echantillon commencent a un multiple de 32 octets (completees de zeros)."""
    headers, body = b'', b''
    for s in samples:
        body += b'\0' * (-len(body) % 32)
        headers += sample_header(s['rate'], s['channels'], len(body), s['length'], s.get('chunks', []))
        body += s['data']
    table = b''
    if names:
        offs, texts = [], b''
        for s in samples:
            offs.append(4 * len(samples) + len(texts))
            texts += (s.get('name') or '').encode('utf-8') + b'\0'
        table = struct.pack('<{}I'.format(len(samples)), *offs) + texts
        table += b'\0' * (-len(table) % 4)
    head = b'FSB5' + struct.pack('<6I', 1, len(samples), len(headers), len(table), len(body), mode) + bytes(32)
    return head + headers + table + body


def vorbis_sample(name: Optional[str] = 'sine_click', length: int = VORBIS_LENGTH) -> dict:
    return {'data': VORBIS_AUDIO, 'rate': 48000, 'channels': 1, 'length': length, 'name': name,
            'chunks': [(13, struct.pack('<f', 0.9)), (11, struct.pack('<I', VORBIS_CRC) + bytes(8))]}


def pcm16_sample(name: str = 'ramp', frames: int = 100, channels: int = 2) -> dict:
    data = b''.join(struct.pack('<{}h'.format(channels), *([i * 300 - 15000] * channels)) for i in range(frames))
    return {'data': data, 'rate': 44100, 'channels': channels, 'length': frames, 'name': name}


def wavc(fsb: bytes) -> bytes:
    """.wavc : u32 taille du FSB5, 12 zeros, le FSB5."""
    return struct.pack('<I', len(fsb)) + bytes(12) + fsb


def bank_with_sounds(fsb: bytes, names=None) -> bytes:
    """Banque FMOD (fmod_fabrique.bank) dont le chunk SND porte ce FSB5, aligne sur 32 octets comme dans le jeu."""
    import fmod_fabrique as FF
    offset = len(FF.bank(names)) + 8              # debut des donnees du chunk SND
    return FF.bank(names, b'\0' * (-offset % 32) + fsb)


def ogg_pages(ogg: bytes) -> List[Tuple[int, int, int, bytes, List[int]]]:
    """(drapeaux, granule, numero, en-tete et corps, tailles des segments) de chaque page d'un flux Ogg."""
    out, p = [], 0
    while p < len(ogg):
        if ogg[p:p + 4] != b'OggS':
            raise ValueError('page Ogg attendue a 0x{:x}'.format(p))
        flags, granule, _serial, seq = ogg[p + 5], *struct.unpack_from('<qII', ogg, p + 6)
        nseg = ogg[p + 26]
        segs = list(ogg[p + 27:p + 27 + nseg])
        end = p + 27 + nseg + sum(segs)
        out.append((flags, granule, seq, ogg[p:end], segs))
        p = end
    return out


def ogg_packets(ogg: bytes) -> Tuple[List[bytes], List[Tuple[int, int]]]:
    """Paquets d'un flux Ogg et (nombre de paquets termines, granule) a la fin de chaque page."""
    packets, ends, cur = [], [], b''
    for _flags, granule, _seq, page, segs in ogg_pages(ogg):
        q = 27 + len(segs)
        for n in segs:
            cur += page[q:q + n]
            q += n
            if n < 255:
                packets.append(cur)
                cur = b''
        ends.append((len(packets), granule))
    return packets, ends
