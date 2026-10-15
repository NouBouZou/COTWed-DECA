#!/usr/bin/env python3
"""
Tests de cotw/renommer.py et cotw/noms_fichiers.py : nommer les fichiers sans nom d'un projet deja scanne,
controle de type, associations, simulation, annulation, retrait de quelques noms ; chaines de l'executable lues au scan ; banques FMOD
nommees par la banque de chaines, au scan et par --fmod.

Un faux jeu est scanne par DECA (vrai pipeline). Quelques fichiers sont nommes directement dans la base (ils
servent a apprendre les types attendus par extension) ; les autres restent sans nom, comme les orphelins du jeu.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_renommer.py [--spawn]
"""
import hashlib
import multiprocessing
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401  (niveau module : les processus spawn reimportent ce fichier)
import noms_fichiers as NF  # noqa: E402
import renommer as RN  # noqa: E402
import fmod_fabrique as FF  # noqa: E402
import deca.ff_rtpc_write as W  # noqa: E402
from deca.ff_rtpc import Rtpc, k_type_u32  # noqa: E402
from deca.hashes import hash32_func  # noqa: E402


def h32(s):
    return int(hash32_func(s.encode())) & 0xFFFFFFFF


def rtpc(value):
    r = Rtpc()
    r.version = 1
    r.root_node = W.make_node(0, [], [W.make_node(0x1234, [W.make_prop(0x5678, k_type_u32, value)])])
    return W.rtpc_to_binary(r)


def texte(n):
    return ('ceci est un fichier texte numero {} sans chemin dedans\n'.format(n) * 2).encode('ascii')


def binaire(n):
    return bytes([0x8f, 0x13, 0xc2, n]) + bytes((i * 37 + n) & 0xFF for i in range(60))


# chemin -> (contenu, nomme dans la base apres le scan)
FICHIERS = {
    'docs/notes_01.txt': (texte(1), True),
    'data/blob_01.raw': (binaire(1), True),
    'cfg/a_01.bin': (rtpc(1), True),
    'docs/notes_02.txt': (texte(2), False),
    'data/blob_02.raw': (binaire(2), False),
    'cfg/a_02.bin': (rtpc(2), False),
    'tex/t_01.hmddsc': (binaire(3), False),       # nomme par association depuis tex/t_01.ddsc
    'docs/fake_01.txt': (binaire(4), False),      # binaire sous une extension texte : refuse
    'misc/thing.yyy': (texte(5), False),          # extension jamais vue : sans controle
    'misc/from_exe.zzz': (texte(6), False),       # nomme par le scan : chaine de l'executable
    'misc/later_exe.zzz': (texte(7), False),      # nomme par renommer.py --exe (exe remplace apres le scan)
    # banque de chaines FMOD : cite bank:/master/master et bank:/animals/specific/animal_test
    'sound/fmod_banks/master_strings.fmod_bankc': (FF.strings_bank(), True),
    'sound/fmod_banks/master.fmod_bankc': (FF.sound_bank(1), False),             # nomme par le scan
    'sound/fmod_banks/animal_test.fmod_bankc': (FF.sound_bank(2), False),        # idem ; puis par --fmod
}
EXE = b'MZ' + b'\0' * 62 + b'\0misc/from_exe.zzz\0version 1.25\0%d items\0ui/zoom%d/%02d.ddsc\0' + b'\0' * 64
EXE_APRES = EXE + b'\0Misc\\Later_Exe.zzz\0'

CANDIDATS = ['docs/notes_02.txt', 'data/blob_02.raw', 'cfg/a_02.bin', 'tex/t_01.ddsc', 'docs/fake_01.txt',
             'misc/thing.yyy', 'docs/notes_01.txt', 'nowhere/x.txt']


def instantane(db_path):
    """Contenu des tables que renommer.py modifie, pour comparer avant / apres annulation."""
    conn = sqlite3.connect(db_path)
    try:
        out = {}
        for table in ('core_nodes', 'core_strings', 'core_string_references'):
            rows = conn.execute('SELECT rowid, * FROM {}'.format(table)).fetchall()
            out[table] = hashlib.sha1(repr(sorted(rows, key=repr)).encode()).hexdigest()
        return out
    finally:
        conn.close()


def build_project(root):
    import faux_projet
    from deca.db_processor import vfs_structure_prep
    from deca.path import UniPath
    project_file = faux_projet.build_fake_game(root, [(p, c) for p, (c, _n) in FICHIERS.items()], EXE)
    working_dir = UniPath.join(UniPath.split(project_file)[0], '')
    vfs = vfs_structure_prep(project_file, working_dir)
    vfs.db_conn.close()
    db = sqlite3.connect(os.path.join(working_dir, 'db', 'core.db'))
    for path, (_c, nomme) in FICHIERS.items():
        if nomme:
            db.execute('UPDATE core_nodes SET v_path = ? WHERE v_hash = ? AND v_path IS NULL', (path, h32(path)))
    db.commit()
    db.close()
    return working_dir


class TestModele(unittest.TestCase):
    def test_extensions(self):
        self.assertEqual(NF.extension_complete('a/b.nl.mdic'), '.nl.mdic')
        self.assertEqual(NF.extension_derniere('a/b.nl.mdic'), '.mdic')
        self.assertEqual(NF.extension_complete('a/b'), '')

    def test_types_learned_with_minimum_share_and_hp_json(self):
        rows = [(i, 'm/x{}.meshc'.format(i), 'adf') for i in range(200)] + [(999, 'm/odd.meshc', None)]
        rows += [(1000 + i, 't/x{}.hmddsc'.format(i), 'hmddsc') for i in range(3)]
        etat = NF.Etat.depuis_lignes(rows)
        modele = NF.ModeleTypes(etat, [{b'.ddsc': ['avtx', 'dds']}])
        self.assertEqual(modele.types_pour('a.meshc'), {'adf'})           # 1 sur 201 : sous le seuil
        self.assertIn(None, modele.types_pour('a.hmddsc'))                # type pose d'apres le nom
        self.assertEqual(modele.types_pour('a.ddsc'), {'avtx', 'dds'})   # declare dans hp.json
        self.assertIsNone(modele.types_pour('a.inconnue'))
        self.assertTrue(modele.accepte('a.meshc', {'adf'}))
        self.assertFalse(modele.accepte('a.meshc', {'FSB5c'}))
        self.assertTrue(modele.accepte('a.meshc', {'symlink'}))
        self.assertIsNone(modele.accepte('a.inconnue', {'adf'}))

    def test_orphans_are_hashes_without_any_named_node(self):
        etat = NF.Etat.depuis_lignes([(1, 'a.txt', 'txt'), (1, None, 'txt'), (2, None, 'adf'), (2, None, None)])
        self.assertEqual(etat.sans_nom, {2: {'adf', None}})

    def test_ambiguous_hash_left_unnamed(self):
        # deux chemins reels du jeu de meme hash32 (journal du scan du 06/10 : 4022835d)
        a = 'models/hp_vegetation/z14/birch_paper/birch_paper_mdm_01.hrmeshc'
        b = 'worlds/world2/locations/hp_patagonia/lookout_towers/lookout_patagonia_08.blo'
        self.assertEqual(h32(a), h32(b))
        etat = NF.Etat.depuis_lignes([(1, 'x.hrmeshc', 'adf'), (2, 'x.blo', 'rtpc'), (h32(a), None, 'adf'),
                                      (h32(a), None, 'rtpc')])
        plan = RN.preparer(etat, NF.ModeleTypes(etat), [a, b], [])
        self.assertEqual(plan.ambigus, {h32(a): sorted([a, b])})
        self.assertEqual(plan.retenus, {})

    def test_node_type_unknown_to_deca_is_refused(self):
        # process_vhash_final indexe ftype_list par le type du noeud : 'NOT' le ferait echouer
        etat = NF.Etat.depuis_lignes([(1, 'a/x.raw', 'NOT'), (h32('a/y.raw'), None, 'NOT')])
        plan = RN.preparer(etat, NF.ModeleTypes(etat), ['a/y.raw'], [])
        self.assertEqual(plan.retenus, {})
        self.assertEqual([r[0] for r in plan.refus_type], ['a/y.raw'])

    def test_associated_names_follow_deca_rule(self):
        assoc = [{b'.ddsc': ['avtx'], b'.hmddsc': ['atx', 'META-NO-TYPE']}, {b'.nl': ['sarc'], b'.nl.mdic': ['mdi']}]
        self.assertEqual([n for n, _t in NF.associes('t/a.ddsc', assoc)], ['t/a.hmddsc'])
        self.assertEqual([n for n, _t in NF.associes('w/b.nl', assoc)], ['w/b.nl.mdic'])
        self.assertEqual(NF.associes('w/b.nl.mdic', assoc), [])          # derniere extension : .mdic

    def test_expected_false_positives(self):
        self.assertAlmostEqual(NF.faux_positifs_attendus(2 ** 20, 2 ** 12), 1.0)


class TestExeStrings(unittest.TestCase):
    def test_paths_and_patterns(self):
        from deca.exe_strings import path_strings
        paths, patterns = path_strings(EXE_APRES + b'kernel32.dll\0v1.5\0hello world.txt\0')
        self.assertIn(b'misc/from_exe.zzz', paths)
        self.assertIn(b'misc/later_exe.zzz', paths)       # antislashs -> '/', copie en minuscules
        self.assertIn(b'Misc/Later_Exe.zzz', paths)
        self.assertIn(b'ui/zoom%d/%02d.ddsc', patterns)
        self.assertNotIn(b'version 1.25', paths)
        self.assertNotIn(b'v1.5', paths)                   # extension numerique
        self.assertNotIn(b'hello world.txt', paths)        # espace


class TestRenommer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='cotw_renommer_')
        cls.project = build_project(cls.tmp.name)
        cls.db = os.path.join(cls.project, 'db', 'core.db')
        cls.liste = os.path.join(cls.tmp.name, 'noms.txt')
        Path(cls.liste).write_text('# candidats\n' + '\n'.join(c + '\tignore' for c in CANDIDATS) + '\n',
                                   encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def noms(self):
        conn = sqlite3.connect(self.db)
        try:
            return {_projet.to_text(v): t for v, t in conn.execute(
                'SELECT v_path, file_type FROM core_nodes WHERE v_path IS NOT NULL')}
        finally:
            conn.close()

    def test_0_scan_names_exe_strings(self):
        self.assertIn('misc/from_exe.zzz', self.noms())
        self.assertNotIn('misc/later_exe.zzz', self.noms())

    def test_1_simulation_writes_nothing(self):
        avant = instantane(self.db)
        self.assertEqual(RN.main(['ajouter', self.project, '--noms', self.liste, '--simulation']), 0)
        self.assertEqual(instantane(self.db), avant)
        self.assertFalse((Path(self.project) / RN.JOURNAL).exists())

    def test_2_plan(self):
        vfs = _projet.open_project(self.project)
        etat = NF.Etat.depuis_base(vfs)
        fa = vfs.game_info.file_assoc()
        plan = RN.preparer(etat, NF.ModeleTypes(etat, fa), RN.lire_noms([self.liste]), fa)
        retenus = {n for n, _t, _o in plan.retenus.values()}
        self.assertEqual(retenus, {'docs/notes_02.txt', 'data/blob_02.raw', 'cfg/a_02.bin', 'tex/t_01.hmddsc',
                                   'misc/thing.yyy'})
        self.assertEqual([r[0] for r in plan.refus_type], ['docs/fake_01.txt'])
        self.assertEqual(plan.non_controles, ['misc/thing.yyy'])
        self.assertEqual(plan.deja_nommes, 1)
        self.assertEqual(plan.hors_projet, 2)              # nowhere/x.txt et tex/t_01.ddsc
        self.assertEqual(plan.associes, 1)
        strict = RN.preparer(etat, NF.ModeleTypes(etat, fa), RN.lire_noms([self.liste]), fa, strict=True)
        self.assertNotIn('misc/thing.yyy', {n for n, _t, _o in strict.retenus.values()})
        vfs.db_conn.close()

    def test_3_apply_then_undo(self):
        avant = instantane(self.db)
        sans_nom_avant = self.sans_nom()
        self.assertEqual(RN.main(['ajouter', self.project, '--noms', self.liste]), 0)
        noms = self.noms()
        for n in ('docs/notes_02.txt', 'data/blob_02.raw', 'cfg/a_02.bin', 'misc/thing.yyy'):
            self.assertIn(n, noms)
        self.assertEqual(noms['tex/t_01.hmddsc'], 'hmddsc')    # type pose par DECA avec le nom
        self.assertNotIn('docs/fake_01.txt', noms)
        self.assertEqual(self.sans_nom(), sans_nom_avant - 5)
        vpaths = (Path(self.project) / 'vpaths.txt').read_text(encoding='utf-8')
        self.assertIn('cfg/a_02.bin\t', vpaths)

        # seconde session : chaines de l'executable remplace apres le scan
        exe = Path(self.tmp.name) / 'theHunterCotW' / 'theHunterCotW_F.exe'
        exe.write_bytes(EXE_APRES)
        self.assertEqual(RN.main(['ajouter', self.project, '--exe']), 0)
        self.assertIn('misc/later_exe.zzz', self.noms())

        self.assertEqual(RN.main(['annuler', self.project]), 0)
        self.assertEqual(instantane(self.db), avant)
        self.assertFalse((Path(self.project) / RN.JOURNAL).exists())
        self.assertEqual(len(list(Path(self.project).glob(RN.JOURNAL + '.annule-*'))), 1)

    def test_4_fmod_banks(self):
        noms = self.noms()
        bank = 'sound/fmod_banks/animal_test.fmod_bankc'
        self.assertEqual(noms.get(bank), 'riff')                    # nomme au scan par la banque de chaines
        self.assertEqual(noms.get('sound/fmod_banks/master.fmod_bankc'), 'riff')

        # projet scanne avant le fork : la banque n'avait pas de nom
        conn = sqlite3.connect(self.db)
        conn.execute('UPDATE core_nodes SET v_path = NULL WHERE v_hash = ?', (h32(bank),))
        conn.commit()
        conn.close()
        self.assertNotIn(bank, self.noms())
        avant = instantane(self.db)
        vfs = _projet.open_project(self.project)
        try:
            self.assertEqual(RN.noms_des_banques_fmod(vfs), ['sound/fmod_banks/animal_test.fmod_bankc',
                                                             'sound/fmod_banks/master.fmod_bankc'])
        finally:
            vfs.db_conn.close()
        self.assertEqual(RN.main(['ajouter', self.project, '--fmod']), 0)
        self.assertEqual(self.noms().get(bank), 'riff')
        self.assertEqual(RN.main(['annuler', self.project]), 0)
        self.assertEqual(instantane(self.db), avant)

    def test_5_remove_some_names(self):
        avant = instantane(self.db)
        self.assertEqual(RN.main(['ajouter', self.project, '--noms', self.liste]), 0)
        self.assertIn('cfg/a_02.bin', self.noms())
        self.assertEqual(RN.main(['retirer', self.project, '--nom', 'cfg/a_02.bin', '--nom', 'tex/t_01.hmddsc',
                                  '--nom', 'pas/dans_le_journal.txt']), 0)
        noms = self.noms()
        self.assertNotIn('cfg/a_02.bin', noms)
        self.assertNotIn('tex/t_01.hmddsc', noms)
        self.assertIn('docs/notes_02.txt', noms)                    # les autres noms restent
        conn = sqlite3.connect(self.db)
        try:
            self.assertIsNone(conn.execute('SELECT rowid FROM core_strings WHERE string = ?',
                                           ('cfg/a_02.bin',)).fetchone())
            self.assertIsNone(conn.execute('SELECT file_type FROM core_nodes WHERE v_hash = ?',
                                           (h32('tex/t_01.hmddsc'),)).fetchone()[0])   # type d'origine : aucun
        finally:
            conn.close()
        self.assertNotIn('cfg/a_02.bin\t', (Path(self.project) / 'vpaths.txt').read_text(encoding='utf-8'))
        self.assertEqual(RN.main(['annuler', self.project]), 0)    # annuler apres retirer : etat d'origine
        self.assertEqual(instantane(self.db), avant)

    def sans_nom(self):
        vfs = _projet.open_project(self.project)
        try:
            return len(NF.Etat.depuis_base(vfs).sans_nom)
        finally:
            vfs.db_conn.close()


if __name__ == '__main__':
    if '--spawn' in sys.argv:
        sys.argv.remove('--spawn')
        multiprocessing.set_start_method('spawn', force=True)
    unittest.main()
