#!/usr/bin/env python3
"""
Tests de cotw/adf_remplacer_chaine.py sur des ADF fabriques par adf_minimal.py (relus par le vrai lecteur
ADF de DECA), dont un essai de bout en bout sur un faux projet DECA.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_adf_remplacer_chaine.py
"""
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401
import adf_minimal as AM  # noqa: E402
import adf_remplacer_chaine as AR  # noqa: E402
import citations as CI  # noqa: E402
import faux_projet  # noqa: E402
import resoudre_hashes as RH  # noqa: E402

STATS = 'settings/hp_settings/equipment_stats_ui.bin'
FMJ = 'equipment_ammo_762x54r_fmj_01'
NEW = 'equipment_ammo_243_test_01'


def read_adf(data: bytes):
    from deca.ff_adf import Adf
    from deca.file import ArchiveFile
    a = Adf()
    a.deserialize(ArchiveFile(io.BytesIO(data)))
    return a


class FakeReader:
    """Lecteur sans projet : les ADF de test definissent leurs types eux-memes."""
    class _Db:
        @staticmethod
        def _load_adf(buffer):
            return read_adf(buffer)
    adf_db = _Db()


def leaves(data):
    return {l.path: l for l in CI.adf_leaves(read_adf(data))}


class TestPatch(unittest.TestCase):
    def setUp(self):
        self.data = AM.build([(FMJ, 0x1111, 7), ('equipment_ammo_308_pt_01', 0x2222, 8)], [5])

    def run_patch(self, pairs, data=None):
        data = data or self.data
        adf, base = AR.decode(FakeReader(), data)
        plans = AR.plan_replacements(adf, data, base, pairs)
        patched = AR.apply(data, plans)
        return patched, AR.verify(FakeReader(), data, patched, pairs)

    def test_rename_in_place(self):
        patched, changed = self.run_patch([(FMJ, NEW)])
        self.assertEqual(changed, 1)
        self.assertEqual(len(patched), len(self.data))
        after = leaves(patched)
        self.assertEqual(after['sheet.Rows[0].Name'].value, NEW.encode())
        self.assertEqual(after['sheet.Rows[1].Name'].value, b'equipment_ammo_308_pt_01')
        self.assertEqual(after['sheet.Rows[0].Value'].value, 7)
        header = after['sheet.Rows[0].Name'].header
        self.assertEqual(struct.unpack_from('<I', patched, header + 4)[0], len(NEW))   # longueur mise a jour

    def test_second_field_not_a_length_is_kept(self):
        # cas reel (equipment_stats_ui.bin de COTW) : le second u32 de l'en-tete vaut 8, quelle que soit la chaine
        data = bytearray(self.data)
        header = leaves(bytes(data))['sheet.Rows[0].Name'].header
        struct.pack_into('<I', data, header + 4, 8)
        patched, changed = self.run_patch([(FMJ, NEW)], bytes(data))
        self.assertEqual(changed, 1)
        self.assertEqual(struct.unpack_from('<I', patched, header + 4)[0], 8)
        self.assertEqual(leaves(patched)['sheet.Rows[0].Name'].value, NEW.encode())

    def test_longer_text_refused(self):
        with self.assertRaises(AR.EPatch):
            self.run_patch([('equipment_ammo_308_pt_01', NEW)])   # 26 octets > 24

    def test_unknown_text_refused(self):
        with self.assertRaises(AR.EPatch):
            self.run_patch([('absent_01', 'x')])

    def test_shared_bytes_refused(self):
        # la 2e chaine est le suffixe de la 1re : le jeu peut partager ainsi des octets ; il faut refuser
        data = bytearray(AM.build([('xx_equipment_pt_01', 1, 1), ('pt_01', 2, 2)], []))
        before = leaves(bytes(data))
        first, second = before['sheet.Rows[0].Name'], before['sheet.Rows[1].Name']
        rel_first = struct.unpack_from('<I', data, first.header)[0]
        struct.pack_into('<II', data, second.header, rel_first + len('xx_equipment_'), len('pt_01'))
        self.assertEqual(leaves(bytes(data))['sheet.Rows[1].Name'].value, b'pt_01')
        with self.assertRaises(AR.EPatch):
            self.run_patch([('xx_equipment_pt_01', 'yy_01')], bytes(data))


class TestEndToEnd(unittest.TestCase):
    def test_main_writes_dropzone_file(self):
        with tempfile.TemporaryDirectory(prefix='cotw_adfpatch_') as root:
            data = AM.build([(FMJ, 0x1111, 7), ('equipment_ammo_308_pt_01', 0x2222, 8)], [5])
            project = faux_projet.build_fake_project(root, [(STATS, data)])
            out = Path(root) / 'mod'
            AR.main([project, STATS, '--remplacer', '{}={}'.format(FMJ, NEW), '--sortie', str(out)])
            patched = (out / 'dropzone' / Path(STATS)).read_bytes()
            self.assertEqual(leaves(patched)['sheet.Rows[0].Name'].value, NEW.encode())
            with self.assertRaises(SystemExit):   # deja present, sans --ecraser
                AR.main([project, STATS, '--remplacer', '{}={}'.format(FMJ, NEW), '--sortie', str(out)])


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    unittest.main(verbosity=2)
