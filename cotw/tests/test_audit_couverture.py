#!/usr/bin/env python3
"""
Tests de la couverture de lecture par extension (deca_audit.py, phase B) et de la selection d'echantillons
(extraire.py --extension / --echantillons / --noeuds).

  - base synthetique (sqlite en memoire, schema de core_nodes) : statut de chaque cas, verdicts, priorites ;
  - projet demo (vrai scan, dossier temporaire) : audit complet, CSV, extraire_echantillons.bat, puis extraction ;
  - la table des visionneuses de l'audit (sans Qt) est celle de l'interface (dataviewwidget.VIEWER_TABS).

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_audit_couverture.py [--spawn]
"""
import csv
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import _projet  # noqa: E402,F401  (niveau module : les processus spawn reimportent ce fichier)
import deca_audit as DA  # noqa: E402
import demo_projet  # noqa: E402
import extraire as EX  # noqa: E402

PROCESSED = DA.FLAG_PROCESSED_FILE_TYPE
FAILED = DA.FLAG_PROCESSING_FAILED
TEMP = DA.FLAG_TEMPORARY_FILE


def synthetic_db():
    """Base minimale : 1 arc, 2 tab, et un fichier par cas de statut."""
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE core_nodes (node_id INTEGER PRIMARY KEY, flags INTEGER, parent_id INTEGER, '
                 'v_hash INTEGER, v_path BLOB, p_path BLOB, magic INTEGER, file_type TEXT, size_c INTEGER, '
                 'size_u INTEGER)')
    conn.execute('CREATE TABLE core_adf_types (type_hash INTEGER, missing_in INTEGER, pickle BLOB)')
    conn.execute('CREATE TABLE core_strings (hash32 INTEGER, string TEXT)')
    rows = [
        # id, flags, parent, v_hash, v_path, magic, type, size
        (1, 0, None, None, None, None, 'arc', 0),
        (2, 0, 1, None, None, None, 'tab', 0),
        (3, 0, 1, None, None, None, 'tab', 0),
        (10, PROCESSED, 2, 0xa1, b'settings/a.rtpc', 0x43505452, 'rtpc', 100),   # lisible, explore
        (11, 0, 3, 0xa1, b'settings/a.rtpc', 0x43505452, 'rtpc', 100),           # copie : compte une fois
        (12, 0, 2, 0xb1, b'x/one.ban', 0x00000202, None, 50),                     # type inconnu
        (13, 0, 2, 0xb2, b'x/two.ban', 0x41444620, 'adf', 70),                    # ADF lisible
        (14, 0, 2, 0xb3, b'x/three.ban', 0x00000202, None, 40),                   # type inconnu
        (15, 0, 2, 0xc1, b'y/z.bin', 0x41444620, 'adf', 30),                      # ADF sans definition
        (16, FAILED, 2, 0xc2, b'y/w.bin', 0x43505452, 'rtpc', 20),                # echec au scan
        (17, 0, 2, 0xc3, b'y/link.bin', None, 'symlink', 0),                      # lien : hors compte
        (18, 0, 2, 0xd1, None, 0x2049444d, 'mdi', 500),                           # sans nom, sans visionneuse
        (19, 0, 2, 0xd2, None, 0x2049444d, 'mdi', 300),
        (20, PROCESSED, 2, 0xe1, b'g/files.gtoc', 0x43305447, 'gt0c', 900),       # conteneur explore
        (21, TEMP, 2, 0xf1, b'tmp/t.rtpc', None, 'rtpc', 5),                      # temporaire : ignore
        (22, 0, 2, 0xf2, b'y/k.nl.mdic', 0x00000000, 'NOT', 10),                  # non ouvrable
    ]
    conn.executemany('INSERT INTO core_nodes (node_id, flags, parent_id, v_hash, v_path, magic, file_type, size_u) '
                     'VALUES (?, ?, ?, ?, ?, ?, ?, ?)', rows)
    conn.execute('INSERT INTO core_adf_types VALUES (?, ?, ?)', (0x12345678, 15, b''))
    return conn


def run_audit(conn):
    a = DA.Audit(conn, sample_size=3)
    a.load_hierarchy()
    a.scan_nodes()
    a.scan_adf_missing()
    a.scan_meta()
    a.scan_coverage()
    return a


def texture_db():
    """Textures : un .ddsc et ses niveaux de mip (lisibles), des mips sans .ddsc nomme (illisibles)."""
    conn = synthetic_db()
    rows = [
        (30, 0, 2, 0x301, b'textures/a.ddsc', 0x58545641, 'avtx', 100),
        (31, 0, 2, 0x302, b'textures/a.hmddsc', None, 'hmddsc', 400),      # son .ddsc existe : lisible
        (32, 0, 2, 0x303, b'textures/a.atx1', None, 'atx', 200),           # idem
        (33, 0, 2, 0x304, b'textures/b.hmddsc', None, 'hmddsc', 400),      # pas de textures/b.ddsc
        (34, 0, 3, 0x304, b'textures/b.hmddsc', None, 'hmddsc', 400),      # copie : compte une fois
        (35, 0, 2, 0x305, b'textures/c.atx2', None, 'atx', 50),
        (36, 0, 2, 0x306, None, None, 'hmddsc', 300),                       # sans nom : pas de .ddsc a trouver
    ]
    conn.executemany('INSERT INTO core_nodes (node_id, flags, parent_id, v_hash, v_path, magic, file_type, size_u) '
                     'VALUES (?, ?, ?, ?, ?, ?, ?, ?)', rows)
    return conn


class TestMipLevels(unittest.TestCase):
    """deca_audit compte un .hmddsc / .atxN comme lisible seulement si son .ddsc est nomme (ff_avtx.image_load)."""

    @classmethod
    def setUpClass(cls):
        cls.a = run_audit(texture_db())
        cls.rows = {r['famille']: r for r in cls.a.coverage}

    def test_statuses(self):
        self.assertEqual(self.rows['.ddsc']['statuts'], {'lisible': 1})
        self.assertEqual(self.rows['.hmddsc']['statuts'], {'lisible': 1, 'mip_orphelin': 1})
        self.assertEqual(self.rows['.hmddsc']['exemples'], ['textures/b.hmddsc'])
        self.assertEqual(self.rows['.atx1']['statuts'], {'lisible': 1})
        self.assertEqual(self.rows['.atx2']['statuts'], {'mip_orphelin': 1})
        self.assertEqual(self.rows['(sans nom) hmddsc']['statuts'], {'mip_orphelin': 1})

    def test_metric_and_comparison(self):
        self.assertEqual(self.a.m['mips_orphelins'], 3)
        labels = [label for _k, label, _l in DA.SCALAIRES_COMPARES]
        self.assertIn('Niveaux de mip sans leur .ddsc', labels)


class TestCoverageSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = run_audit(synthetic_db())
        cls.rows = {r['famille']: r for r in cls.a.coverage}

    def test_families(self):
        self.assertEqual(set(self.rows), {'.rtpc', '.ban', '.bin', '(sans nom) mdi', '.gtoc', '.nl.mdic'})

    def test_copies_count_once(self):
        r = self.rows['.rtpc']
        self.assertEqual((r['fichiers'], r['noeuds'], r['octets']), (1, 2, 100))
        self.assertEqual((r['lisibles'], r['verdict']), (1, 'lu'))
        self.assertEqual((r['explores'], r['explorables']), (1, 1))
        self.assertEqual(r['lecteurs'], {'RTPC': 1})

    def test_partial_family(self):
        r = self.rows['.ban']
        self.assertEqual((r['fichiers'], r['lisibles'], r['non_lus'], r['verdict']), (3, 1, 2, 'partiel'))
        self.assertEqual(r['statuts'], {'type_inconnu': 2, 'lisible': 1})
        self.assertEqual(r['exemples'], ['x/one.ban', 'x/three.ban'])   # exemples non lisibles seulement
        self.assertEqual(r['magics'].most_common(1)[0][1], 2)

    def test_failed_missing_adf_and_link(self):
        r = self.rows['.bin']
        self.assertEqual(r['statuts'], {'adf_sans_type': 1, 'echec_scan': 1, 'lien': 1})
        self.assertEqual((r['fichiers'], r['lisibles'], r['non_lus'], r['verdict']), (3, 0, 2, 'non lu'))
        self.assertEqual(self.a.m['echecs_scan'], 1)

    def test_unnamed_and_containers(self):
        r = self.rows['(sans nom) mdi']
        self.assertEqual((r['fichiers'], r['verdict'], r['noeuds_exemples']), (2, 'non lu', [18, 19]))
        self.assertEqual(r['statuts'], {'type_sans_visionneuse': 2})
        self.assertEqual(self.rows['.gtoc']['verdict'], 'lu')
        self.assertEqual(self.rows['.gtoc']['statuts'], {'conteneur': 1})
        self.assertEqual(self.rows['.nl.mdic']['statuts'], {'type_inconnu': 1})

    def test_metrics_and_priorities(self):
        m = self.a.m
        # utiles : rtpc 1 + ban 3 + bin 2 (sans le lien) + mdi 2 + gtoc 1 + nl.mdic 1 = 10 ; lus : 1 + 1 + 1 = 3
        self.assertEqual((m['couverture_fichiers'], m['couverture_lisibles']), (10, 3))
        self.assertEqual(m['couverture_pct'], 30.0)
        self.assertEqual((m['familles'], m['familles_non_lues'], m['familles_partielles']), (6, 3, 1))
        prio = [r['famille'] for r in self.a.priorities()]
        self.assertEqual(prio[:3], ['(sans nom) mdi', '.ban', '.bin'])   # 2 non lus chacun : volume decroissant
        self.assertEqual(prio[3], '.nl.mdic')
        self.assertNotIn('.rtpc', prio)

    def test_sample_commands(self):
        proj, out = Path('P'), Path('O')
        cmd = DA.sample_command(self.rows['.ban'], proj, out, 4)
        self.assertIn('--extension .ban --echantillons 4', cmd)
        self.assertIn(str(Path('O') / 'echantillons' / 'ban'), cmd)
        cmd = DA.sample_command(self.rows['(sans nom) mdi'], proj, out, 4)
        self.assertIn('--noeuds 18 19', cmd)
        self.assertIn(str(Path('O') / 'echantillons' / 'sans_nom_mdi'), cmd)
        self.assertEqual(DA.sample_dir_name('.nl.mdic'), 'nl.mdic')
        self.assertEqual(DA.sample_dir_name('(sans extension)'), 'sans_extension')


class TestTablesAgree(unittest.TestCase):
    def test_viewer_table_is_the_gui_routing(self):
        from deca_gui import dataviewwidget as DV
        self.assertEqual(DA.LECTEURS, DV.VIEWER_TABS)

    def test_types_exist_in_deca(self):
        from deca import ff_types as FT
        known = {v for k, v in vars(FT).items() if k.startswith('FTYPE_') and isinstance(v, str)}
        for t in set(DA.LECTEURS) | DA.CONTENEURS | DA.TYPES_EXPLORES:
            self.assertIn(t, known, t)
        self.assertEqual(DA.FLAG_PROCESSING_FAILED, __import__('deca.db_types', fromlist=['x'])
                         .node_flag_processing_failed)


class TestExtraireSelection(unittest.TestCase):
    def test_extension_rule(self):
        rows = [('a/b.nl.mdic', 1), ('a/c.mdic', 2), ('a/d.MDIC', 3), ('a/e_mdic', 4), ('a.dir/f', 5)]
        self.assertEqual([r[0] for r in EX.with_extension(rows, '.mdic')], ['a/c.mdic', 'a/d.MDIC'])
        self.assertEqual([r[0] for r in EX.with_extension(rows, 'nl.mdic')], ['a/b.nl.mdic'])
        self.assertEqual(EX.extension_of('a.dir/f'), '(sans extension)')
        self.assertEqual(EX.extension_of('x/y.fmod_guids'), DA.extension_of('x/y.fmod_guids'))

    def test_spread(self):
        rows = [('f{}'.format(i), size) for i, size in enumerate([50, 10, 40, 30, 20, 60, 70])]
        self.assertEqual([r[1] for r in EX.spread(rows, 3)], [10, 40, 70])
        self.assertEqual([r[1] for r in EX.spread(rows, 1)], [40])
        self.assertEqual(len(EX.spread(rows, 10)), 7)
        self.assertEqual(EX.spread(rows, 3), EX.spread(list(reversed(rows)), 3))
        with self.assertRaises(SystemExit):
            EX.select(rows, 'x', 5, 0)
        self.assertEqual(len(EX.select(rows, 'x', 5, 2)), 2)


class TestDemoProject(unittest.TestCase):
    """Audit complet du projet demo, puis extraction avec les commandes proposees."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='cotwed_audit_'))
        cls.project_file = demo_projet.build(cls.root / 'demo')
        cls.project = cls.project_file.parent
        cls.out = cls.root / 'audit'
        DA.main([str(cls.project), '--out', str(cls.out), '--echantillons', '2'])
        with open(cls.out / 'couverture_extensions.csv', encoding='utf-8-sig') as f:   # BOM pour Excel
            cls.rows = {r['famille']: r for r in csv.DictReader(f, delimiter=';')}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_csv(self):
        rows = self.rows
        self.assertEqual(rows['.ee']['verdict'], 'lu')
        self.assertIn('SARC', rows['.ee']['visionneuses'])
        self.assertEqual(rows['.epe']['verdict'], 'lu')
        self.assertEqual(rows['.stringlookup']['visionneuses'], 'ADF (1)')
        self.assertEqual(rows['.fmod_guids']['verdict'], 'non lu')
        self.assertEqual(rows['.fmod_guids']['fichiers_uniques'], str(len(demo_projet.AMMO)))
        self.assertTrue(any(k.startswith('(sans nom)') for k in rows), sorted(rows))

    def test_report_and_metrics(self):
        report = (self.out / 'rapport.md').read_text(encoding='utf-8')
        self.assertIn('## 3. Couverture de lecture par extension (COTWed DECA)', report)
        self.assertIn('### Priorites (phase C', report)
        self.assertIn('`.fmod_guids`', report)
        import json
        m = json.loads((self.out / 'metrics.json').read_text(encoding='utf-8'))
        self.assertGreater(m['couverture_lisibles'], 0)
        self.assertLess(m['couverture_lisibles'], m['couverture_fichiers'])
        self.assertGreaterEqual(m['commandes_echantillons'], 1)

    def test_sample_script_runs(self):
        bat = (self.out / 'extraire_echantillons.bat').read_text(encoding='utf-8')
        self.assertTrue(bat.startswith('@echo off'))
        line = [x for x in bat.splitlines() if '--extension .fmod_guids' in x][0]
        self.assertIn('--echantillons 2', line)
        self.assertIn('--a-plat', line)
        # meme selection que la commande, executee ici
        target = self.root / 'ech'
        EX.main([str(self.project), '--extension', '.fmod_guids', '--echantillons', '2', '--sortie', str(target)])
        written = sorted(p for p in target.rglob('*') if p.is_file())
        self.assertEqual(len(written), 2)
        self.assertTrue(all(p.name.endswith('.fmod_guids') for p in written))
        flat = self.root / 'ech_plat'
        EX.main([str(self.project), '--extension', '.fmod_guids', '--echantillons', '2', '--a-plat', '--sortie', str(flat)])
        self.assertEqual(sorted(p.name for p in flat.iterdir()),
                         sorted('__'.join(p.relative_to(target).parts) for p in written))
        unnamed = [x for x in bat.splitlines() if '--noeuds' in x]
        if unnamed:
            ids = [int(t) for t in unnamed[0].split('--noeuds', 1)[1].split('--sortie')[0].split() if t.isdigit()]
            EX.main([str(self.project), '--noeuds', str(ids[0]), '--sortie', str(target)])
            self.assertTrue(list((target / '_noeuds').glob('{}_*'.format(ids[0]))))


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    if '--spawn' in sys.argv:
        sys.argv.remove('--spawn')
        multiprocessing.set_start_method('spawn', force=True)
    unittest.main()
