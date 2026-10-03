#!/usr/bin/env python3
"""
Tests de cotw/stringlookup.py (tables de traduction .stringlookup : lecture, ajout de cles sans rien ecraser), de
cotw/traduction.py et des options --nom-affiche / --description de cotw/cloner_munition.py.

Les tables viennent de adf_fabrique.stringlookup(), qui reproduit la disposition de string_ids.stringlookup du jeu
(types, paires triees par hash, une ligne de dialogue et un sous-titre par cle, bloc Text a la fin de l'instance,
octets 04 00 00 00 apres l'instance, chaine de relocation). Chaque resultat est relu par le vrai lecteur ADF de
DECA, et sa chaine de relocation reverifiee.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_stringlookup.py
"""
import contextlib
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401
import adf_ecriture as AE  # noqa: E402
import adf_fabrique as F  # noqa: E402
import stringlookup as SL  # noqa: E402

SP_NAME = 'equipment_ammo_243_sp_name'
SP_DESC = 'equipment_ammo_243_sp_description'
NEW_NAME = 'equipment_ammo_243_test_13_name'
NEW_DESC = 'equipment_ammo_243_test_13_description'


def instance_bounds(data: bytes):
    """(debut, fin) absolus de l'unique instance."""
    base = AE.adf_base(data)
    off, size = struct.unpack_from('<II', data, base + struct.unpack_from('<I', data, base + 12)[0] + 8)
    return base + off, base + off + size


class TestVue(unittest.TestCase):
    def test_fixture_reads_back(self):
        data = F.stringlookup(F.LOOKUP_ENTRIES)
        vue = SL.Vue(data)
        self.assertEqual(vue.n_lang, 3)
        for key, texts in F.LOOKUP_ENTRIES.items():
            self.assertEqual(vue.texts(key), texts)
        self.assertEqual(vue.texts('cle_absente'), [None] * 3)
        adf = SL.decode(data)
        AE.verify_chain(adf, data, 0, exact=True)
        # disposition du jeu : Text est le dernier tableau de l'instance
        start, end = instance_bounds(data)
        self.assertEqual(start + vue.text_off + vue.text_len, end)

    def test_key_hash_matches_game(self):
        self.assertEqual(SL.key_hash(SP_NAME), 0x40911ad1)

    def test_dialogue_lines_parallel_to_pairs(self):
        vue = SL.Vue(F.stringlookup(F.LOOKUP_ENTRIES))
        for lang in range(vue.n_lang):
            pairs, lines = vue.pairs(lang), vue.lines(lang)
            self.assertEqual([h for h, _t in pairs], sorted(h for h, _t in pairs))
            self.assertEqual([h for h, _t in pairs], [x[0] for x in lines])
            for h, name, s_off, s_n in lines:
                self.assertEqual(SL.key_hash(vue.text(name)), h)
                self.assertEqual((s_n, vue.subtitle_hash(s_off)), (1, h))

    def test_not_a_stringlookup(self):
        with self.assertRaises(SL.EStringLookup):
            SL.Vue(F.xlsbook(F.STATS_SHEETS))


class TestTextesParLangue(unittest.TestCase):
    def test_default_for_all_languages(self):
        self.assertEqual(SL.textes_par_langue(['BALLES DE TEST']), ['BALLES DE TEST'] * 10)

    def test_language_overrides(self):
        t = SL.textes_par_langue(['BALLES DE TEST .243', 'en=.243 TEST BULLET', 'DE=TESTGESCHOSS'])
        self.assertEqual(t[SL.LANGUES.index('en')], '.243 TEST BULLET')
        self.assertEqual(t[SL.LANGUES.index('de')], 'TESTGESCHOSS')
        self.assertEqual(t[SL.LANGUES.index('fr')], 'BALLES DE TEST .243')
        self.assertEqual(t[SL.LANGUES.index('zh')], 'BALLES DE TEST .243')

    def test_all_languages_without_default(self):
        t = SL.textes_par_langue(['{}=T{}'.format(c, i) for i, c in enumerate(SL.LANGUES)])
        self.assertEqual(t, ['T{}'.format(i) for i in range(10)])

    def test_text_is_kept_as_typed(self):
        # un prefixe qui n'est pas une langue fait partie du texte ; "=" apres la langue aussi
        self.assertEqual(SL.textes_par_langue(['x=1'])[0], 'x=1')
        self.assertEqual(SL.textes_par_langue(['A', 'fr=a=b'])[1], 'a=b')
        self.assertEqual(SL.textes_par_langue(['  Espaces gardés  '])[0], '  Espaces gardés  ')

    def test_errors(self):
        with self.assertRaises(SL.EStringLookup):
            SL.textes_par_langue(['A', 'B'])                  # deux textes sans langue
        with self.assertRaises(SL.EStringLookup):
            SL.textes_par_langue(['fr=seulement le francais'])  # ni defaut ni toutes les langues
        with self.assertRaises(SL.EStringLookup):
            SL.language_index('xx')


class TestAdd(unittest.TestCase):
    def entries(self):
        return [SL.Entree(NEW_NAME, ['.243 TEST BULLET', 'BALLES DE TEST .243', 'TESTGESCHOSS .243'], SP_NAME),
                SL.Entree(NEW_DESC, ['Test.', 'Munition de test, accentuée.', 'Test.'])]

    def check(self, old: bytes, new: bytes, entries):
        vo, vn = SL.Vue(old), SL.Vue(new)
        for key in [k for k in F.LOOKUP_ENTRIES]:
            self.assertEqual(vn.texts(key), vo.texts(key))
        for e in entries:
            self.assertEqual(vn.texts(e.cle), e.textes)

    def test_add_two_keys(self):
        data = F.stringlookup(F.LOOKUP_ENTRIES)
        entries = self.entries()
        out, plan = SL.add(SL.decode, data, entries)
        SL.verify(SL.decode, data, out, plan)
        self.check(data, out, entries)
        self.assertTrue(plan.text_in_place)
        self.assertEqual(plan.head, 0)
        AE.verify_chain(SL.decode(out), out, 0, head=0)
        # les octets qui suivaient l'instance la suivent toujours
        _s, end = instance_bounds(out)
        self.assertEqual(out[end:end + 4], b'\x04\0\0\0')
        # ligne de dialogue copiee du modele, sous-titre propre
        lang = SL.decode(out).table_instance_full_values[0].value['Languages'].value[1].value
        h = SL.key_hash(NEW_NAME)
        line = next(x.value for x in lang['SortedDialogueLines'].value if x.value['Hash'].value == h)
        self.assertEqual(line['FMODEvent'].value, 1)
        self.assertEqual(line['CharacterName'].value, 0xdeadbeef)
        self.assertEqual(line['Flags'].value, 1219670572)
        sub = line['Subtitles'].value[0].value
        self.assertEqual((sub['LineHash'].value, sub['Start'].value, sub['Duration'].value), (h, 0.0, -1.0))

    def test_identical_texts_written_once(self):
        data = F.stringlookup(F.LOOKUP_ENTRIES)
        entry = SL.Entree(NEW_NAME, ['MEME TEXTE'] * 3, SP_NAME)
        out, plan = SL.add(SL.decode, data, [entry])
        SL.verify(SL.decode, data, out, plan)
        self.assertEqual(len({plan.text_offsets[(NEW_NAME, i)] for i in range(3)}), 1)
        grown = SL.Vue(out).text_len - SL.Vue(data).text_len
        self.assertEqual(grown, len(NEW_NAME) + 1 + len('MEME TEXTE') + 1)

    def test_text_not_at_end_is_copied(self):
        data = F.stringlookup(F.LOOKUP_ENTRIES, text_last=False)
        out, plan = SL.add(SL.decode, data, self.entries())
        SL.verify(SL.decode, data, out, plan)
        self.assertFalse(plan.text_in_place)
        self.check(data, out, self.entries())

    def test_second_addition_on_produced_file(self):
        # le fichier produit garde dans la chaine les anciennes copies des tableaux : il doit rester modifiable
        data = F.stringlookup(F.LOOKUP_ENTRIES)
        out, plan = SL.add(SL.decode, data, self.entries())
        third = SL.Entree('equipment_ammo_270_test_01_name', ['A', 'B', 'C'], 'equipment_ammo_270_sp_name')
        out2, plan2 = SL.add(SL.decode, out, [third])
        SL.verify(SL.decode, out, out2, plan2)
        self.check(data, out2, self.entries() + [third])

    def test_game_like_ten_languages(self):
        data = F.stringlookup(F.LOOKUP_ENTRIES_10)
        e = SL.Entree(NEW_NAME, SL.textes_par_langue(['BALLES DE TEST .243', 'en=.243 TEST BULLET']), SP_NAME)
        out, plan = SL.add(SL.decode, data, [e])
        SL.verify(SL.decode, data, out, plan)
        texts = SL.Vue(out).texts(NEW_NAME)
        self.assertEqual(texts[0], '.243 TEST BULLET')
        self.assertEqual(texts[1:], ['BALLES DE TEST .243'] * 9)

    def test_refusals(self):
        data = F.stringlookup(F.LOOKUP_ENTRIES)
        cases = {
            'cle existante': [SL.Entree(SP_NAME, ['a', 'b', 'c'])],
            'meme cle deux fois': [SL.Entree(NEW_NAME, ['a', 'b', 'c']), SL.Entree(NEW_NAME, ['d', 'e', 'f'])],
            'modele absent': [SL.Entree(NEW_NAME, ['a', 'b', 'c'], 'cle_absente')],
            'nombre de textes': [SL.Entree(NEW_NAME, ['a', 'b'])],
            'octet nul': [SL.Entree(NEW_NAME, ['a', 'b\0', 'c'])],
            'cle vide': [SL.Entree('', ['a', 'b', 'c'])],
            'aucune entree': [],
        }
        for what, entries in cases.items():
            with self.subTest(what), self.assertRaises(SL.EStringLookup):
                SL.add(SL.decode, data, entries)

    def test_verify_detects_tampering(self):
        data = F.stringlookup(F.LOOKUP_ENTRIES)
        out, plan = SL.add(SL.decode, data, self.entries())
        vue = SL.Vue(out)
        # 1. texte d'origine modifie
        t = bytearray(out)
        pos = vue.i0 + vue.text_off + vue.text_blob.index('MAGASIN'.encode())
        t[pos] = ord('X')
        with self.assertRaisesRegex(SL.EStringLookup, 'Text'):
            SL.verify(SL.decode, data, bytes(t), plan)
        # 2. paire de la nouvelle cle designant un autre texte
        t = bytearray(out)
        off, n = vue.array(vue.lang_pos[1] + vue.m['SortedPairs'])
        k = [h for h, _x in vue.pairs(1)].index(SL.key_hash(NEW_NAME))
        struct.pack_into('<I', t, vue.i0 + off + k * vue.pair_size + vue.m['TextOffset'], 0)
        with self.assertRaisesRegex(SL.EStringLookup, 'incorrect'):
            SL.verify(SL.decode, data, bytes(t), plan)
        # 3. dernier champ pointeur ajoute retire de la chaine de relocation
        t = bytearray(out)
        adf = SL.decode(out)
        fields = AE.pointer_fields(adf, 0)
        chain, _extra = AE.read_chain(bytes(t[vue.i0:vue.i0 + vue.size]), fields)
        before_last = chain[-2]
        struct.pack_into('<I', t, vue.i0 + before_last + 4, 0)
        with self.assertRaises(SL.EStringLookup):
            SL.verify(SL.decode, data, bytes(t), plan)


class TestTools(unittest.TestCase):
    """traduction.py et cloner_munition.py --nom-affiche / --description sur un faux projet DECA (vrai scan)."""

    @staticmethod
    def equipment_data() -> bytes:
        """equipment_data de test_separateurs, avec les cles de traduction de chaque munition, comme dans le jeu."""
        import deca.ff_rtpc_write as W
        from deca.ff_rtpc import k_type_str
        import test_separateurs as TS
        tree = W.rtpc_from_bytes(TS.equipment_data())
        for item in W.node_get_child(tree.root_node, 'ammo').child_table:
            name = bytes(W.node_get_prop(item, 'name').data).decode()
            stem = name[:-3] if name.endswith('_01') else name
            W.node_add_prop(item, W.make_prop('display_name', k_type_str, (stem + '_name').encode()))
            if name != TS.FMJ:                 # une munition sans description : refus explicite de --description
                W.node_add_prop(item, W.make_prop('display_description', k_type_str, (stem + '_description').encode()))
        return W.rtpc_to_binary(tree)

    @classmethod
    def setUpClass(cls):
        import faux_projet
        import test_separateurs as TS
        cls.TS = TS
        cls.tmp = tempfile.TemporaryDirectory(prefix='cotw_sl_')
        cls.strings = F.stringlookup(F.LOOKUP_ENTRIES_10)
        cls.project = faux_projet.build_fake_project(cls.tmp.name, [
            ('settings/hp_settings/equipment_data.bin', cls.equipment_data()),
            ('settings/spawn_ammunition_defs.bin', TS.spawn_defs()),
            (SL.STRING_IDS, cls.strings)])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_main(self, main, argv) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(argv)
        return buf.getvalue()

    def test_traduction_lire_et_ajouter(self):
        import traduction as T
        text = self.run_main(T.main, ['lire', self.project, SP_NAME, 'cle_absente'])
        self.assertIn('fr : BALLES À TÊTE MOLLE .243', text)
        self.assertIn('(absente)', text)
        out = Path(self.tmp.name) / 'trad'
        self.run_main(T.main, ['ajouter', self.project, '--ajouter', NEW_NAME + '=BALLES DE TEST .243',
                               '--texte', NEW_NAME + ':en=.243 TEST BULLET', '--ajouter', NEW_DESC + '=Test.',
                               '--modele', SP_NAME, '--sortie', str(out)])
        produced = (out / 'dropzone' / Path(SL.STRING_IDS)).read_bytes()
        vue = SL.Vue(produced)
        self.assertEqual(vue.texts(NEW_NAME)[:2], ['.243 TEST BULLET', 'BALLES DE TEST .243'])
        self.assertEqual(vue.texts(NEW_DESC), ['Test.'] * 10)
        self.assertEqual(vue.texts(SP_NAME), SL.Vue(self.strings).texts(SP_NAME))
        # empilement : lire et ajouter a partir du fichier produit (--base)
        text = self.run_main(T.main, ['lire', self.project, NEW_NAME, '--base', str(out / 'dropzone')])
        self.assertIn('en : .243 TEST BULLET', text)
        self.run_main(T.main, ['ajouter', self.project, '--base', str(out / 'dropzone'), '--ajouter', 'autre_cle=X',
                               '--sortie', str(out), '--ecraser'])
        vue = SL.Vue((out / 'dropzone' / Path(SL.STRING_IDS)).read_bytes())
        self.assertEqual((vue.texts('autre_cle')[0], vue.texts(NEW_NAME)[0]), ('X', '.243 TEST BULLET'))
        refused = [
            ['--ajouter', 'autre=X', '--sortie', str(out)],                         # sortie deja presente
            ['--ajouter', SP_NAME + '=X', '--sortie', str(out), '--ecraser'],       # cle existante
            ['--ajouter', 'sans_texte', '--sortie', str(out), '--ecraser'],         # pas de "="
            ['--ajouter', 'a=X', '--texte', 'b:en=Y', '--sortie', str(out), '--ecraser'],   # --texte d'une autre cle
            ['--ajouter', 'a=X', '--texte', 'a:xx=Y', '--sortie', str(out), '--ecraser'],   # langue inconnue
            ['--ajouter', 'a=X', '--ajouter', 'a=Y', '--sortie', str(out), '--ecraser'],    # cle deux fois
        ]
        for argv in refused:
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                self.run_main(T.main, ['ajouter', self.project] + argv)

    def test_cloner_nom_affiche_et_description(self):
        import cloner_munition as C
        import deca.ff_rtpc_write as W
        sp, new = self.TS.SP243, 'equipment_ammo_243_test_13'
        out = Path(self.tmp.name) / 'clone'
        self.run_main(C.main, [self.project, '--source', sp, '--nom', new, '--prix', '77',
                               '--nom-affiche', 'BALLES DE TEST .243', '--nom-affiche', 'en=.243 TEST BULLET',
                               '--description', 'Munition de test.', '--sortie', str(out)])
        dz = out / 'dropzone'
        tree = W.rtpc_from_bytes((dz / Path(C.EQUIPMENT_DATA)).read_bytes())
        _, item = C.find_item(tree.root_node, new, C.H_NAME)
        self.assertEqual(bytes(W.node_get_prop(item, C.H_DISPLAY_NAME).data).decode(), new + '_name')
        self.assertEqual(bytes(W.node_get_prop(item, C.H_DISPLAY_DESC).data).decode(), new + '_description')
        _, src = C.find_item(tree.root_node, sp, C.H_NAME)
        self.assertEqual(bytes(W.node_get_prop(src, C.H_DISPLAY_NAME).data).decode(), SP_NAME)
        vue = SL.Vue((dz / Path(SL.STRING_IDS)).read_bytes())
        self.assertEqual(vue.texts(new + '_name'), ['.243 TEST BULLET'] + ['BALLES DE TEST .243'] * 9)
        self.assertEqual(vue.texts(new + '_description'), ['Munition de test.'] * 10)
        report = (out / 'rapport_clonage.txt').read_text(encoding='utf-8')
        self.assertIn('display_name : "{}" -> "{}_name"'.format(SP_NAME, new), report)
        self.assertIn('string_ids : cles ajoutees dans les 10 langues', report)
        self.assertIn('    fr : BALLES DE TEST .243', report)

        # sans --nom-affiche ni --description : string_ids n'est pas produit, les cles de la source restent
        out2 = Path(self.tmp.name) / 'clone_sans_texte'
        self.run_main(C.main, [self.project, '--source', sp, '--nom', new, '--sortie', str(out2)])
        self.assertFalse((out2 / 'dropzone' / Path(SL.STRING_IDS)).exists())
        tree = W.rtpc_from_bytes((out2 / 'dropzone' / Path(C.EQUIPMENT_DATA)).read_bytes())
        _, item = C.find_item(tree.root_node, new, C.H_NAME)
        self.assertEqual(bytes(W.node_get_prop(item, C.H_DISPLAY_NAME).data).decode(), SP_NAME)

        # description seule, puis refus
        out3 = Path(self.tmp.name) / 'clone_description'
        self.run_main(C.main, [self.project, '--source', sp, '--nom', new, '--description', 'D', '--sortie', str(out3)])
        vue = SL.Vue((out3 / 'dropzone' / Path(SL.STRING_IDS)).read_bytes())
        self.assertEqual((vue.texts(new + '_name'), vue.texts(new + '_description')[0]), ([None] * 10, 'D'))
        # base ne contenant que le string_ids produit plus haut : la cle <nom>_name y existe deja
        base_si = Path(self.tmp.name) / 'base_si'
        (base_si / Path(SL.STRING_IDS)).parent.mkdir(parents=True)
        (base_si / Path(SL.STRING_IDS)).write_bytes((dz / Path(SL.STRING_IDS)).read_bytes())
        refused = [
            # fichier deja present dans la sortie
            ['--source', sp, '--nom', new, '--nom-affiche', 'X', '--sortie', str(out)],
            # deux textes sans langue
            ['--source', sp, '--nom', new, '--nom-affiche', 'X', '--nom-affiche', 'Y', '--sortie', str(out), '--ecraser'],
            # la source n'a pas de display_description
            ['--source', self.TS.FMJ, '--nom', 'equipment_ammo_762x54r_test_01', '--description', 'X',
             '--sortie', str(Path(self.tmp.name) / 'clone_fmj')],
            # cle deja dans string_ids
            ['--source', sp, '--nom', new, '--nom-affiche', 'X', '--base', str(base_si), '--sortie',
             str(Path(self.tmp.name) / 'clone_base')],
        ]
        for argv in refused:
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                self.run_main(C.main, [self.project] + argv)


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    unittest.main(verbosity=2)
