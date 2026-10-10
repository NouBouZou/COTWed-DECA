#!/usr/bin/env python3
"""
Tests des formats du lot L4 (2026-10-10) : sons FSB5 (.wavc et chunk SND des banques FMOD, reconstruction Ogg Vorbis
et WAV, export), instances de modeles MDI (.nl.mdic / .fl.mdic), physique PFX (.pfxc / .pfs), Scaleform GFx, Bink ;
le routage de l'interface vers leurs onglets (Structure, Havok) sur un faux projet scanne.

Fichiers fabriques ici et dans fsb5_fabrique.py (son Vorbis synthetique encode par libvorbis), havok_fabrique.py
et fmod_fabrique.py : aucun fichier du jeu n'est versionne.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_formats_l4.py [--spawn]
"""
import io
import math
import multiprocessing
import os
import shutil
import sqlite3
import struct
import sys
import tempfile
import time
import unittest
import wave
import zlib
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import _projet  # noqa: E402,F401  (substitut de zugbruecke hors Windows ; niveau module pour "spawn")
import fsb5_fabrique as SF  # noqa: E402
import havok_fabrique as HF  # noqa: E402

import deca.ff_fsb5 as F  # noqa: E402
import deca.ff_mdi as M  # noqa: E402
import deca.ff_gfx as GX  # noqa: E402
import deca.ff_bink as BK  # noqa: E402
import deca.ff_pfx as PX  # noqa: E402
import deca.ff_fmod_bank as B  # noqa: E402
from deca.ff_determine import determine_file_type_and_size  # noqa: E402
from deca.ff_havok import Packfile  # noqa: E402

APP = None


def ogg_crc_reference(data: bytes) -> int:
    """CRC des pages Ogg bit par bit (polynome 04C11DB7, MSB d'abord, init 0) : reference independante."""
    crc = 0
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc


# ---------------------------------------------------------------------------------------------------------
# Fichiers fabriques
# ---------------------------------------------------------------------------------------------------------

def mdi_bytes(unknown=0) -> bytes:
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    rot = (c, 0.0, -s, 0.0, 0.0, 1.0, 0.0, 0.0, s, 0.0, c, 0.0)
    insts = [M.Instance(0, rot + (100.0, 20.0, -50.0, 1.0), 3, (99.0, 20.0, -51.0), (101.0, 23.0, -49.0)),
             M.Instance(1, (2.0, 0, 0, 0, 0, 2.0, 0, 0, 0, 0, 2.0, 0, 5.0, 6.0, 7.0, 1.0), 3, (4, 6, 6), (6, 9, 8)),
             M.Instance(0, (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, -1.0, 0.0, 1.0, 1.0), 3, (-2, 0, 0), (0, 2, 2))]
    return M.mdi_bytes(['models/test/crate_01.modelc', 'models/test/fence_long_name_02.modelc'], insts, unknown)


def _rect(xmin, xmax, ymin, ymax) -> bytes:
    nbits = max(abs(v) for v in (xmin, xmax, ymin, ymax)).bit_length() + 1      # valeurs signees
    bits = '{:05b}'.format(nbits) + ''.join(format(v & ((1 << nbits) - 1), '0{}b'.format(nbits))
                                            for v in (xmin, xmax, ymin, ymax))
    bits += '0' * (-len(bits) % 8)
    return int(bits, 2).to_bytes(len(bits) // 8, 'big')


def _pstr(text: str) -> bytes:
    raw = text.encode('utf-8')
    return bytes([len(raw)]) + raw


def _tag(code, body: bytes, long=False) -> bytes:
    if long or len(body) >= 63:
        return struct.pack('<HI', (code << 6) | 63, len(body)) + body
    return struct.pack('<H', (code << 6) | len(body)) + body


def gfx_bytes(compressed=True) -> bytes:
    """Petit film GFx : ExporterInfo, image externe et sous-image, import, classes AS3, police, etiquette."""
    tags = [
        _tag(1000, struct.pack('<HIH', 0x401, 0, 13) + b'\x00' + _pstr('test_ui') + b'\x00\x00'),
        _tag(69, struct.pack('<I', 0x19)),
        _tag(9, b'\x00\x00\x00'),
        _tag(1009, struct.pack('<5H', 1, 9, 13, 512, 256) + b'\x00' + _pstr('test_ui_i1.tga')),
        _tag(1008, struct.pack('<6H', 25, 1, 0, 0, 500, 250)),
        _tag(71, b'shared_lib.swf\x00\x01\x00' + struct.pack('<H', 1) + struct.pack('<H', 3) + b'$Button\x00'),
        _tag(75, struct.pack('<HBB', 2, 0x8C, 1) + _pstr('Test Gothic\0') + b'\x00' * 8),
        _tag(82, struct.pack('<I', 1) + b'frame1\x00' + b'\x10\x00\x2e\x00' * 4, long=True),
        _tag(76, struct.pack('<H', 2) + struct.pack('<H', 0) + b'test_ui\x00' + struct.pack('<H', 7) + b'GFX_Card\x00'),
        _tag(43, b'intro\x00'),
        _tag(1, b''),
        _tag(0, b''),
    ]
    body = _rect(0, 1920 * 20, 0, 1080 * 20) + struct.pack('<HH', 30 << 8, 1) + b''.join(tags)
    length = 8 + len(body)
    if not compressed:
        return b'GFX' + bytes([15]) + struct.pack('<I', length) + body
    padded = body + b'\0' * (-(length - 8) % 4096)        # comme le jeu : le zlib donne un multiple de 4 Ko
    return b'CFX' + bytes([43]) + struct.pack('<I', length) + zlib.compress(padded)


def bink_bytes(frames=6, audio=True) -> bytes:
    tracks = 1 if audio else 0
    sizes = [100 + 8 * i for i in range(frames)]          # tailles paires : le bit 0 d'un offset marque l'image cle
    header = 44 + 12 * tracks + 4 * (frames + 1)
    offs, pos = [], header
    for i, n in enumerate(sizes):
        offs.append(pos | (1 if i == 0 else 0))
        pos += n
    offs.append(pos)
    head = b'BIKi' + struct.pack('<10I', pos - 8, frames, max(sizes), frames, 1280, 720, 30000, 1001, 0, tracks)
    if audio:
        head += struct.pack('<HH', 7168, 2) + struct.pack('<HH', 44100, 0x7000) + struct.pack('<I', 0)
    head += struct.pack('<{}I'.format(frames + 1), *offs)
    return head + b''.join(bytes([i]) * n for i, n in enumerate(sizes))


def pfx_bytes() -> bytes:
    return b'PFX\x00' + struct.pack('<3I', 1, 1, 3) + bytes(52) + HF.navmesh_and_physics()


VORBIS_FSB = SF.fsb5([SF.vorbis_sample('alberta_dia_test')])


# ---------------------------------------------------------------------------------------------------------
# FSB5
# ---------------------------------------------------------------------------------------------------------

class TestFsb5(unittest.TestCase):
    def test_wavc_fields(self):
        fsb = F.wavc(SF.wavc(VORBIS_FSB))
        self.assertEqual((fsb.version, fsb.mode_name, len(fsb.samples)), (1, 'Vorbis', 1))
        s = fsb.samples[0]
        self.assertEqual((s.name, s.frequency, s.channels, s.length), ('alberta_dia_test', 48000, 1, SF.VORBIS_LENGTH))
        self.assertEqual(s.vorbis_crc, SF.VORBIS_CRC)
        self.assertEqual(fsb.sample_data(s), SF.VORBIS_AUDIO)
        self.assertAlmostEqual(s.duration, 2.2)
        lines = F.wavc_dump_lines(SF.wavc(VORBIS_FSB))
        self.assertTrue(lines[0].startswith('.wavc: 16-byte header (FSB5 size {})'.format(len(VORBIS_FSB))))
        self.assertIn('export: Ogg Vorbis', lines)
        self.assertTrue(any(line.startswith('[   0] alberta_dia_test  48000 Hz, 1 ch, 105600 samples (0:02.200)')
                            and 'peak volume 0.900' in line and 'setup c55efa16' in line for line in lines), lines)

    def test_setup_table(self):
        from deca.ff_fsb5_vorbis import setup_packet, setup_packets, SHA1
        import hashlib
        import lzma
        import base64
        import deca.ff_fsb5_vorbis as V
        raw = lzma.decompress(base64.b64decode(''.join(V._XZ)))
        self.assertEqual(hashlib.sha1(raw).hexdigest(), SHA1)
        self.assertEqual(len(setup_packets()), 161)
        for crc in (0xc55efa16, 0xa4f666fc):                    # dialogues (mono) et banques (stereo) de COTW
            packet = setup_packet(crc)
            self.assertEqual(packet[:7], b'\x05vorbis')
            self.assertEqual(zlib.crc32(packet), crc)            # la cle est le CRC-32 du paquet
            self.assertEqual(F.vorbis_mode_blockflags(packet), [0, 1])

    def test_granules_as_libvorbis(self):
        """Les granules calculees (blocs de 256 et 2 048, modes lus a la fin du paquet de configuration) sont celles que
        libvorbis a ecrites a la fin de ses pages, blocs courts et longs melanges."""
        from deca.ff_fsb5_vorbis import setup_packet
        packets = F.vorbis_packets(SF.VORBIS_AUDIO)
        self.assertEqual(len(packets), SF.VORBIS_PACKETS)
        g = F.vorbis_granules(packets, setup_packet(SF.VORBIS_CRC))
        blocks = {packets[i][0] >> 1 & 1 for i in range(len(packets))}
        self.assertEqual(blocks, {0, 1})
        for n, granule in SF.LIBVORBIS_PAGES[:-1]:
            self.assertEqual(g[n - 4], granule)                  # n compte les 3 paquets d'en-tete
        self.assertGreater(g[-1], SF.VORBIS_LENGTH)               # la fin est a couper

    def test_ogg_rebuild(self):
        fsb = F.wavc(SF.wavc(VORBIS_FSB))
        ogg = fsb.sample_file(fsb.samples[0])
        pages = SF.ogg_pages(ogg)
        for k, (_flags, _g, seq, page, _segs) in enumerate(pages):
            self.assertEqual(seq, k)
            blank = bytearray(page)
            struct.pack_into('<I', blank, 22, 0)
            self.assertEqual(struct.unpack_from('<I', page, 22)[0], ogg_crc_reference(bytes(blank)))
            self.assertEqual(F.ogg_crc(bytes(blank)), ogg_crc_reference(bytes(blank)))
        self.assertEqual(pages[0][0], 2)                          # debut du flux, paquet d'identification seul
        self.assertEqual(pages[-1][0] & 4, 4)                     # fin du flux
        packets, ends = SF.ogg_packets(ogg)
        self.assertEqual(ends[0], (1, 0))
        self.assertEqual(ends[1], (3, 0))                         # commentaire et configuration, page a part
        self.assertEqual(packets[0], F.vorbis_id_packet(1, 48000))
        self.assertEqual(packets[0][28], 0xB8)                    # blocs 2^8 et 2^11
        self.assertEqual(packets[2][:7], b'\x05vorbis')
        self.assertEqual(packets[3:], F.vorbis_packets(SF.VORBIS_AUDIO))
        self.assertEqual(ends[-1], (SF.VORBIS_PACKETS + 3, SF.VORBIS_LENGTH))   # longueur du FSB : fin coupee
        # la derniere page n'est pas la premiere page audio et ses paquets couvrent l'exces
        self.assertGreater(len(ends), 3)
        from deca.ff_fsb5_vorbis import setup_packet
        g = F.vorbis_granules(packets[3:], setup_packet(SF.VORBIS_CRC))
        first_on_last = ends[-2][0] - 3
        self.assertGreaterEqual(g[-1] - g[first_on_last - 1], g[-1] - SF.VORBIS_LENGTH)

    def test_untrimmed_length(self):
        """Longueur egale ou superieure au calcul : pas de coupe, derniere granule = calcul."""
        from deca.ff_fsb5_vorbis import setup_packet
        full = F.vorbis_granules(F.vorbis_packets(SF.VORBIS_AUDIO), setup_packet(SF.VORBIS_CRC))[-1]
        fsb = F.Fsb5(SF.fsb5([SF.vorbis_sample(length=full)]))
        _packets, ends = SF.ogg_packets(fsb.sample_file(fsb.samples[0]))
        self.assertEqual(ends[-1][1], full)

    def test_pcm_wav(self):
        fsb = F.Fsb5(SF.fsb5([SF.pcm16_sample(frames=100, channels=2)], mode=2))
        self.assertEqual(fsb.extension(), 'wav')
        with wave.open(io.BytesIO(fsb.sample_file(fsb.samples[0]))) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()), (2, 2, 44100, 100))
            first = struct.unpack('<2h', w.readframes(1))
        self.assertEqual(first, (-15000, -15000))

    def test_unknown_setup_and_rejects(self):
        sample = SF.vorbis_sample()
        sample['chunks'] = [(11, struct.pack('<I', 0x12345678))]
        fsb = F.Fsb5(SF.fsb5([sample]))
        with self.assertRaises(F.EFsb5):
            fsb.sample_file(fsb.samples[0])
        self.assertIn('export: Ogg Vorbis (setup packet unknown for 12345678: those samples export as .fsb)',
                      F.dump_lines(fsb))
        data = SF.wavc(VORBIS_FSB)
        for bad in (data[:-1], data[:40], b'\x00' * 16 + b'FSB4' + data[20:]):
            with self.assertRaises(F.EFsb5):
                F.wavc(bad)
        wrong = bytearray(data)
        struct.pack_into('<I', wrong, 0, len(VORBIS_FSB) + 4)
        with self.assertRaises(F.EFsb5):
            F.wavc(bytes(wrong))

    def test_several_samples_and_names(self):
        a = SF.vorbis_sample('click:2?')
        b = SF.vorbis_sample('CLICK_2_')
        c = SF.vorbis_sample(None)
        fsb = F.Fsb5(SF.fsb5([a, b, SF.vorbis_sample('click:2?'), c]))
        self.assertEqual([s.data_offset % 32 for s in fsb.samples], [0] * 4)
        self.assertEqual(fsb.file_names(), ['click_2_.ogg', 'CLICK_2_~1.ogg', 'click_2_~2.ogg', '0003.ogg'])

    def test_determine(self):
        data = SF.wavc(VORBIS_FSB)
        self.assertEqual(determine_file_type_and_size(io.BytesIO(data), len(data), False)[0], 'FSB5c')


class TestBankSounds(unittest.TestCase):
    def setUp(self):
        self.fsb = SF.fsb5([SF.vorbis_sample('american_alligator_call_01'), SF.vorbis_sample('wolf_howl', 50000)])
        self.bank = SF.bank_with_sounds(self.fsb)

    def test_snd_chunk(self):
        start = self.bank.find(b'FSB5')
        self.assertEqual(start % 32, 0)
        fsb = B.sound_fsb(io.BytesIO(self.bank), len(self.bank))
        self.assertFalse(fsb.complete)                          # en-tetes et noms seulement
        self.assertEqual([s.name for s in fsb.samples], ['american_alligator_call_01', 'wolf_howl'])
        with self.assertRaises(F.EFsb5):
            fsb.sample_data(fsb.samples[0])
        full = B.sound_fsb_full(io.BytesIO(self.bank), len(self.bank))
        self.assertEqual(full.raw(), self.fsb)
        import fmod_fabrique as FF
        strings = FF.strings_bank()                              # banque de chaines : pas de chunk SND
        self.assertIsNone(B.sound_fsb(io.BytesIO(strings), len(strings)))

    def test_structure_lines(self):
        lines = B.dump_lines(self.bank)
        i = next(k for k, line in enumerate(lines) if line.startswith('sounds (SND, FSB5 at 0x'))
        self.assertEqual(lines[i + 1], '  FSB5 version 1, Vorbis, 2 sample(s), sound data {} bytes'.format(
            F.Fsb5(self.fsb).data_size))
        self.assertTrue(any('wolf_howl  48000 Hz, 1 ch, 50000 samples' in line for line in lines[i:]))

    def test_rejects_snd_without_fsb5(self):
        import fmod_fabrique as FF
        bad = FF.bank(None, b'\0' * 8 + b'NOTFSB' + b'\0' * 50)
        with self.assertRaises(B.EFmodBank):
            B.sound_fsb(io.BytesIO(bad), len(bad))


class _Logger:
    def __init__(self):
        self.lines = []

    def log(self, s):
        self.lines.append(s)


class _Vfs:
    def __init__(self, data: bytes):
        self.data, self.logger = data, _Logger()

    def file_obj_from(self, node):
        return io.BytesIO(self.data)


class _Node:
    def __init__(self, v_path, size, v_hash=0x1234ABCD):
        self.v_path = None if v_path is None else v_path.encode('utf-8')
        self.size_u, self.v_hash = size, v_hash


class TestExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cotw_l4_export_')
        self.out = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def files(self):
        return sorted(str(p.relative_to(self.out)).replace('\\', '/') for p in Path(self.out).rglob('*') if p.is_file())

    def test_wavc_to_ogg(self):
        from deca.export_import_audio import node_export_fsb5c_processed
        data = SF.wavc(VORBIS_FSB)
        node_export_fsb5c_processed(_Vfs(data), _Node('sound/dialogue/eng/test_dia.wavc', len(data)), self.out)
        node_export_fsb5c_processed(_Vfs(data), _Node(None, len(data)), self.out)
        self.assertEqual(self.files(), ['1234ABCD.dat.DECA.ogg', 'sound/dialogue/eng/test_dia.wavc.DECA.ogg'])
        ogg = (Path(self.out) / 'sound/dialogue/eng/test_dia.wavc.DECA.ogg').read_bytes()
        self.assertEqual(SF.ogg_packets(ogg)[1][-1][1], SF.VORBIS_LENGTH)

    def test_wavc_unknown_setup_to_fsb(self):
        from deca.export_import_audio import node_export_fsb5c_processed
        sample = SF.vorbis_sample()
        sample['chunks'] = [(11, struct.pack('<I', 0x12345678))]
        data = SF.wavc(SF.fsb5([sample]))
        vfs = _Vfs(data)
        node_export_fsb5c_processed(vfs, _Node('sound/x.wavc', len(data)), self.out)
        self.assertEqual(self.files(), ['sound/x.wavc.DECA.fsb'])
        self.assertEqual((Path(self.out) / 'sound/x.wavc.DECA.fsb').read_bytes(), data[16:])
        self.assertTrue(any('bare FSB5 written' in line for line in vfs.logger.lines))

    def test_bank_folder(self):
        from deca.export_import_audio import node_export_fmod_bank_processed
        import fmod_fabrique as FF
        bank = SF.bank_with_sounds(SF.fsb5([SF.vorbis_sample('call_01'), SF.vorbis_sample('call_01')]))
        node_export_fmod_bank_processed(_Vfs(bank), _Node('sound/fmod_banks/animal_test.fmod_bankc', len(bank)),
                                        self.out)
        self.assertEqual(self.files(), ['sound/fmod_banks/animal_test.fmod_bankc.DECA/call_01.ogg',
                                        'sound/fmod_banks/animal_test.fmod_bankc.DECA/call_01~1.ogg'])
        strings = FF.strings_bank()
        vfs = _Vfs(strings)
        node_export_fmod_bank_processed(vfs, _Node('sound/fmod_banks/master_strings.fmod_bankc', len(strings)),
                                        self.out)
        self.assertEqual(len(self.files()), 2)
        self.assertTrue(any('without sounds' in line for line in vfs.logger.lines))


# ---------------------------------------------------------------------------------------------------------
# MDI, GFx, Bink, PFX
# ---------------------------------------------------------------------------------------------------------

class TestMdi(unittest.TestCase):
    def test_round_trip_and_fields(self):
        for unknown in (0, 5):
            data = mdi_bytes(unknown)
            m = M.Mdi(data)
            self.assertEqual(M.mdi_bytes(m.models, m.instances, m.unknown, m.reserved), data)
            self.assertEqual(m.unknown, unknown)
        self.assertEqual(len(data), 0x20 + 80 + 3 * 92)              # 2 chemins de 28 et 38 octets -> 80
        self.assertEqual(m.block_size % 16, 0)
        a = m.instances[0]
        self.assertEqual(a.position, (100.0, 20.0, -50.0))
        self.assertAlmostEqual(a.yaw_degrees, 30.0, places=4)
        self.assertEqual(tuple(round(x, 6) for x in m.instances[1].scale), (2.0, 2.0, 2.0))

    def test_dump(self):
        lines = M.dump_lines(mdi_bytes(5))
        self.assertEqual(lines[0], 'MDI version 13: 2 models, 3 instances (flags 3)')
        self.assertEqual(lines[1], 'header 0x14: u32 5 [unknown], 0x18: 00 00 00 00 00 00 00 00')
        self.assertIn('  [  0]     2 x models/test/crate_01.modelc', lines)
        self.assertTrue(any(line.startswith('  [    1]   1 fence_long_name_02.modelc  pos (5.000, 6.000, 7.000)')
                            and 'scale 2.000' in line for line in lines), lines)
        self.assertEqual(M.dump_lines(mdi_bytes(), limit=1)[-1], '  ... 2 more')

    def test_rejects(self):
        data = mdi_bytes()
        for bad in (data[:-1], data + b'\0', b'MDX' + data[3:], data[:4] + struct.pack('<I', 12) + data[8:]):
            with self.assertRaises(M.EMdi):
                M.Mdi(bad)
        wrong = bytearray(data)
        struct.pack_into('<H', wrong, 0x20 + 80 + 3 * 64, 7)          # modele 7 sur 2
        with self.assertRaises(M.EMdi):
            M.Mdi(bytes(wrong))

    def test_determine(self):
        data = mdi_bytes()
        self.assertEqual(determine_file_type_and_size(io.BytesIO(data), len(data), False)[0], 'mdi')


class TestGfx(unittest.TestCase):
    def test_compressed_and_plain(self):
        for compressed in (True, False):
            g = GX.Gfx(gfx_bytes(compressed))
            self.assertEqual(g.compressed, compressed)
            self.assertEqual((g.frame, g.frame_rate, g.frame_count), ((0, 1920, 0, 1080), 30, 1))
            self.assertEqual(g.exporter()['name'], 'test_ui')
            self.assertEqual(g.external_images(), [{'id': 1, 'extra': 9, 'format': 13, 'width': 512, 'height': 256,
                                                    'export': '', 'file': 'test_ui_i1.tga'}])
            self.assertEqual(g.sub_images(), [(25, 1, (0, 0, 500, 250))])
            self.assertEqual(g.imports(), [('shared_lib.swf', ['$Button'])])
            self.assertEqual(g.symbols(76), [(0, 'test_ui'), (7, 'GFX_Card')])
            self.assertEqual(g.font_names(), ['Test Gothic'])
            self.assertEqual(g.frame_labels(), ['intro'])
            self.assertEqual(g.abc_names(), [('frame1', 16)])

    def test_dump(self):
        lines = GX.dump_lines(gfx_bytes())
        self.assertEqual(lines[0], 'Scaleform GFx (zlib-compressed), SWF version 43, movie {} bytes'.format(
            GX.Gfx(gfx_bytes()).length))
        self.assertEqual(lines[1], 'stage 1920 x 1080, 30 frames/s, 1 frame(s), 12 tags')
        self.assertIn('  test_ui_i1.tga  512x256  format 13  id 1 (u16 9)', lines)
        self.assertIn('  shared_lib.swf: $Button', lines)

    def test_rejects(self):
        data = gfx_bytes(False)
        for bad in (b'SWF' + data[3:], data[:-2], data[:8] + data[8:-2]):
            with self.assertRaises(GX.EGfx):
                GX.Gfx(bad)
        with self.assertRaises(GX.EGfx):
            GX.Gfx(b'CFX\x0f' + data[4:8] + b'not zlib')

    def test_determine(self):
        data = gfx_bytes()
        self.assertEqual(determine_file_type_and_size(io.BytesIO(data), len(data), False)[0], 'gfx')


class TestBink(unittest.TestCase):
    def test_header(self):
        data = bink_bytes()
        v = BK.Bink(data)
        self.assertEqual((v.width, v.height, v.frames, v.key_frames, v.file_size), (1280, 720, 6, 1, len(data)))
        self.assertAlmostEqual(v.fps, 29.97, places=2)
        self.assertEqual(v.tracks, [{'id': 0, 'max_size': 7168, 'channels': 2, 'rate': 44100, 'flags': 0x7000}])
        lines = BK.dump_lines(data)
        self.assertEqual(lines[0], 'Bink revision i: 1280 x 720, 6 frames at 29.970 frames/s (0.20 s), 1 key frame(s)')
        self.assertEqual(lines[-1], '  track 0: 44100 Hz, 2 channel(s), 16-bit, stereo, DCT')
        self.assertEqual(BK.dump_lines_from(io.BytesIO(data), len(data)), lines)
        self.assertEqual(BK.dump_lines(bink_bytes(audio=False))[2], '0 audio track(s)')

    def test_rejects(self):
        data = bink_bytes()
        for bad in (data[:-1], b'KB2j' + data[4:], data[:60]):
            with self.assertRaises(BK.EBink):
                BK.dump_lines(bad)
        with self.assertRaises(BK.EBink):
            BK.dump_lines_from(io.BytesIO(data[:-1]), len(data) - 1)

    def test_determine(self):
        data = bink_bytes()
        self.assertEqual(determine_file_type_and_size(io.BytesIO(data), len(data), False)[0], 'bik')


class TestPfx(unittest.TestCase):
    def test_packfile(self):
        data = pfx_bytes()
        self.assertEqual(PX.header_values(data), (1, 1, 3))
        pf = Packfile(PX.packfile_bytes(data))
        self.assertTrue(pf.objects)
        self.assertEqual(PX.header_lines(data), ['PFX header (0x44 bytes): u32 1, 1, 3; Havok packfile from 0x44'])

    def test_rejects(self):
        data = pfx_bytes()
        with self.assertRaises(PX.EPfx):
            PX.packfile_bytes(b'PFY' + data[3:])
        with self.assertRaises(PX.EPfx):
            PX.packfile_bytes(data[:0x44] + b'\0' * 8 + data[0x4C:])

    def test_determine(self):
        data = pfx_bytes()
        self.assertEqual(determine_file_type_and_size(io.BytesIO(data), len(data), False)[0], 'pfx')


# ---------------------------------------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------------------------------------

FILES = [
    ('sound/dialogue/eng/test_dia_010.wavc', SF.wavc(VORBIS_FSB)),
    ('sound/fmod_banks/animal_test.fmod_bankc', SF.bank_with_sounds(SF.fsb5([SF.vorbis_sample('call_01')]))),
    ('worlds/base/locations/test/outpost_test.nl.mdic', mdi_bytes()),
    ('ui/test_ui.gfx', gfx_bytes()),
    ('movies/test_intro.bik', bink_bytes()),
    ('models/test/crate_01.pfxc', pfx_bytes()),
]


def text_paths(project):
    db = sqlite3.connect(os.path.join(project, 'db', 'core.db'))
    db.execute("UPDATE core_nodes SET v_path = CAST(v_path AS TEXT) WHERE typeof(v_path) = 'blob'")
    db.commit()
    db.close()


def wait_until(cond, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        APP.processEvents()
        if cond():
            return True
        time.sleep(0.05)
    return False


class TestViewers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global APP
        from PySide6.QtWidgets import QApplication
        APP = QApplication.instance() or QApplication([])
        import faux_projet
        cls.tmp = tempfile.TemporaryDirectory(prefix='cotw_l4_gui_')
        cls.project = faux_projet.build_fake_project(cls.tmp.name, FILES)
        text_paths(cls.project)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp.name, ignore_errors=True)

    def setUp(self):
        from deca_gui.main import MainWindow
        self.errors = []
        self.window = MainWindow()
        self.window.error_dialog = self.errors.append
        self.window.show()
        self.window.project_open_path(str(Path(self.project) / 'project.json'))
        self.assertTrue(wait_until(lambda: not self.window.task_running() and self.window.vfs is not None),
                        'ouverture non terminee : {}'.format(self.errors))
        self.dv = self.window.ui.data_view

    def tearDown(self):
        wait_until(lambda: not self.window.task_running(), 120)
        if self.window.vfs is not None:
            self.window.tabs_nodes_clear()
            self.window.vfs.db_conn.close()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def show(self, path):
        uid = _projet.nodes_by_path(self.window.vfs, path)[0].uid
        self.window.vnode_2click_selected([uid])
        APP.processEvents()
        return self.dv.tab_widget.tabText(self.dv.tab_widget.currentIndex())

    def lines(self, tab):
        m = tab.list_view.model()
        return [m.data(m.index(i, 0)) for i in range(m.rowCount())]

    def test_tabs_and_content(self):
        dv = self.dv
        self.assertEqual(self.show('sound/dialogue/eng/test_dia_010.wavc'), 'Structure')
        self.assertTrue(self.lines(dv.tab_structure)[0].startswith('.wavc: 16-byte header'))
        self.assertEqual(self.show('sound/fmod_banks/animal_test.fmod_bankc'), 'Structure')
        self.assertTrue(any(line.startswith('[   0] call_01  48000 Hz') for line in
                            (x.strip() for x in self.lines(dv.tab_structure))))
        self.assertEqual(self.show('worlds/base/locations/test/outpost_test.nl.mdic'), 'Structure')
        self.assertEqual(self.lines(dv.tab_structure)[0], 'MDI version 13: 2 models, 3 instances (flags 3)')
        self.assertEqual(self.show('ui/test_ui.gfx'), 'Structure')
        self.assertTrue(self.lines(dv.tab_structure)[0].startswith('Scaleform GFx (zlib-compressed)'))
        self.assertEqual(self.show('movies/test_intro.bik'), 'Structure')
        self.assertTrue(self.lines(dv.tab_structure)[0].startswith('Bink revision i: 1280 x 720'))
        self.assertEqual(self.show('models/test/crate_01.pfxc'), 'Havok')
        top = dv.tab_havok.tree.model.root.children()
        self.assertTrue(top[0].cols[0].startswith('Havok packfile hk_2014.1.0-r1'), top[0].cols)
        self.assertEqual(dv.errors, [])
        self.assertEqual(self.errors, [])

    def test_types_after_scan(self):
        db = sqlite3.connect(os.path.join(self.project, 'db', 'core.db'))
        rows = dict(db.execute('SELECT v_path, file_type FROM core_nodes WHERE v_path IS NOT NULL').fetchall())
        db.close()
        rows = {_projet.to_text(k): _projet.to_text(v) for k, v in rows.items()}
        self.assertEqual({p: rows[p] for p, _d in FILES},
                         {'sound/dialogue/eng/test_dia_010.wavc': 'FSB5c',
                          'sound/fmod_banks/animal_test.fmod_bankc': 'riff',
                          'worlds/base/locations/test/outpost_test.nl.mdic': 'mdi', 'ui/test_ui.gfx': 'gfx',
                          'movies/test_intro.bik': 'bik', 'models/test/crate_01.pfxc': 'pfx'})


if __name__ == '__main__':
    if '--spawn' in sys.argv:
        sys.argv.remove('--spawn')
        multiprocessing.set_start_method('spawn', force=True)
    multiprocessing.freeze_support()
    unittest.main(verbosity=2)
