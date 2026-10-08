#!/usr/bin/env python3
"""
Tests de cotw/generer_noms.py : passes (chiffres, suffixes, dossiers, motifs de l'executable), noms associes,
controle de type, ambiguite, temoin sur cibles fictives, mode hors ligne ; puis enchainement avec renommer.py
sur un faux projet DECA (vrai scan).

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_generer_noms.py [--spawn]
"""
import multiprocessing
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401  (niveau module : les processus spawn reimportent ce fichier)
import generer_noms as G  # noqa: E402
import noms_fichiers as NF  # noqa: E402
import renommer as RN  # noqa: E402
from deca.hashes import hash32_func  # noqa: E402


def h32(s):
    return int(hash32_func(s.encode())) & 0xFFFFFFFF


NOMMES = [
    ('snd/eng/mm100_dia010_010.wavc', 'FSB5c'), ('snd/eng/mm100_dia010_020.wavc', 'FSB5c'),
    ('tex/wood_dif.ddsc', 'avtx'), ('tex/wood_nrm.ddsc', 'avtx'), ('tex/stone_dif.ddsc', 'avtx'),
    ('tex/wood_dif.hmddsc', 'hmddsc'),
    ('w/hp_africa/a_hp_africa.blo', 'rtpc'), ('w/hp_mexico/b.blo', 'rtpc'),
    ('ui/zoom0/00.ddsc', 'avtx'),
]
CIBLES = {
    'snd/eng/mm100_dia010_030.wavc': 'FSB5c',     # chiffres
    'snd/eng/mm100_dia010_180.wavc': 'FSB5c',     # chiffres, largeur 3 (serie)
    'tex/stone_nrm.ddsc': 'avtx',                 # suffixes
    'tex/stone_nrm.hmddsc': None,                 # nom associe (.ddsc -> .hmddsc), type pose d'apres le nom
    'w/hp_mexico/a_hp_mexico.blo': 'rtpc',        # dossiers freres, nom du dossier remplace
    'ui/zoom2/07.ddsc': 'avtx',                   # motif de l'executable
}
FAUX = {'snd/eng/mm100_dia010_040.wavc': 'adf'}   # hash touche, mauvais type : refuse


def etat_test():
    rows = [(h32(n), n, t) for n, t in NOMMES]
    rows += [(h32(n), None, t) for n, t in list(CIBLES.items()) + list(FAUX.items())]
    rows += [(0x0BADF00D, None, 'avtx')]          # sans nom, rien ne le nomme
    return NF.Etat.depuis_lignes(rows)


class TestPieces(unittest.TestCase):
    def test_separer(self):
        self.assertEqual(G.separer('a/b.nl.mdic'), ('a/b', '.nl.mdic'))
        self.assertEqual(G.separer('a.b/c'), ('a.b/c', ''))

    def test_valeurs(self):
        self.assertEqual(len(G._valeurs('07', 2)), 100)
        self.assertIn('07', G._valeurs('07', 2))
        v = G._valeurs('10', 2)
        self.assertIn('5', v)
        self.assertIn('05', v)                     # "dia10" a des voisins "dia05"
        self.assertIn('99', v)
        self.assertIn('42', G._valeurs('5', 1))    # un "5" a des voisins a deux chiffres

    def test_families_width_3_only_for_series_or_large(self):
        deja = set()
        f = G.familles_chiffres(['a/x_100.txt', 'b/y_100.txt', 'b/y_200.txt'], deja)
        self.assertEqual({(a, ap) for a, _g, ap, _e, _l in f}, {('b/y_', '')})
        f = G.familles_chiffres(['c/z_100.txt'], set(), large=lambda ext: ext == '.txt')
        self.assertEqual(len(f), 1)

    def test_associations_follow_deca_rule(self):
        assoc = G.extensions_associees(G._file_assoc_hp())
        self.assertIn('.hmddsc', assoc['.ddsc'])
        self.assertIn('.nl.mdic', assoc['.nl'])

    def test_printf_patterns(self):
        v = list(G.developper_motif('ui/zoom%d/%02d.ddsc'))
        self.assertIn('ui/zoom2/07.ddsc', v)
        self.assertEqual(len(v), 300 * 100)
        self.assertEqual(list(G.developper_motif('a/%s.ddsc')), [])
        self.assertEqual(len(list(G.developper_motif('a/%d.ddsc'))), G.PRINTF_MAX)
        self.assertEqual(len(list(G.developper_motif('m%d/z%d/%d.ddsc'))), 64 ** 3)


class TestGenerer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.etat = etat_test()
        cls.fa = G._file_assoc_hp()
        cls.modele = NF.ModeleTypes(cls.etat, cls.fa)
        cls.ch = G.generer(cls.etat, cls.modele, cls.fa, G.PASSES, [], ['ui/zoom%d/%02d.ddsc'])

    def test_all_targets_found_by_the_expected_pass(self):
        retenus, ambigus = self.ch.resultats()
        trouves = {n: p for n, p in retenus.values()}
        self.assertEqual(set(trouves), set(CIBLES))
        self.assertEqual(trouves['tex/stone_nrm.ddsc'], 'suffixes')
        self.assertEqual(trouves['w/hp_mexico/a_hp_mexico.blo'], 'dossiers')
        self.assertEqual(trouves['ui/zoom2/07.ddsc'], 'exe')
        self.assertTrue(trouves['snd/eng/mm100_dia010_180.wavc'].startswith('chiffres'))
        self.assertEqual(ambigus, {})

    def test_wrong_type_refused(self):
        self.assertEqual([r[0] for r in self.ch.refus], ['snd/eng/mm100_dia010_040.wavc'])

    def test_decoys_find_nothing(self):
        fictif = G.cibles_fictives(self.etat)
        self.assertEqual(len(fictif.sans_nom), len(self.etat.sans_nom))
        self.assertFalse(set(fictif.sans_nom) & set(self.etat.sans_nom))
        t = G.generer(fictif, self.modele, self.fa, G.PASSES, [], ['ui/zoom%d/%02d.ddsc'])
        self.assertEqual(t.resultats(), ({}, {}))

    def test_ambiguous_hash_left_out(self):
        a = 'models/hp_vegetation/z14/birch_paper/birch_paper_mdm_01.hrmeshc'
        b = 'worlds/world2/locations/hp_patagonia/lookout_towers/lookout_patagonia_08.blo'
        etat = NF.Etat.depuis_lignes([(1, 'x.hrmeshc', 'adf'), (2, 'x.blo', 'rtpc'), (h32(a), None, 'adf'),
                                      (h32(a), None, 'rtpc')])
        ch = G.Chercheur(etat, NF.ModeleTypes(etat), {})
        st = G.Stat('t')
        ch.essayer(a, st)
        ch.essayer(b, st)
        self.assertEqual(ch.resultats(), ({}, {h32(a): sorted([a, b])}))

    def test_report(self):
        texte = '\n'.join(G.rapport(self.ch, len(self.etat.sans_nom)))
        self.assertIn('**Noms retenus : 6**', texte)


class TestHorsLigne(unittest.TestCase):
    def test_offline_inputs(self):
        with tempfile.TemporaryDirectory(prefix='cotw_gen_') as d:
            d = Path(d)
            (d / 'vpaths.txt').write_text(''.join('{}\tabc\n'.format(n) for n, _t in NOMMES), encoding='utf-8')
            lignes = ['hash;archive;niveau;type_detecte;magic_ascii;taille_octets']
            for n, t in CIBLES.items():
                lignes.append('{:08x};game0.tab;direct;{};;10'.format(h32(n), t or '(aucun type)'))
            (d / 'orphelins.csv').write_text('﻿' + '\n'.join(lignes) + '\n', encoding='utf-8')
            couv = ['famille;types_detectes', '.wavc;FSB5c (2)', '.ddsc;avtx (4), dds (1)', '.hmddsc;hmddsc (1)',
                    '.blo;rtpc (2), symlink (1)', '(sans nom) avtx;avtx (3)']
            (d / 'couv.csv').write_text('﻿' + '\n'.join(couv) + '\n', encoding='utf-8')
            etat = G.etat_hors_ligne(str(d / 'vpaths.txt'), str(d / 'orphelins.csv'), str(d / 'couv.csv'))
            self.assertEqual(len(etat.sans_nom), len(CIBLES))
            self.assertEqual(etat.types_par_extension['.ddsc'], {'avtx': 4, 'dds': 1})
            self.assertNotIn(NF.TYPE_LIEN, etat.types_par_extension['.blo'])
            sortie = d / 'noms.txt'
            self.assertEqual(G.main(['--hors-ligne', str(d / 'vpaths.txt'), str(d / 'orphelins.csv'),
                                     str(d / 'couv.csv'), '--sortie', str(sortie), '--passes', 'chiffres,suffixes,dossiers']), 0)
            noms = [l for l in sortie.read_text(encoding='utf-8').splitlines() if not l.startswith('#')]
            self.assertEqual(set(noms), set(CIBLES) - {'ui/zoom2/07.ddsc'})


def binaire(n):
    return bytes([0x8f, 0x13, 0xc2, n]) + bytes((i * 37 + n) & 0xFF for i in range(60))


class TestAvecRenommer(unittest.TestCase):
    """generer_noms.py sur un vrai projet, puis renommer.py : les fichiers d'une serie sont nommes."""

    def test_generate_then_rename(self):
        import faux_projet
        from deca.db_processor import vfs_structure_prep
        from deca.path import UniPath
        fichiers = [('snd/s/a_{:02d}.raw'.format(i), binaire(i)) for i in range(6)]
        with tempfile.TemporaryDirectory(prefix='cotw_gen_projet_') as root:
            project_file = faux_projet.build_fake_game(root, fichiers)
            working_dir = UniPath.join(UniPath.split(project_file)[0], '')
            vfs = vfs_structure_prep(project_file, working_dir)
            vfs.db_conn.close()
            db = sqlite3.connect(os.path.join(working_dir, 'db', 'core.db'))
            for p in ('snd/s/a_00.raw', 'snd/s/a_01.raw'):
                db.execute('UPDATE core_nodes SET v_path = ? WHERE v_hash = ?', (p, h32(p)))
            db.commit()
            db.close()
            sortie = os.path.join(root, 'noms.txt')
            self.assertEqual(G.main([working_dir, '--sortie', sortie]), 0)
            self.assertEqual(RN.main(['ajouter', working_dir, '--noms', sortie]), 0)
            db = sqlite3.connect(os.path.join(working_dir, 'db', 'core.db'))
            noms = {_projet.to_text(r[0]) for r in db.execute('SELECT v_path FROM core_nodes WHERE v_path IS NOT NULL')}
            db.close()
            self.assertTrue({p for p, _c in fichiers} <= noms)


if __name__ == '__main__':
    if '--spawn' in sys.argv:
        sys.argv.remove('--spawn')
        multiprocessing.set_start_method('spawn', force=True)
    unittest.main()
