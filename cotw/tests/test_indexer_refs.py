#!/usr/bin/env python3
"""
Tests de cotw/indexer_refs.py : recherche texte, hashes bruts (alignes, non alignes, en limite de bloc,
48 et 64 bits), RTPC par structure, univers des objets, puis index complet d'un faux projet (vrai scan
DECA, deux processus, reprise).

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_indexer_refs.py
"""
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401
import indexer_refs as I  # noqa: E402
import scenario_registre as S  # noqa: E402
import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import k_type_u32, k_type_str  # noqa: E402
from deca.hashes import hash48_func  # noqa: E402

NAME = S.AVAILABLE[0]
ICON = 'textures/ui/items/ammo/' + NAME + '_alpha.ddsc'


def matcher():
    return I.Matcher([I.Ident(1, NAME, 'ammo', 'nom', NAME), I.Ident(2, NAME, 'ammo', 'display_icon', ICON)])


def found(hits, encoding):
    return {k[0]: rec for k, rec in hits.data.items() if k[1] == encoding}


class TestText(unittest.TestCase):
    def test_name_in_csv_and_icon_path_with_backslashes(self):
        data = (b'\x00\x01' + NAME.encode() + b',ammo_cal_243,ammunition\x00'
                + ICON.replace('/', '\\').encode() + b'\x00')
        hits = I.FileHits()
        matcher().match_text(data, hits)
        text = found(hits, 'texte')
        self.assertEqual(text[1][0], 2)   # nom : dans la liste CSV et comme segment du chemin de l'icone
        self.assertEqual(text[2][0], 1)   # chemin complet de l'icone

    def test_longer_identifier_is_not_the_name(self):
        hits = I.FileHits()
        matcher().match_text(b'xx equipment_ammo_243_sp_011 yy', hits)
        self.assertEqual(found(hits, 'texte'), {})


class TestDecimal(unittest.TestCase):
    def test_hash32_written_in_digits(self):
        data = b'id=' + str(S.h32(NAME)).encode() + b';x=12345678901;'
        hits = I.FileHits()
        n = matcher().match_text(data, hits)
        self.assertEqual(found(hits, 'decimal')[1][1], [3])
        self.assertEqual(n, 1)   # 12345678901 a 11 chiffres : ignore

    def test_decimal_string_in_rtpc(self):
        node = W.make_node('secondary_ids', [W.make_prop(0xa4f0837b, k_type_str, str(S.h32(NAME)).encode())])
        tree = W.rtpc_from_bytes(S.rtpc_bytes(W.make_node(0, [], [node])))
        hits = I.FileHits()
        matcher().match_rtpc(tree, hits)
        self.assertIn('ecrit en chiffres', found(hits, 'decimal')[1][2][0])


class TestRaw(unittest.TestCase):
    def test_aligned_and_unaligned_u32(self):
        h = S.h32(NAME)
        data = bytearray(64)
        data[8:12] = struct.pack('<I', h)
        data[21:25] = struct.pack('<I', h)
        hits = I.FileHits()
        counts = matcher().match_raw(bytes(data), hits)
        self.assertEqual(found(hits, 'hash32')[1][1], [8])
        self.assertEqual(counts['n32'], 16)
        hits = I.FileHits()
        matcher().match_raw(bytes(data), hits, unaligned=True)
        self.assertEqual(sorted(found(hits, 'hash32')[1][1]), [8, 21])

    def test_chunk_boundary_counted_once(self):
        h = S.h32(NAME)
        n = I.RAW_CHUNK + 4096
        data = bytearray(n)
        for off in (I.RAW_CHUNK - 4, I.RAW_CHUNK, I.RAW_CHUNK + 4):
            data[off:off + 4] = struct.pack('<I', h)
        hits = I.FileHits()
        counts = matcher().match_raw(bytes(data), hits)
        self.assertEqual(found(hits, 'hash32')[1][0], 3)
        self.assertEqual(counts['n32'], n // 4)

    def test_wide_hashes(self):
        h64 = S.h64(NAME)
        h48 = int(hash48_func(NAME.encode()))
        data = bytearray(96)
        data[16:24] = struct.pack('<Q', h64)
        data[32:40] = struct.pack('<Q', h48 | (0xABCD << 48))                      # 48 bits de poids faible
        data[50:56] = struct.pack('<HHH', h48 >> 32, (h48 >> 16) & 0xFFFF, h48 & 0xFFFF)   # lecture DECA
        hits = I.FileHits()
        matcher().match_raw(bytes(data), hits)
        self.assertEqual(found(hits, 'hash64')[1][1], [16])
        self.assertEqual(sorted(found(hits, 'hash48')[1][1]), [32, 50])


class TestRtpc(unittest.TestCase):
    def test_inline_u32_found_where_aligned_raw_scan_misses(self):
        data = S.registry()
        tree = W.rtpc_from_bytes(data)
        m = I.Matcher([I.Ident(i + 1, n, 'ammo', 'nom', n) for i, n in enumerate(S.AVAILABLE)])
        hits = I.FileHits()
        m.match_rtpc(tree, hits)
        self.assertEqual(set(found(hits, 'hash32')), {1, 2, 3})
        detail = found(hits, 'hash32')[1][2][0]
        self.assertIn('valeur u32', detail)
        raw = I.FileHits()
        m.match_raw(data, raw)
        self.assertLess(len(found(raw, 'hash32')), 3)  # le balayage aligne en rate : d'ou la lecture par structure

    def test_node_hash_counts_as_citation(self):
        node = W.make_node(NAME, [W.make_prop('x', k_type_u32, 1)])
        tree = W.rtpc_from_bytes(S.rtpc_bytes(W.make_node(0, [], [node])))
        hits = I.FileHits()
        matcher().match_rtpc(tree, hits)
        self.assertIn('hash du noeud', found(hits, 'hash32')[1][2][0])


class TestProject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='cotw_index_')
        cls.project = S.build(cls.tmp.name)
        cls.index = Path(cls.tmp.name) / 'refs.db'

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_1_universe(self):
        vfs = _projet.open_project(self.project)
        idents = I.build_universe(vfs)
        items = {i.item for i in idents}
        self.assertEqual(items, set(S.AVAILABLE + S.ABSENT + [S.WEAPON]))
        roles = {(i.item, i.role) for i in idents}
        self.assertIn((NAME, 'display_icon'), roles)
        self.assertIn((NAME, 'entite'), roles)
        self.assertIn((NAME, 'display_name'), roles)
        self.assertNotIn('Libelle anglais', {i.text for i in idents})   # libelle avec espaces : pas un identifiant
        # nom de la categorie si DECA le connait (vrai jeu), sinon son hash (faux projet minimal)
        self.assertIn({i.category for i in idents if i.item == NAME}, ({'ammo'}, {'0x{:08x}'.format(S.h32('ammo'))}))

    def test_2_index_and_resume(self):
        I.main([self.project, '--index', str(self.index), '--processus', '2'])
        db = sqlite3.connect(str(self.index))

        def items_in(label_like):
            return {r[0] for r in db.execute(
                'SELECT DISTINCT i.item FROM hits h JOIN files f ON f.file_id = h.file_id '
                'JOIN identifiers i ON i.id = h.ident_id WHERE f.label LIKE ?', (label_like,))}
        self.assertEqual(items_in(S.REGISTRY), set(S.AVAILABLE))
        self.assertEqual(items_in(S.UNKNOWN), set(S.AVAILABLE))
        self.assertEqual(items_in('[exe]%'), set(S.AVAILABLE[:2]))
        self.assertEqual(items_in(S.STATS), set(S.AVAILABLE + S.ABSENT[:1]))
        self.assertTrue(set(S.AVAILABLE + S.ABSENT) <= items_in('settings/hp_settings/equipment_data.bin'))
        mode = db.execute('SELECT mode FROM files WHERE label = ?', (S.REGISTRY,)).fetchone()[0]
        self.assertEqual(mode, 'rtpc')
        self.assertEqual(db.execute("SELECT COUNT(*) FROM files WHERE status != 'ok'").fetchone()[0], 0)
        n_files = db.execute('SELECT COUNT(*) FROM files').fetchone()[0]
        db.close()
        I.main([self.project, '--index', str(self.index), '--processus', '1'])   # reprise : rien a relire
        db = sqlite3.connect(str(self.index))
        self.assertEqual(db.execute('SELECT COUNT(*) FROM files').fetchone()[0], n_files)
        db.close()

    def test_3_other_options_refused_without_rebuild(self):
        with self.assertRaises(SystemExit):
            I.main([self.project, '--index', str(self.index), '--non-aligne', '--processus', '1'])
        I.main([self.project, '--index', str(self.index), '--non-aligne', '--processus', '1', '--reconstruire'])


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    unittest.main(verbosity=2)
