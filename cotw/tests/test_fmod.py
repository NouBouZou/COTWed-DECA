#!/usr/bin/env python3
"""
Tests des banques FMOD Studio (deca.ff_fmod_bank, 2026-10-09) : arbre des chunks, table des chaines STDT de la
banque de chaines (lecture, et ecriture identique a l'octet), noms des fichiers de banques, vue Structure, noms des
evenements dans la vue des .fmod_guids.

Le nommage des banques au scan et par renommer.py --fmod est teste dans test_renommer.py ; le routage de
l'interface dans test_formats_l2.py. Banques fabriquees par fmod_fabrique.py : aucun fichier du jeu n'est versionne.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_fmod.py
"""
import io
import os
import struct
import sys
import unittest
import uuid
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import _projet  # noqa: E402,F401  (substitut de zugbruecke hors Windows)
import fmod_fabrique as FF  # noqa: E402

import deca.ff_fmod_bank as B  # noqa: E402
import deca.ff_fmod_guids as G  # noqa: E402
from deca.ff_determine import determine_file_type_and_size  # noqa: E402


def table_of(data: bytes) -> B.StringTable:
    return B.string_table_from(io.BytesIO(data), len(data))


class TestStringTable(unittest.TestCase):
    def test_round_trip(self):
        st = table_of(FF.strings_bank())
        self.assertEqual(sorted(st.names), sorted(FF.NAMES))
        self.assertEqual(st.leaf_mismatches, 0)
        self.assertEqual(st.by_guid()[FF.GUID_GRAVEL], 'event:/weapons/bullet_impact/plr_bullet_impact_gravel')
        self.assertEqual(st.kinds(), {'event': 3, 'bus': 3, 'bank': 2, 'vca': 1, 'parameter': 1})
        self.assertEqual(B.string_table_bytes(st.names), st.data)          # ecriture identique a l'octet

    def test_prefix_names_and_case(self):
        """'bus:/' est aussi le prefixe de 'bus:/SFX' : noeud terminal (caractere 0) ; la casse est gardee."""
        st = table_of(FF.strings_bank())
        names = st.by_guid()
        self.assertEqual(names[uuid.UUID('0b0bd674-676b-42b0-bb6f-d0f1490fd686')], 'bus:/')
        self.assertEqual(names[uuid.UUID('962ee1bf-0c72-4ce1-8451-d60f0d478547')], 'bus:/SFX')
        self.assertEqual(names[uuid.UUID('303fca22-565e-4689-9e26-edca63dc7f1d')], 'bus:/SFX/GUI')
        d = st.data
        n_nodes = st.node_count
        chars = [d[8 + 8 * i + 3] for i in range(n_nodes)]
        self.assertIn(ord('s'), chars)                                      # caractere de recherche en minuscule
        self.assertNotIn(ord('S'), chars)
        self.assertEqual(chars.count(0), 3)                                 # racine + terminaux de bus:/ et bus:/SFX

    def test_headers_as_in_the_game(self):
        """En-tetes de master_strings.fmod_bankc : 6 214 noeuds de 8 octets, 4 001 GUID de 16 octets."""
        self.assertEqual(B._array_header(6214, 8), bytes.fromhex('8d300800'))
        self.assertEqual(B._array_header(4001, 16), bytes.fromhex('431f1000'))
        d = table_of(FF.strings_bank()).data
        self.assertEqual(struct.unpack_from('<I', d, 0)[0], 1)
        self.assertEqual(d[8:16], bytes.fromhex('ffffff00') + bytes.fromhex('010000') + bytes([4]))   # racine : b, e, p, v

    def test_guids_sorted_by_first_u32(self):
        st = table_of(FF.strings_bank())
        keys = [struct.unpack('<I', g.bytes_le[:4])[0] for g, _n in st.names]
        self.assertEqual(keys, sorted(keys))

    def test_bank_names(self):
        st = table_of(FF.strings_bank())
        self.assertEqual(st.bank_paths(), ['bank:/animals/specific/animal_test', 'bank:/master/master'])
        self.assertEqual(B.bank_file_name('bank:/animals/specific/animal_alligator'),
                         'sound/fmod_banks/animal_alligator.fmod_bankc')
        self.assertEqual(B.bank_file_name('bank:/weapons/weapons'), 'sound/fmod_banks/weapons.fmod_bankc')

    def test_limits(self):
        many = [(uuid.UUID(int=i + 1), 'event:/{}'.format(chr(0x4e00 + i))) for i in range(300)]
        with self.assertRaises(ValueError):
            B.string_table_bytes(many)


class TestChunks(unittest.TestCase):
    def test_tree_and_versions(self):
        data = FF.strings_bank()
        f = io.BytesIO(data)
        top = B.read_chunks(f, len(data))
        self.assertEqual(top.label, 'RIFF FEV ')
        self.assertEqual([c.label for c in top.children], ['FMT ', 'LIST PROJ'])
        self.assertEqual(B.format_versions(f, top), (125, 119))
        self.assertIsNotNone(top.find('STDT'))
        self.assertTrue(B.is_fmod_bank(data[:12]))

    def test_sound_bank_has_no_string_table(self):
        data = FF.sound_bank()
        self.assertIsNone(table_of(data))
        lines = B.dump_lines(data)
        self.assertTrue(lines[0].startswith('FMOD Studio bank (RIFF FEV ), {} bytes, format version 125'.format(len(data))))
        self.assertTrue(any(line.strip().startswith('SND ') and '46 53 42 35' in line for line in lines), lines)

    def test_dump_lines(self):
        lines = B.dump_lines(FF.strings_bank(), names_limit=3)
        self.assertIn('string table (STDT): 10 names (3 bus, 3 event, 2 bank, 1 parameter, 1 vca), 17 trie nodes',
                      lines)
        self.assertEqual(lines[-1], '  ... 7 more')
        self.assertIn('  {b0079de6-6220-4e07-a7d1-2570be0371d9}  bank:/animals/specific/animal_test', lines)
        self.assertTrue(any('LIST EVTS' in line and '(empty)' in line for line in lines))

    def test_rejects(self):
        with self.assertRaises(B.EFmodBank):
            B.read_chunks(io.BytesIO(b'RIFX' + b'\0' * 20), 24)
        data = bytearray(FF.strings_bank())
        struct.pack_into('<I', data, 4, len(data) * 2)                     # taille RIFF au-dela du fichier
        with self.assertRaises(B.EFmodBank):
            B.read_chunks(io.BytesIO(bytes(data)), len(data))
        stdt = table_of(FF.strings_bank()).data
        for bad in (stdt[:-1], stdt[:20], stdt + b'\0'):
            with self.assertRaises(B.EFmodBank):
                B.StringTable(bad)

    def test_determine(self):
        data = FF.strings_bank()
        self.assertEqual(determine_file_type_and_size(io.BytesIO(data), len(data), False)[0], 'riff')


class TestViews(unittest.TestCase):
    def test_structure_lines(self):
        from deca_gui.viewer_structure import structure_lines
        lines = structure_lines('riff', FF.strings_bank())
        self.assertTrue(lines[0].startswith('FMOD Studio bank'))
        names = table_of(FF.strings_bank()).by_guid()
        guids = structure_lines('fmodguids', G.to_bytes([FF.GUID_GRAVEL, uuid.UUID(int=7)]), None, names)
        self.assertEqual(guids[1], '[  0] {e4be7f4e-866d-4c89-974c-b1ab00c66efe}  '
                                   'event:/weapons/bullet_impact/plr_bullet_impact_gravel')
        self.assertEqual(guids[2], '[  1] {00000000-0000-0000-0000-000000000007}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
