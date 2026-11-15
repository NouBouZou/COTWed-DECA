#!/usr/bin/env python3
"""
Tests de cotw/manifestes.py : lecture des game_hash_names<N>.txt du depot de debogage, controle des hashes, export des
noms, verification des archives par SHA-1 ; integrite de resources/deca/hp/manifestes.txt.

Les manifestes fabriques reprennent le format exact du depot 518792 (build 3366163), en-tete compris.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_manifestes.py
"""
import hashlib
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _projet  # noqa: E402,F401
import manifestes as M  # noqa: E402
from deca.hashes import hash32_func  # noqa: E402

HEADER = ('HASH       BYTEOFFSET SIZE       DCOMP SIZE CONTENT DIGEST (SHA1)                     NAME      \n'
          '========== ========== ========== ========== ========================================  ==========\n')


def line(name: str, offset: int, data: bytes, hash_value: int = None) -> str:
    h = hash32_func(name) if hash_value is None else hash_value
    return '0x{:08X} 0x{:08X} 0x{:08X} 0x{:08X} {}  {}\n'.format(h, offset, len(data), len(data),
                                                              hashlib.sha1(data).hexdigest(), name)


class TestManifestes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.depot = root / 'depot'
        self.jeu = root / 'jeu'
        self.depot.mkdir()
        self.jeu.mkdir()
        self.a = b'A' * 100
        self.b = b'B' * 50
        # game0 : deux entrees, la seconde a 4 096 octets (alignement des archives du jeu)
        (self.depot / 'game_hash_names0.txt').write_text(
            HEADER + line('ai/aisystem.aisystunec', 0, self.a) + line('settings/x.bin', 4096, self.b),
            encoding='utf-8')
        # game1 : une entree dont le hash ne correspond pas au nom ; son archive manque dans l'installation
        (self.depot / 'game_hash_names1.txt').write_text(
            HEADER + line('textures/y.ddsc', 0, self.a, hash_value=0x12345678), encoding='utf-8')
        (self.depot / 'archives_digest.txt').write_text('sans rapport\n', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def write_arc(self, second: bytes):
        data = bytearray(self.a) + bytearray(4096 - len(self.a)) + bytearray(second)
        (self.jeu / 'game0.arc').write_bytes(bytes(data))

    def run_cmd(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = M.main(list(argv))
        return code, out.getvalue()

    def test_lecture(self):
        entries = M.read_folder(self.depot)
        self.assertEqual([(e.archive, e.name) for e in entries],
                         [(0, 'ai/aisystem.aisystunec'), (0, 'settings/x.bin'), (1, 'textures/y.ddsc')])
        self.assertEqual(entries[1].offset, 4096)
        self.assertEqual(entries[1].size, 50)
        self.assertEqual([e.name for e in M.check_hashes(entries)], ['textures/y.ddsc'])

    def test_noms(self):
        out = Path(self.tmp.name) / 'noms.txt'
        code, text = self.run_cmd('noms', str(self.depot), '--out', str(out))
        self.assertEqual(code, 1)                                   # une entree discordante
        self.assertEqual(out.read_text(encoding='utf-8').splitlines(), ['ai/aisystem.aisystunec', 'settings/x.bin'])
        self.assertIn('1 discordants exclus', text)

    def test_verifier(self):
        self.write_arc(self.b)
        code, text = self.run_cmd('verifier', str(self.depot), str(self.jeu))
        self.assertIn('identique 2', text)
        self.assertIn('archive absente 1', text)
        self.assertEqual(code, 1)

    def test_verifier_contenu_modifie(self):
        self.write_arc(b'C' * 50)
        code, text = self.run_cmd('verifier', str(self.depot), str(self.jeu))
        self.assertIn('differente : game0 settings/x.bin', text)
        self.assertEqual(code, 1)

    def test_verifier_archive_tronquee(self):
        self.write_arc(b'B' * 10)
        code, text = self.run_cmd('verifier', str(self.depot), str(self.jeu))
        self.assertIn('tronquee : game0 settings/x.bin', text)

    def test_dossier_sans_manifeste(self):
        with self.assertRaises(SystemExit):
            M.read_folder(self.jeu)


class TestFichierDeNoms(unittest.TestCase):
    def test_noms_du_build_3366163(self):
        names = M.NAMES_OUT.read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(names), 89205)
        self.assertEqual(names, sorted(set(names)))
        for name in ('ai/aisystem.aisystunec',
                     'models/hp_weapons/shotgun_ss_410_01/textures/shotgun_ss_410_01_rear_dif.hmddsc'):
            self.assertIn(name, names)
        hashes = {}
        for name in names:
            hashes.setdefault(hash32_func(name), []).append(name)
        collisions = [v for v in hashes.values() if len(v) > 1]
        self.assertEqual(collisions, [['models/hp_manmade/vehicles/car_01/textures/car_01_car_paint_black_dif.hmddsc',
                                       'worlds/world2/terrain/world2/patches/patch_09_60_00.streampatch']])


if __name__ == '__main__':
    unittest.main()
