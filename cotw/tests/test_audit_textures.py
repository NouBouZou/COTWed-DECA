#!/usr/bin/env python3
"""
Tests de cotw/audit_textures.py (formats, en-tetes, effet du defaut D2, decodage des .ddsc).

Textures fabriquees par cotw/icone.py (aucun fichier du jeu n'est versionne).

Usage, depuis la racine du depot, environnement virtuel active :
    python -m unittest cotw\\tests\\test_audit_textures.py
"""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401
import audit_textures as AT  # noqa: E402
import icone as I  # noqa: E402
import texture_fabrique as T  # noqa: E402


def bloc_bc3(c0: int, c1: int, idx: int) -> bytes:
    return bytes([255, 255]) + bytes(6) + struct.pack('<HHI', c0, c1, idx)


def tous(k: int) -> int:
    return sum(k << (2 * p) for p in range(16))


def ddsc(fmt: int, corps: bytes, w: int = 4, h: int = 4) -> bytes:
    """En-tete AVTX d'un seul niveau w x h, format fmt, suivi du corps donne."""
    tete = bytearray(I.entete(w, h, 1))
    struct.pack_into('<I', tete, 0x08, fmt)
    struct.pack_into('<I', tete, 0x24, len(corps))
    return bytes(tete) + corps


class TestBlocs(unittest.TestCase):
    def test_d2_pixels_counted(self):
        raw = (bloc_bc3(0x1000, 0x2000, tous(3))       # couleur0 < couleur1, index 3 : noir avec l'ancien decodeur
               + bloc_bc3(0x1000, 0x2000, tous(2))     # index 2 : (c0 + c1) / 2 au lieu de (2 c0 + c1) / 3
               + bloc_bc3(0x1000, 0x1000, tous(2))     # c0 == c1, index 2 : meme couleur dans les deux lectures
               + bloc_bc3(0x2000, 0x1000, tous(3)))    # couleur0 > couleur1 : lu juste
        self.assertEqual(AT.blocs_couleur(raw, 77), (4, 3, 32, 0))

    def test_bc1_transparency_counted(self):
        raw = struct.pack('<HHI', 0x0000, 0xFFFF, tous(3)) + struct.pack('<HHI', 0xFFFF, 0x0000, tous(3))
        self.assertEqual(AT.blocs_couleur(raw, 71), (2, 1, 0, 16))


class TestDdsc(unittest.TestCase):
    def test_icon_ok(self):
        data = T.icone_ddsc(64, 32, 7)
        r = AT.auditer_ddsc(data, 'textures/ui/x.ddsc')
        self.assertEqual(r.statut, 'ok', r.detail)
        self.assertEqual((r.format, r.nom_format, r.largeur, r.hauteur, r.niveaux, r.niveaux_fichier), (77, 'BC3_UNORM', 64, 32, 7, 7))
        self.assertEqual((r.octet_06, r.u32_28, r.taille_entete, r.niveaux_decodes), (4, 16, 128, 7))
        self.assertEqual(r.corps_attendu, r.taille_corps)
        self.assertEqual(r.pixels_faux_ancien_bc23, 0)     # l'encodeur de icone.py met toujours couleur0 > couleur1

    def test_d2_in_file(self):
        r = AT.auditer_ddsc(ddsc(77, bloc_bc3(0x1000, 0x2000, tous(3))), 'a.ddsc')
        self.assertEqual((r.statut, r.blocs_couleur, r.blocs_mode3, r.pixels_faux_ancien_bc23), ('ok', 1, 1, 16))

    def test_bc6h_flagged(self):
        r = AT.auditer_ddsc(ddsc(95, bytes(16)), 'ciel.ddsc')
        self.assertEqual((r.statut, r.nom_format), ('bc6h_non_decode', 'BC6H_UF16'))

    def test_structure_checks(self):
        data = T.icone_ddsc(64, 32, 7)
        self.assertEqual(AT.auditer_ddsc(data + bytes(16), 'a.ddsc').statut, 'taille_incoherente')
        self.assertEqual(AT.auditer_ddsc(data[:-16], 'a.ddsc').statut, 'echec')
        self.assertEqual(AT.auditer_ddsc(b'DDS ' + bytes(200), 'a.ddsc').statut, 'entete')
        self.assertEqual(AT.auditer_ddsc(ddsc(1234, bytes(16)), 'a.ddsc').statut, 'format_inconnu')


def ddsc_flux(flux, niveaux=3, dans_fichier=1) -> bytes:
    """BC1 16 x 16, niveaux 16, 8, 4 ; seul le plus petit (4 x 4, 8 octets) dans le .ddsc ; flux 1 a 7 donnes."""
    tete = bytearray(128)
    tete[:0x20] = b'AVTX' + struct.pack('<HBBIHHHHBBHII', 1, 6, 2, 71, 16, 16, 1, 1, niveaux, dans_fichier, 0, 0, 0)
    struct.pack_into('<IIHBB', tete, 0x20, 128, 8, 16, 0, 0)
    for i, f in enumerate(flux):
        struct.pack_into('<IIHBB', tete, 0x2c + 12 * i, *f)
    return bytes(tete) + struct.pack('<HHI', 0xFFFF, 0, 0)


class TestFlux(unittest.TestCase):
    def test_flux_read(self):
        r = AT.auditer_ddsc(ddsc_flux([(0, 32, 16, 0, 1), (32, 128, 16, 0, 1)]), 'a.ddsc')
        self.assertEqual(r.statut, 'ok', r.detail)
        self.assertEqual(r.flux0, '128:8:16:0:0')
        self.assertEqual(r.flux, '0:32:16:0:1 32:128:16:0:1')
        self.assertEqual(r.octets_entete_non_nuls, 7)                    # 4k - 1, k = 2 niveaux externes

    def test_flux_checked_against_levels_and_files(self):
        data = ddsc_flux([(0, 32, 16, 0, 1), (32, 128, 16, 0, 1)])
        r = AT.auditer_ddsc(data, 't/a.ddsc')
        AT.controler_flux(data, r, {'.hmddsc': 160})
        self.assertEqual(r.niveaux_externes, '128 32')
        self.assertEqual(r.fichiers_externes, 'hmddsc=160')
        self.assertEqual(r.flux_controle, 'somme=ok niveaux=ok 1>hmddsc')
        data = ddsc_flux([(0, 32, 16, 0, 2), (0, 128, 16, 0, 1)])
        r = AT.auditer_ddsc(data, 't/a.ddsc')
        AT.controler_flux(data, r, {'.hmddsc': 128, '.atx1': 32})
        self.assertEqual(r.flux_controle, 'somme=ok niveaux=ok 1>hmddsc 2>atx1')
        r = AT.auditer_ddsc(ddsc_flux([(0, 40, 16, 0, 1)]), 't/a.ddsc')
        AT.controler_flux(ddsc_flux([(0, 40, 16, 0, 1)]), r, {})
        self.assertEqual(r.flux_controle, 'somme=faux 1>?')

    def test_deca_reads_stream_table(self):
        """deca.ff_avtx : table des flux lue avec l'en-tete, decrite champ par champ (onglet Image)."""
        import io
        from deca import ff_avtx
        dd = ff_avtx.Ddsc()
        dd.load_ddsc(io.BytesIO(ddsc_flux([(0, 32, 16, 0, 1), (32, 128, 16, 0, 1)])))
        s = dd.header.streams
        self.assertEqual(len(s), 8)
        self.assertEqual((s[0].offset, s[0].size, s[0].alignment, s[0].source), (128, 8, 16, 0))
        self.assertEqual((s[2].offset, s[2].size, s[2].source), (32, 128, 1))
        self.assertTrue(s[3].is_empty())
        lines = ff_avtx.avtx_header_lines(dd.header)
        self.assertEqual(lines[0], 'AVTX v1 : BC1_UNORM (71), 16 x 16, 3 levels (1 in the .ddsc)')
        self.assertEqual(lines[1], '0x06 group 6 (man-made objects, shared, camo) ; 0x07 dimension 3 ; '
                                   '0x12 flags 0x1 (levels in .hmddsc)')
        self.assertEqual(lines[3:], ['stream 0 : .ddsc offset 128 size 8 alignment 16',
                                     'stream 1 : .hmddsc offset 0 size 32 alignment 16',
                                     'stream 2 : .hmddsc offset 32 size 128 alignment 16'])

    def test_external_paths(self):
        self.assertEqual(AT.externes_de('t/a.ddsc', {'t/a.hmddsc': 5, 't/a.atx1': 3, 't/b.atx1': 9}),
                         {'.hmddsc': 5, '.atx1': 3})
        self.assertEqual(AT.externes_de('(sans nom) 1234', {'t/a.hmddsc': 5}), {})


class TestProjet(unittest.TestCase):
    def test_audit_on_fake_project(self):
        """Faux projet (vrai scan) : deux textures, dont une a blocs D2 ; chaine complete pour toutes."""
        import os
        import sqlite3
        import faux_projet
        hm = struct.pack('<HHI', 0x07E0, 0, 0) * 4 + struct.pack('<HHI', 0xF800, 0, 0) * 16   # 8 x 8 puis 16 x 16
        files = [('textures/ui/icone.ddsc', T.icone_ddsc(64, 32, 7)),
                 ('textures/ui/d2.ddsc', ddsc(77, bloc_bc3(0x1000, 0x2000, tous(3)) * 4, 8, 8)),
                 ('textures/t/a.ddsc', ddsc_flux([(0, 32, 16, 0, 1), (32, 128, 16, 0, 1)])),
                 ('textures/t/a.hmddsc', hm)]
        with tempfile.TemporaryDirectory() as d:
            projet = faux_projet.build_fake_project(d, files)
            db = sqlite3.connect(os.path.join(projet, 'db', 'core.db'))
            db.execute('UPDATE core_nodes SET v_path = CAST(v_path AS TEXT) WHERE v_path IS NOT NULL')
            db.commit()
            db.close()
            out = Path(d) / 'audit'
            self.assertEqual(AT.main([projet, '--sortie', str(out), '--processus', '1', '--complet', '-1']), 0)
            md = (out / 'textures.md').read_text(encoding='utf-8')
            lignes = (out / 'textures.csv').read_text(encoding='utf-8-sig').splitlines()
        self.assertIn('- **Decodees sans defaut : 3 / 3**', md)
        self.assertIn('| BC3_UNORM (77) | 2 |', md)
        self.assertIn("Pixels que l'ancien decodeur lisait faux : 64", md)
        self.assertIn('- Textures : 3 ; tous niveaux decodes : 3', md)
        self.assertIn('- Flux 0 = (taille de l\'en-tete, taille du corps, 16, 0, 0) : 3 / 3', md)
        self.assertIn('somme des flux = somme de ces niveaux : 1 ; un flux par niveau, memes tailles : 1', md)
        self.assertIn('`1>hmddsc` x 1', md)
        self.assertEqual(len(lignes), 4)


if __name__ == '__main__':
    unittest.main()
