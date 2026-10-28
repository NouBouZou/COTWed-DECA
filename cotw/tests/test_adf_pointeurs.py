#!/usr/bin/env python3
"""
Tests des pointeurs ADF (metatype 2) suivis par deca.ff_adf (AdfPointer, 2026-10-28) : cible lue avec le type vise,
pointeur nul, cible hors de l'instance, cycle ; et leurs lecteurs : adf_value_extract, adf_format, adf_dump,
citations.adf_leaves, audit_noms_internes.collecter_adf.

Disposition d'un champ pointeur (comme les chaines et les tableaux, cotw/adf_ecriture.py) :
[u32 cible relative a l'instance][u32 lien de la chaine de relocation].

Usage, depuis la racine du depot, environnement virtuel active :
    python -m unittest cotw\\tests\\test_adf_pointeurs.py
"""
import io
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401
import adf_fabrique as F  # noqa: E402

U32 = 0x075e4e4f
T_ROOT, T_PTR, T_TARGET, T_NODE, T_NODE_PTR = 0x7E590001, 0x7E590002, 0x7E590003, 0x7E590004, 0x7E590005


def lire(data: bytes):
    from deca.ff_adf import Adf
    from deca.file import ArchiveFile
    adf = Adf()
    with ArchiveFile(io.BytesIO(data)) as fp:
        adf.deserialize(fp)
    return adf


def adf_pointeurs() -> bytes:
    """Racine : p -> Target en 0x20 (lien 0x08 vers q), q nul, far hors de l'instance, n -> Node A en 0x30 ;
    A.next -> B en 0x40 ; B.next -> A : cycle."""
    t = F.Types()
    t.struct('Target', T_TARGET, 4, 4, [('x', U32, 4, 0)])
    t.pointer('Target*', T_PTR, T_TARGET)
    t.pointer('Node*', T_NODE_PTR, T_NODE)
    t.struct('Node', T_NODE, 16, 8, [('next', T_NODE_PTR, 8, 0), ('id', U32, 4, 8)])
    t.struct('Root', T_ROOT, 32, 8, [('p', T_PTR, 8, 0), ('q', T_PTR, 8, 8), ('far', T_PTR, 8, 16),
                                     ('n', T_NODE_PTR, 8, 24)])
    data = bytearray(0x50)
    struct.pack_into('<QQQQ', data, 0, (0x08 << 32) | 0x20, 0, 0x10000, 0x30)
    struct.pack_into('<I', data, 0x20, 0x1234)
    struct.pack_into('<QI', data, 0x30, 0x40, 1)        # A
    struct.pack_into('<QI', data, 0x40, 0x30, 2)        # B -> A
    return F.build(t, [('root', T_ROOT, bytes(data))])


class TestLecture(unittest.TestCase):
    def setUp(self):
        self.adf = lire(adf_pointeurs())
        self.root = self.adf.table_instance_full_values[0].value

    def test_followed_null_out_of_range(self):
        from deca.ff_adf import AdfPointer
        p = self.root['p'].value
        self.assertIsInstance(p, AdfPointer)
        self.assertEqual((p.target, p.link, p.cycle), (0x20, 0x08, False))
        self.assertEqual(p.value.value['x'].value, 0x1234)
        base = self.adf.table_instance[0].offset
        self.assertEqual((self.root['p'].info_offset, self.root['p'].data_offset), (base, base + 0x20))
        q = self.root['q'].value
        self.assertEqual((q.target, q.value, q.describe()), (0, None, 'null'))
        far = self.root['far'].value
        self.assertEqual((far.target, far.value), (0x10000, None))
        self.assertIn('not read', far.describe())

    def test_cycle_stops(self):
        a = self.root['n'].value.value
        self.assertEqual(a.value['id'].value, 1)
        b = a.value['next'].value.value
        self.assertEqual(b.value['id'].value, 2)
        retour = b.value['next'].value
        self.assertTrue(retour.cycle)
        self.assertIsNone(retour.value)
        self.assertEqual(retour.target, 0x30)

    def test_extract_and_format(self):
        from deca.ff_adf import adf_value_extract, adf_format
        e = adf_value_extract(self.adf.table_instance_full_values[0])
        self.assertEqual(e['p'], {'x': 0x1234})
        self.assertIsNone(e['q'])
        self.assertEqual(e['n']['next']['id'], 2)

        class Vfs:
            def hash_string_match(self, **_k):
                return []

            def lookup_equipment_from_hash(self, _h):
                return None
        texte = adf_format(self.adf.table_instance_full_values[0], Vfs(), self.adf.extended_map_typedef)
        self.assertIn('-> 0x00000020', texte)
        self.assertIn('4660', texte)            # 0x1234, lu a la cible
        self.assertIn('null', texte)
        self.assertIn('(cycle)', texte)


class TestLecteurs(unittest.TestCase):
    def setUp(self):
        self.adf = lire(adf_pointeurs())

    def test_dump(self):
        import adf_dump
        lignes = adf_dump.format_tree(self.adf, {}, None, 10)
        texte = '\n'.join(lignes)
        self.assertIn('p : Pointer -> 0x00000020', texte)
        self.assertIn('*p : Structure Target', texte)
        self.assertIn('x = 4660', texte)
        self.assertIn('q : Pointer null', texte)
        self.assertIn('next : Pointer -> 0x00000030 (cycle)', texte)

    def test_leaves_skip_pointers(self):
        import citations as CI
        feuilles = {f.path: f.value for f in CI.adf_leaves(self.adf)}
        self.assertEqual(feuilles, {})         # la racine n'a que des pointeurs : leurs cibles ne sont pas rangees

    def test_audit_counts_pointers_once(self):
        import audit_noms_internes as A
        c = A.Collecte()
        A.collecter_adf(self.adf, 'x.adf', c)
        self.assertEqual(c.pointeurs, 4)
        self.assertEqual(c.pointeurs_types[('Target*', '7e590003')], [3, 2, 1])
        self.assertEqual(c.pointeurs_types[('Node*', '7e590004')], [1, 1, 1])
        ex = c.pointeurs_exemples[('Target*', '7e590003')]     # un exemple par fichier, non nul
        self.assertEqual(len(ex), 1)
        self.assertIn(ex[0][1], ('0000000800000020', '0000000000010000'))


if __name__ == '__main__':
    unittest.main()
