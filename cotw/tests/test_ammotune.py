#!/usr/bin/env python3
"""
Tests de cotw/ammotune.py (reglage balistique .ammotunec : lecture, modification) et des options --balistique /
--classes de cotw/cloner_munition.py et "ballistics" / "classes" de cotw/construire_mod.py.

Le reglage vient de adf_fabrique.ammotune() : types, disposition et valeurs de equipment_ammo_243_sp_01.ammotunec du
jeu (instance identique a l'octet pres, verifie le 2026-10-06). Chaque resultat est relu par le vrai lecteur ADF de
DECA, et sa chaine de relocation reverifiee.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_ammotune.py
"""
import contextlib
import io
import json
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
import ammotune as T  # noqa: E402

NAME = 'equipment_ammo_243_sp_01.ammotu'


def tune(levels=(2, 3, 4, 5, 6)) -> bytes:
    return F.ammotune(NAME, levels)


def instance(data: bytes):
    base = AE.adf_base(data)
    _p, off, size = AE.instance_entries(data, base, AE.Header(data, base))[0]
    return base + off, size


class TestLecture(unittest.TestCase):
    def test_fields_and_values_of_the_game_file(self):
        values, levels, fields = T.lire(tune())
        self.assertEqual(levels, [2, 3, 4, 5, 6])
        self.assertEqual(len(fields), 28)
        expect = {n: v for n, _o, _f, v in F.TUNE_243_SP}
        expect.update({'tracer_settings.' + n: v for n, _o, _f, v in F.TRACER_243_SP})
        for path, v in expect.items():
            got = values[path]
            self.assertAlmostEqual(got, v, places=6, msg=path) if isinstance(v, float) else self.assertEqual(got, v, path)
        self.assertEqual(values['tracer_settings.color.green'], 1.0)
        by = {c.chemin: c for c in fields}
        self.assertEqual((by['projectile_model_path'].genre, by['projectiles_per_shot'].fmt), ('hash', '<B'))
        self.assertEqual(by['tracer_settings.color.blue'].offset, 84 + 20 + 8)

    def test_resolve(self):
        fields = T.lire(tune())[2]
        self.assertEqual(T.resolve(fields, 'enabled').chemin, 'tracer_settings.enabled')
        self.assertEqual(T.resolve(fields, 'color.red').chemin, 'tracer_settings.color.red')
        self.assertEqual(T.resolve(fields, 'projectile_model_path').chemin, 'projectile_model_path')   # exact d'abord
        with self.assertRaisesRegex(T.EAmmotune, 'inconnu'):
            T.resolve(fields, 'model_path')
        twins = [T.Champ('a.level', 0, '<B', 'entier'), T.Champ('b.level', 1, '<B', 'entier')]
        with self.assertRaisesRegex(T.EAmmotune, 'ambigu'):
            T.resolve(twins, 'level')

    def test_parse_classes(self):
        self.assertEqual(T.parse_classes('3;4;5;6'), [3, 4, 5, 6])
        self.assertEqual(T.parse_classes('3-6'), [3, 4, 5, 6])
        self.assertEqual(T.parse_classes([7, 8]), [7, 8])
        self.assertEqual(T.parse_classes('9'), [9])
        for bad in ('', '0;1', '10', 'a;b', '6-x', []):
            with self.subTest(bad=bad), self.assertRaises(T.EAmmotune):
                T.parse_classes(bad)


class TestModifier(unittest.TestCase):
    def test_fixed_fields_written_in_place(self):
        data = tune()
        out, lines = T.modifier(data, {'enabled': '1', 'projectile_penetration': '0,08', 'color.red': '1',
                                       'tracer_settings.color.green': '0', 'projectiles_per_shot': '3',
                                       'projectile_model_path': '0x12345678'})
        self.assertEqual(len(out), len(data))
        values, levels, _f = T.lire(out)
        self.assertEqual(values['tracer_settings.enabled'], 1)
        self.assertEqual(values['projectile_penetration'], struct.unpack('<f', struct.pack('<f', 0.08))[0])
        self.assertEqual((values['tracer_settings.color.red'], values['tracer_settings.color.green']), (1.0, 0.0))
        self.assertEqual((values['projectiles_per_shot'], values['projectile_model_path']), (3, 0x12345678))
        self.assertEqual(levels, [2, 3, 4, 5, 6])
        # seuls les octets des champs demandes changent
        start, _size = instance(data)
        changed = {i for i, (a, b) in enumerate(zip(data, out)) if a != b}
        allowed = {start + o + k for o, n in ((84, 1), (40, 4), (104, 4), (108, 4), (60, 1), (68, 4)) for k in range(n)}
        self.assertTrue(changed and changed <= allowed, sorted(changed - allowed))
        self.assertIn('balistique : tracer_settings.enabled : 0 -> 1', lines)
        self.assertIn('balistique : projectile_penetration : 0.106935985 -> 0.0799999982', lines)

    def test_classes_in_place_and_resized(self):
        data = tune()
        same, lines = T.modifier(data, {}, '3;4;5;6;7')                 # meme nombre : sur place
        self.assertEqual((len(same), T.lire(same)[1]), (len(data), [3, 4, 5, 6, 7]))
        self.assertEqual(lines, ['balistique : ammunition_class : 2;3;4;5;6 -> 3;4;5;6;7'])
        fewer, _l = T.modifier(data, {}, '3-6')                         # moins de classes : sur place
        self.assertEqual((len(fewer), T.lire(fewer)[1]), (len(data), [3, 4, 5, 6]))
        AE.verify_chain(T.decode(fewer), fewer, 0, head=0, exact=True)
        for levels in ([1, 2, 3, 4, 5, 6, 7], [1, 2, 3, 4, 5, 6, 7, 8, 9]):
            with self.subTest(levels=levels):
                out, _l = T.modifier(data, {'mass': '0.01'}, levels)    # plus de classes : recopie en fin d'instance
                self.assertGreater(len(out), len(data))
                self.assertEqual(T.lire(out)[1], levels)
                self.assertAlmostEqual(T.lire(out)[0]['mass'], 0.01, places=7)
                adf = T.decode(out)
                AE.verify_chain(adf, out, 0, head=0)
                self.assertEqual(AE.instance_names(out), [NAME])
                again, _l = T.modifier(out, {'enabled': '1'}, '1-9')    # seconde passe sur un fichier agrandi
                self.assertEqual(T.lire(again)[1], list(range(1, 10)))
        unchanged, _l = T.modifier(data, {}, [2, 3, 4, 5, 6])
        self.assertEqual(unchanged, data)

    def test_refusals(self):
        data = tune()
        cases = [({'enabled': '300'}, None), ({'inconnu': '1'}, None), ({'max_range': 'abc'}, None),
                 ({'max_range': 'nan'}, None), ({'max_range': '1e39'}, None), ({}, '0;1'), ({}, '')]
        for values, classes in cases:
            with self.subTest(values=values, classes=classes), self.assertRaises(T.EAmmotune):
                T.modifier(data, values, classes)
        with self.assertRaisesRegex(T.EAmmotune, 'deux fois'):
            T.modifier(data, {'enabled': '1', 'tracer_settings.enabled': '0'})
        # reglage sans classe (tableau vide, pas de chaine de relocation) : ajout refuse, autres champs possibles
        empty = tune(())
        self.assertEqual(T.lire(empty)[1], [])
        with self.assertRaisesRegex(T.EAmmotune, 'aucune'):
            T.modifier(empty, {}, '3')
        self.assertEqual(T.lire(T.modifier(empty, {'enabled': '1'})[0])[0]['tracer_settings.enabled'], 1)
        with self.assertRaises(T.EAmmotune):
            T.lire(F.xlsbook(F.STATS_SHEETS))

    def test_verify_detects_tampering(self):
        data = tune()
        out, _l = T.modifier(data, {'enabled': '1'})
        start, _s = instance(out)
        bad = bytearray(out)
        bad[start + 16] ^= 0x40                                          # max_range, non demande
        with self.assertRaisesRegex(T.EAmmotune, 'max_range'):
            T.verifier(data, bytes(bad), {'tracer_settings.enabled': 1}, None)
        with self.assertRaisesRegex(T.EAmmotune, 'classes'):
            T.verifier(data, out, {'tracer_settings.enabled': 1}, [3])


class TestEntite(unittest.TestCase):
    def test_entity_tuning_modified_other_entries_kept(self):
        import sarc as S
        import test_entite as TE
        ee = TE.entity(TE.SRC, tuning='jeu')
        out, lines = T.modifier_entite(ee, {'enabled': '1'}, '3-6')
        a, b = S.parse(ee), S.parse(out)
        self.assertEqual([e.path for e in a.entries], [e.path for e in b.entries])
        self.assertEqual(a.entries[0].data, b.entries[0].data)          # .epe intact
        self.assertTrue(b.entries[2].is_link and b.entries[2].length == a.entries[2].length)
        path, values, levels, _f = T.lire_entite(out)
        self.assertEqual((path, values['tracer_settings.enabled'], levels), (TE.DIR + TE.SRC + '.ammotunec', 1, [3, 4, 5, 6]))
        self.assertTrue(all(line.startswith(TE.DIR + TE.SRC + '.ammotunec : balistique') for line in lines))
        with self.assertRaisesRegex(T.EAmmotune, '0 reglage'):
            T.modifier_entite(TE.entity(TE.SRC), {'enabled': '1'})     # ADF d'adf_minimal : pas un reglage


class TestOutils(unittest.TestCase):
    """cloner_munition --balistique / --classes, construire_mod "ballistics" / "classes", lecture par ammotune.py,
    sur un faux projet DECA (vrai scan)."""

    @classmethod
    def setUpClass(cls):
        import faux_projet
        import test_entite as TE
        import test_separateurs as TS
        cls.TE, cls.TS = TE, TS
        cls.src = TS.SOLD[0]                                            # equipment_ammo_308_pt_01
        cls.tmp = tempfile.TemporaryDirectory(prefix='cotw_ammotune_')
        cls.project = faux_projet.build_fake_project(cls.tmp.name, [
            ('settings/hp_settings/equipment_data.bin', TS.equipment_data()),
            ('settings/spawn_ammunition_defs.bin', TS.spawn_defs()),
            (TE.DIR + cls.src + '.ee', TE.entity(cls.src, tuning='jeu'))])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_main(self, main, argv) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(argv)
        return buf.getvalue()

    def entity_tuning(self, out: Path, name: str):
        ee = (out / 'dropzone' / Path(self.TE.DIR + name + '.ee')).read_bytes()
        return T.lire_entite(ee)

    def test_cloner_ballistics(self):
        import cloner_munition as C
        new = 'equipment_ammo_308_test_15'
        out = Path(self.tmp.name) / 'clone'
        self.run_main(C.main, [self.project, '--source', self.src, '--nom', new, '--entite-propre',
                               '--balistique', 'enabled=1', '--balistique', 'projectile_penetration=0.08',
                               '--balistique', 'color.red=1', '--classes', '3-6', '--sortie', str(out)])
        _p, values, levels, _f = self.entity_tuning(out, new)
        self.assertEqual((values['tracer_settings.enabled'], values['tracer_settings.color.red'], levels), (1, 1.0, [3, 4, 5, 6]))
        report = (out / 'rapport_clonage.txt').read_text(encoding='utf-8')
        self.assertIn('balistique : tracer_settings.enabled : 0 -> 1', report)
        self.assertIn('balistique : ammunition_class : 2;3;4;5;6 -> 3;4;5;6', report)
        # la source n'est pas touchee : son entite n'est pas dans le dropzone, celle du jeu garde ses valeurs
        self.assertFalse((out / 'dropzone' / Path(self.TE.DIR + self.src + '.ee')).exists())
        # lecture par ammotune.py : la copie depuis le dropzone, la source depuis le jeu
        text = self.run_main(T.main, [self.project, new, '--base', str(out / 'dropzone')])
        self.assertIn('ammunition_class', text)
        self.assertRegex(text, r'tracer_settings\.enabled\s+1 ')
        text = self.run_main(T.main, [self.project, self.src])
        self.assertRegex(text, r'tracer_settings\.enabled\s+0 ')
        refused = [
            ['--nom', 'equipment_ammo_308_test_16', '--balistique', 'enabled=1'],                     # sans entite propre
            ['--nom', 'equipment_ammo_308_test_16', '--entite-propre', '--balistique', 'enabled'],    # pas de "="
            ['--nom', 'equipment_ammo_308_test_16', '--entite-propre', '--balistique', 'enabled=1',
             '--balistique', 'enabled=0'],                                                           # deux fois
            ['--nom', 'equipment_ammo_308_test_16', '--entite-propre', '--classes', '0'],            # classe hors 1-9
            ['--nom', 'equipment_ammo_308_test_16', '--entite-propre', '--balistique', 'inconnu=1'],  # champ inconnu
        ]
        for argv in refused:
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                self.run_main(C.main, [self.project, '--source', self.src] + argv +
                              ['--sortie', str(Path(self.tmp.name) / 'refus')])

    def test_mod_file_ballistics(self):
        import construire_mod as CMOD
        new = 'equipment_ammo_308_test_17'
        mod = {'format': 'cotwed-mod', 'version': 1, 'name': 'balistique', 'witness_prices': {}, 'changes': [],
               'ammo': [{'source': self.src, 'name': new, 'entity': 'own', 'price': 99,
                         'ballistics': {'tracer_settings.enabled': 1, 'projectile_penetration': 0.08,
                                        'projectile_model_path': '0x12345678'},
                         'classes': '4;5'}]}
        mf = CMOD.from_dict(mod)
        m = mf.mod.munitions[0]
        self.assertEqual((m.balistique['projectile_penetration'], m.classes), ('0.08', [4, 5]))
        back = CMOD.munition_to_dict(m)
        self.assertEqual(back['ballistics'], {'tracer_settings.enabled': 1, 'projectile_penetration': 0.08,
                                              'projectile_model_path': '0x12345678'})
        self.assertEqual(back['classes'], [4, 5])
        self.assertNotIn('ballistics', CMOD.munition_to_dict(CMOD.from_dict(dict(mod, ammo=[
            {'source': self.src, 'name': new}])).mod.munitions[0]))
        path = Path(self.tmp.name) / 'balistique.cotwmod.json'
        path.write_text(json.dumps(mod), encoding='utf-8')
        out = Path(self.tmp.name) / 'mod'
        self.run_main(CMOD.main, [self.project, str(path), '--sortie', str(out)])
        _p, values, levels, _f = self.entity_tuning(out, new)
        self.assertEqual((values['tracer_settings.enabled'], values['projectile_model_path'], levels), (1, 0x12345678, [4, 5]))
        for bad in ({'ballistics': [1]}, {'ballistics': {'enabled': True}}, {'classes': '0'}, {'entity': 'source',
                                                                                              'ballistics': {'enabled': 1}}):
            with self.subTest(bad=bad), self.assertRaises((CMOD.EMod, CMOD.CM.EClone)):
                d = dict(mod, ammo=[dict(mod['ammo'][0], **bad)])
                CMOD.from_dict(d).mod.munitions[0].valider()


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    unittest.main(verbosity=2)
