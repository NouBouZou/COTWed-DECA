#!/usr/bin/env python3
"""
Tests de cotw/pdb_2020.py : constantes du PDB -> noms, index des types, dispositions, vocabulaire, dictionnaire.

Les extraits de dumps reprennent mot pour mot le format de llvm-pdbutil 18 et des enregistrements reels du PDB
theHunterCotW_P.pdb (build du 2020-10-23) : constantes de CAnimalPopulationManager, classe CAnimalPopulation.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_pdb_2020.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _projet  # noqa: E402,F401
import pdb_2020 as P  # noqa: E402
from deca.hashes import hash32_func  # noqa: E402

GLOBALS = """\
  20488688 | S_CONSTANT [size = 32] `CStateGroup_hash`
           type = 0x1009 (const unsigned), value = 1908058674
  11037816 | S_CONSTANT [size = 24] `E_SS_GETINFO`
           type = 0x80F17 (EStreamerState), value = 1
  1000 | S_CONSTANT [size = 60] `CAnimalPopulationManager_GreatOnesSpawnProbability`
           type = 0x1009 (const unsigned), value = 3486858766
  1100 | S_CONSTANT [size = 60] `CPlayerProgressionManager_IsPartOfDlcPc`
           type = 0x1009 (const unsigned), value = 1227080418
  1200 | S_CONSTANT [size = 60] `NMissionHashes::event_name::scent_used::hash`
           type = 0x1009 (const unsigned), value = 1303991828
  1300 | S_CONSTANT [size = 52] `c_SomeBigMask`
           type = 0x1009 (const unsigned __int64), value = 4294967296
"""

TYPES = """\
    0xAACFD | LF_FIELDLIST [size = 400]
              - LF_MEMBER [name = `m_NumMales`, Type = 0x0074 (int), offset = 0, attrs = public]
              - LF_MEMBER [name = `m_NumGreatOneMales`, Type = 0x0075 (unsigned), offset = 8, attrs = private]
              - LF_MEMBER [name = `m_AnimalType`, Type = 0xAAD10, offset = 80, attrs = private]
              - LF_ONEMETHOD [name = `ShouldSpawnGreatOne`]
                type = 0xAAD03, vftable offset = -1, attrs = public
    0xAACFE | LF_CLASS [size = 64] `CAnimalPopulation`
              unique name: `.?AVCAnimalPopulation@@`
              vtable: <no type>, base list: <no type>, field list: 0xAACFD
              options: has ctor / dtor | contains nested class | has unique name, sizeof 552
    0xAAD0E | LF_CLASS [size = 64] `CAnimalType`
              unique name: `.?AVCAnimalType@@`
              vtable: <no type>, base list: <no type>, field list: <no type>
              options: forward ref (-> 0xAAD0F) | has unique name, sizeof 0
    0xAAD0F | LF_CLASS [size = 64] `CAnimalType`
              unique name: `.?AVCAnimalType@@`
              vtable: <no type>, base list: <no type>, field list: <no type>
              options: has unique name, sizeof 1104
    0xAAD0D | LF_MODIFIER [size = 12]
              referent = 0xAAD0F, modifiers = const
    0xAAD10 | LF_POINTER [size = 12]
              referent = 0xAAD0D, mode = pointer, opts = None, kind = ptr64
    0xAAD11 | LF_FIELDLIST [size = 64]
              - LF_ENUMERATE [GENDER_NONE = 0]
              - LF_ENUMERATE [GENDER_MALE = 1]
              - LF_ENUMERATE [GENDER_FEMALE = 2]
    0xAAD12 | LF_ENUM [size = 48] `EAnimalGender`
              unique name: `.?AW4EAnimalGender@@`
              field list: 0xAAD11, underlying type: 0x0020 (unsigned char)
              options: has unique name
    0xAAD13 | LF_FIELDLIST [size = 64]
              - LF_MEMBER [name = `m_Value`, Type = 0x0074 (int), offset = 0, attrs = public]
    0xAAD14 | LF_STRUCTURE [size = 48] `hkpThirdPartyThing`
              vtable: <no type>, base list: <no type>, field list: 0xAAD13
              options: sizeof 4
    0xB0001 | LF_FIELDLIST [size = 64]
              - LF_MEMBER [name = `m_AirDensity`, Type = 0x0040 (float), offset = 436, attrs = private]
    0xB0002 | LF_ARRAY [size = 16]
              size: 120, index type: 0x0023 (unsigned __int64), element type: 0x0040 (float)
    0xB0003 | LF_FIELDLIST [size = 64]
              - LF_MEMBER [name = `m_RandomSeed`, Type = 0x0075 (unsigned), offset = 0, attrs = private]
              - LF_MEMBER [name = `m_Lightning`, Type = 0xB0002, offset = 316, attrs = private]
              - LF_INDEX continuation = 0xB0001
    0xB0004 | LF_CLASS [size = 48] `CWeather`
              unique name: `.?AVCWeather@@`
              vtable: <no type>, base list: <no type>, field list: 0xB0003
              options: has ctor / dtor | has unique name, sizeof 848
"""


class TestConstantes(unittest.TestCase):
    def test_snake(self):
        self.assertEqual(P.snake('GreatOnesSpawnProbability'), 'great_ones_spawn_probability')
        self.assertEqual(P.snake('AIStateMachine'), 'ai_state_machine')
        self.assertEqual(P.snake('IsPartOfDlcPc'), 'is_part_of_dlc_pc')

    def test_lecture(self):
        constants = dict(P.read_constants(GLOBALS))
        self.assertIn('CStateGroup_hash', constants)
        self.assertNotIn('E_SS_GETINFO', constants)          # type enum : pas un hash
        self.assertNotIn('c_SomeBigMask', constants)         # 64 bits : pas un hash32

    def test_paires_prouvees(self):
        cases = {
            ('CAnimalPopulationManager_GreatOnesSpawnProbability', 0xcfd53e0e): 'great_ones_spawn_probability',
            ('CPlayerProgressionManager_IsPartOfDlcPc', 0x4923c2e2): 'is_part_of_dlc_pc',
            ('CStateGroup_hash', 1908058674): 'CStateGroup',
            ('NMissionHashes::event_name::scent_used::hash', 0x4db95614): 'scent_used',   # partie qualifiee
        }
        for (name, value), expected in cases.items():
            text, tried = P.resolve_constant(name, value)
            self.assertEqual(text, expected)
            self.assertEqual(hash32_func(text), value)
            self.assertGreater(tried, 0)

    def test_sans_chaine(self):
        # la chaine de ce hash n'est pas une forme du nom de la constante (etiquette commune a 367 constantes CXxx_Game)
        text, tried = P.resolve_constant('CDoorObject_Game', 0x5a6d4885)
        self.assertIsNone(text)
        self.assertLess(tried, 1000)                         # le cout des faux positifs reste borne


class TestTypes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        txt = Path(cls.tmp.name) / 'types.txt'
        txt.write_text(TYPES, encoding='utf-8')
        idx = Path(cls.tmp.name) / 'types.pkl'
        P.main(['index', str(txt), str(idx)])
        cls.index = P.TypeIndex(idx)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def layout(self, name):
        lines = []
        self.index.layout(name, out=lines.append)
        return lines

    def test_disposition(self):
        lines = self.layout('CAnimalPopulation')
        self.assertEqual(lines[0], 'class CAnimalPopulation  (sizeof 552)')
        self.assertIn('+0x8 unsigned', lines[2])
        self.assertTrue(lines[2].endswith('m_NumGreatOneMales'))
        self.assertIn('const CAnimalType*', lines[3])         # pointeur -> modificateur -> classe definie
        self.assertEqual(len(lines), 4)                      # la methode n'est pas un membre de donnees

    def test_tableau_et_suite_de_liste(self):
        # LF_ARRAY ecrit "size: N, ..., element type: T" ; LF_INDEX enchaine la suite de la liste des membres
        lines = self.layout('CWeather')
        self.assertIn('float[120 octets]', lines[2])
        self.assertTrue(lines[3].startswith('  +0x1B4 float'))
        self.assertTrue(lines[3].endswith('m_AirDensity'))

    def test_reference_anticipee_ignoree(self):
        self.assertEqual(self.index.by_name['CAnimalType'], 0xAAD0F)

    def test_enumeration(self):
        self.assertEqual(self.layout('EAnimalGender'),
                         ['enum EAnimalGender', '  GENDER_NONE = 0', '  GENDER_MALE = 1', '  GENDER_FEMALE = 2'])

    def test_vocabulaire(self):
        words = P.game_identifiers(self.index, GLOBALS)
        for w in ('CAnimalPopulation', 'm_NumGreatOneMales', 'ShouldSpawnGreatOne', 'GENDER_FEMALE',
                  'GreatOnesSpawnProbability', 'IsPartOfDlcPc'):
            self.assertIn(w, words)
        self.assertNotIn('m_Value', words)                   # membre d'un type Havok


class TestDictionnaire(unittest.TestCase):
    def test_noms_pdb_verifies(self):
        lines = [l.split('\t') for l in P.DICTIONARY.read_text(encoding='utf-8').splitlines()
                 if '\tpdb-' in l and not l.startswith('#')]
        self.assertGreater(len(lines), 7000)
        for cols in lines:
            self.assertEqual(hash32_func(cols[2]), int(cols[0], 16), cols)
        names = {cols[2] for cols in lines}
        for expected in ('great_ones_spawn_probability', 'great_ones_reserve_limit', 'great_ones_limit',
                         'is_part_of_dlc_pc', 'is_part_of_dlc_consoles', 'persist_new_game', 'max_males_in_group'):
            self.assertIn(expected, names)


if __name__ == '__main__':
    unittest.main()
