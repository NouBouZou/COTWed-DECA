#!/usr/bin/env python3
"""
Tests de cotw/audit_noms_internes.py sur un faux projet DECA (vrai scan) : un RTPC et un ADF dont chaque hash
interne a un statut connu d'avance (resolu sur place, par une chaine connue, par le dictionnaire, inconnu),
une enumeration dont DECA affiche le mauvais libelle, une valeur sentinelle ignoree.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_audit_noms_internes.py [--spawn]
"""
import csv
import json
import multiprocessing
import os
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401  (niveau module : les processus spawn reimportent ce fichier)
import audit_noms_internes as A  # noqa: E402
import adf_fabrique as F  # noqa: E402
import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import Rtpc, k_type_str, k_type_u32, k_type_objid, k_type_event  # noqa: E402
from deca.hashes import hash32_func, hash48_func  # noqa: E402


def h32(s):
    return int(hash32_func(s.encode())) & 0xFFFFFFFF


INCONNU_PROP = 0x12345678
OBJID = 0xFFFF00001234ABCD
EVENT_CONNU = 'cotw_test_event_known'
STRING_CONNUE = 'cotw_test_string_known'


def rtpc_bytes():
    item = W.make_node('item_a', [
        W.make_prop('name', k_type_str, b'item_a'),                 # noeud nomme par la chaine du fichier
        W.make_prop('cash_price', k_type_u32, 3),                   # dictionnaire du fork
        W.make_prop(INCONNU_PROP, k_type_u32, 1),                   # inconnu
        W.make_prop(0x1000, k_type_objid, OBJID),                   # objid aleatoire : inconnu
        W.make_prop(0x1001, k_type_event, [int(hash48_func(EVENT_CONNU.encode()))]),   # chaine connue (hash48)
    ])
    r = Rtpc()
    r.version = 1
    r.root_node = W.make_node(0, [], [item])
    return W.rtpc_to_binary(r)


T_ENUM, T_SH, T_ROOT = 0x7E570001, 0x7E570002, 0x7E570003


def adf_bytes():
    t = F.Types()
    t.enum('Mode', T_ENUM, [('A', 0), ('B', 2), ('C', 1)])
    t.stringhash('StringHash_48c5294d_4', T_SH)
    t.struct('Root', T_ROOT, 20, 4, [('sh_file', T_SH, 4, 0), ('sh_core', T_SH, 4, 4), ('sh_none', T_SH, 4, 8),
                                       ('sh_unknown', T_SH, 4, 12), ('mode', T_ENUM, 4, 16)])
    data = struct.pack('<IIIII', h32('in/file_table'), h32(STRING_CONNUE), 0xdeadbeef, h32('nobody/knows'), 1)
    return F.build(t, [('root', T_ROOT, data)], stringhashes=[(b'in/file_table', h32('in/file_table'))])


class TestAuditNomsInternes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import faux_projet
        cls.tmp = tempfile.TemporaryDirectory(prefix='cotw_audit_n2_')
        cls.project = faux_projet.build_fake_project(cls.tmp.name, [('settings/test.bin', rtpc_bytes()),
                                                                    ('settings/test.adftest', adf_bytes())])
        # chaines connues de la base, comme si un dictionnaire les avait apportees
        from deca.db_types import make_hash_string_tuple
        db = sqlite3.connect(os.path.join(cls.project, 'db', 'core.db'))
        for s in (EVENT_CONNU, STRING_CONNUE):
            t = make_hash_string_tuple(s.encode())
            db.execute('INSERT OR IGNORE INTO core_strings VALUES (?,?,?,?,?)', (s, t[1], t[2], t[3], t[4]))
        db.commit()
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_audit(self, processus):
        out = os.path.join(self.tmp.name, 'out{}'.format(processus))
        self.assertEqual(A.main([self.project, '--out', out, '--processus', str(processus)]), 0)
        metrics = json.loads(Path(out, 'metrics.json').read_text(encoding='utf-8'))
        with open(os.path.join(out, 'inconnus.csv'), encoding='utf-8-sig') as f:
            inconnus = list(csv.DictReader(f, delimiter=';'))
        return metrics, inconnus, Path(out, 'rapport.md').read_text(encoding='utf-8')

    def check(self, metrics, inconnus, rapport):
        self.assertEqual(metrics['fichiers'], {'rtpc': 1, 'adf': 1})
        self.assertEqual(metrics['echecs'], 0)
        c = metrics['categories']
        self.assertEqual((c['rtpc_noeud']['occurrences'], c['rtpc_noeud']['occ_resolues'],
                          c['rtpc_noeud']['occ_sur_place']), (1, 1, 1))
        # name (chaines de DECA), cash_price (dictionnaire), 0x12345678, 0x1000, 0x1001 : 2 resolus sur 5
        self.assertEqual((c['rtpc_propriete']['occurrences'], c['rtpc_propriete']['occ_resolues']), (5, 2))
        self.assertEqual((c['rtpc_event']['occurrences'], c['rtpc_event']['occ_resolues']), (1, 1))
        self.assertEqual((c['rtpc_objid']['occurrences'], c['rtpc_objid']['occ_resolues']), (1, 0))
        # sh_file (table du fichier), sh_core (core_strings), sh_unknown ; 0xdeadbeef ignore
        sh = c['adf_stringhash_32']
        self.assertEqual((sh['occurrences'], sh['occ_resolues'], sh['occ_sur_place']), (3, 2, 1))
        self.assertEqual(metrics['enums']['occurrences'], 1)
        self.assertEqual(metrics['enums']['hors_membres'], 0)
        self.assertEqual(metrics['enums']['occurrences_libelle_faux'], 1)
        hashes = {(r['categorie'], r['hash']) for r in inconnus}
        self.assertIn(('rtpc_propriete', '{:08x}'.format(INCONNU_PROP)), hashes)
        self.assertIn(('adf_stringhash_32', '{:08x}'.format(h32('nobody/knows'))), hashes)
        self.assertIn(('rtpc_objid', '{:012x}'.format(OBJID & A.MASK48)), hashes)
        self.assertEqual(len(inconnus), 5)     # 3 proprietes, 1 objid, 1 StringHash
        self.assertIn('Libelle faux dans DECA', rapport)
        # familles (familles.py) : settings/test.bin -> test.bin ; pas de famille pour objid et StringHash
        fams = {(r['categorie'], r['hash']): r['familles'] for r in inconnus}
        self.assertEqual(fams[('rtpc_propriete', '{:08x}'.format(INCONNU_PROP))], 'test.bin:1')
        self.assertEqual(fams[('rtpc_objid', '{:012x}'.format(OBJID & A.MASK48))], '')
        self.assertEqual(metrics['familles'], 1)
        self.assertIn('| rtpc_propriete | `test.bin` | 3 | 3 |', rapport)

    def check_vocabulary(self, out):
        lignes = [l.split('\t') for l in Path(out, 'noms_par_famille.tsv').read_text(encoding='utf-8').splitlines()
                  if not l.startswith('#')]
        vocab = {(f, c, n) for f, c, n, _o in lignes}
        self.assertIn(('test.bin', 'rtpc_noeud', 'item_a'), vocab)                  # chaine du fichier
        self.assertIn(('test.bin', 'rtpc_propriete', 'name'), vocab)                # chaines de DECA
        self.assertIn(('test.bin', 'rtpc_propriete', 'cash_price'), vocab)          # dictionnaire du fork
        self.assertIn(('test.bin', 'rtpc_event', EVENT_CONNU), vocab)               # hash48
        self.assertEqual(len(vocab), 4)

    def test_single_process(self):
        self.check(*self.run_audit(1))
        self.check_vocabulary(os.path.join(self.tmp.name, 'out1'))

    def test_pool(self):
        self.check(*self.run_audit(2))

    def test_resume_after_a_failed_write(self):
        """Une ecriture qui echoue laisse erreur.txt et reprise.pkl ; --reprendre refait les sorties sans relire."""
        out = Path(self.tmp.name, 'out_reprise')
        vrai = A.ecrire_echantillons
        def casse(*_a, **_k):
            raise RuntimeError('ecriture cassee')
        A.ecrire_echantillons = casse
        try:
            self.assertEqual(A.main([self.project, '--out', str(out), '--processus', '1']), 1)
        finally:
            A.ecrire_echantillons = vrai
        self.assertIn('ecriture cassee', (out / 'erreur.txt').read_text(encoding='utf-8'))
        self.assertTrue((out / 'reprise.pkl').exists())
        self.assertFalse((out / 'rapport.md').exists())
        # la reprise ne relit rien : un projet qui n'existe pas suffit
        self.assertEqual(A.main(['/projet/absent', '--out', str(out), '--reprendre', str(out / 'reprise.pkl')]), 0)
        self.assertFalse((out / 'erreur.txt').exists())
        self.assertFalse((out / 'reprise.pkl').exists())
        metrics = json.loads((out / 'metrics.json').read_text(encoding='utf-8'))
        with open(out / 'inconnus.csv', encoding='utf-8-sig') as f:
            inconnus = list(csv.DictReader(f, delimiter=';'))
        self.check(metrics, inconnus, (out / 'rapport.md').read_text(encoding='utf-8'))

    def test_compare_with_previous_audit(self):
        metrics, _inconnus, _rapport = self.run_audit(1)
        self.assertIn('date', metrics)
        # audit precedent fictif : une propriete de moins resolue
        old = json.loads(json.dumps(metrics))
        old['categories']['rtpc_propriete']['occ_resolues'] -= 1
        old['categories']['rtpc_propriete']['distincts_resolus'] -= 1
        old_path = Path(self.tmp.name, 'ancien_metrics.json')
        old_path.write_text(json.dumps(old), encoding='utf-8')
        out = os.path.join(self.tmp.name, 'out_compare')
        self.assertEqual(A.main([self.project, '--out', out, '--processus', '1', '--compare', str(old_path)]), 0)
        rapport = Path(out, 'rapport.md').read_text(encoding='utf-8')
        self.assertIn('## Evolution depuis', rapport)
        self.assertIn('| rtpc_propriete | 20.0 % | 40.0 % | +20.0 | 20.0 % | 40.0 % | +20.0 | 3 (-1) |', rapport)
        self.assertIn('| rtpc_noeud | 100.0 % | 100.0 % | +0.0 |', rapport)
        self.assertNotIn('**Attention**', rapport)


class TestEchantillons(unittest.TestCase):
    """Releve des pointeurs ADF et des types de proprietes RTPC (2026-10-22)."""

    def test_pointer_note(self):
        c, vus = A.Collecte(), set()
        A.noter_pointeur('SomePtr', 0x2800000010, 0x1234abcd, 'a.adf', c, vus)     # cible 0x10, lien 0x28
        A.noter_pointeur('SomePtr', 0, 0x1234abcd, 'a.adf', c, vus)
        A.noter_pointeur('SomePtr', 0x20, 0x1234abcd, 'b.adf', c, set())
        A.noter_pointeur('SomePtr', 0, 0x1234abcd, 'd.adf', c, set())
        A.noter_pointeur('', 0, 0, 'c.adf', c, set())
        self.assertEqual(c.pointeurs_types[('SomePtr', '1234abcd')], [4, 2, 3])   # occurrences, non nuls, fichiers
        # exemples : un par fichier, pointeurs non nuls seulement
        self.assertEqual(c.pointeurs_exemples[('SomePtr', '1234abcd')],
                         [('a.adf', '0000002800000010'), ('b.adf', '0000000000000020')])
        self.assertEqual(c.pointeurs_types[('?', '00000000')], [1, 0, 1])

    def test_rtpc_types(self):
        from deca.ff_rtpc import k_type_unk_15, k_type_unk_16
        noeud = W.make_node('n', [W.make_prop('a', k_type_u32, 1), W.make_prop('b', k_type_unk_15, 7),
                                  W.make_prop('c', k_type_unk_16, 0xfffffffe)])
        r = Rtpc()
        r.version = 1
        r.root_node = W.make_node(0, [], [noeud])
        c = A.Collecte()
        A.collecter_rtpc(W.rtpc_to_binary(r), 'x/y.bin', c)
        self.assertEqual(dict(c.types_rtpc), {k_type_u32: 1, k_type_unk_15: 1, k_type_unk_16: 1})
        self.assertEqual(c.rtpc_rares[k_type_unk_15], [('x/y.bin', h32('n'), h32('b'), 7)])
        self.assertEqual(c.rtpc_rares[k_type_unk_16], [('x/y.bin', h32('n'), h32('c'), 0xfffffffe)])
        # fusion de deux lots
        d = A.Collecte()
        d.fusionner(c)
        d.fusionner(c)
        self.assertEqual(d.types_rtpc[k_type_unk_15], 2)
        self.assertEqual(len(d.rtpc_rares[k_type_unk_15]), 2)

    def test_sample_files(self):
        from deca.ff_rtpc import k_type_unk_15
        c = A.Collecte()
        A.noter_pointeur('P', 0x10, 0xbeef, 'gfx/a.adf', c, set())
        A.noter_pointeur('P', 0x10, 0xbeef, '(sans nom) 0123abcd', c, set())
        c.rtpc_rares[k_type_unk_15] = [('settings/z.bin', 1, 2, 3)]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            A.ecrire_echantillons(out, c, 'D:\\projet')
            with open(out / 'pointeurs_adf.csv', encoding='utf-8-sig') as f:
                lignes = list(csv.DictReader(f, delimiter=';'))
            self.assertEqual(len(lignes), 1)
            self.assertEqual((lignes[0]['type_pointeur'], lignes[0]['type_vise'], lignes[0]['occurrences'],
                              lignes[0]['fichiers']), ('P', '0000beef', '2', '2'))
            with open(out / 'rtpc_types_rares.csv', encoding='utf-8-sig') as f:
                rares = list(csv.DictReader(f, delimiter=';'))
            self.assertEqual(rares, [{'type': str(k_type_unk_15), 'fichier': 'settings/z.bin', 'noeud': '00000001',
                                      'propriete': '00000002', 'valeur': '3'}])
            bat = (out / 'extraire_echantillons.bat').read_bytes().decode('utf-8')
            self.assertIn('\r\n', bat)
            self.assertNotIn('%PROJET%', bat)
            self.assertIn('python cotw\\extraire.py "D:\\projet" gfx/a.adf settings/z.bin --sortie', bat)
            self.assertIn('--hashes 0123abcd', bat)


if __name__ == '__main__':
    if '--spawn' in sys.argv:
        sys.argv.remove('--spawn')
        multiprocessing.set_start_method('spawn', force=True)
    unittest.main()
