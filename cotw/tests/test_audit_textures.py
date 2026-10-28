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


class TestProjet(unittest.TestCase):
    def test_audit_on_fake_project(self):
        """Faux projet (vrai scan) : deux textures, dont une a blocs D2 ; chaine complete pour toutes."""
        import os
        import sqlite3
        import faux_projet
        files = [('textures/ui/icone.ddsc', T.icone_ddsc(64, 32, 7)),
                 ('textures/ui/d2.ddsc', ddsc(77, bloc_bc3(0x1000, 0x2000, tous(3)) * 4, 8, 8))]
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
        self.assertIn('- **Decodees sans defaut : 2 / 2**', md)
        self.assertIn('| BC3_UNORM (77) | 2 |', md)
        self.assertIn("Pixels que l'ancien decodeur lisait faux : 64", md)
        self.assertIn('- Textures : 2 ; tous niveaux decodes : 2', md)
        self.assertEqual(len(lignes), 3)


if __name__ == '__main__':
    unittest.main()
