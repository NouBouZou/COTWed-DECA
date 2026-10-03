#!/usr/bin/env python3
"""
Tests de cotw/adf_ecriture.py (agrandir des tableaux d'un ADF), cotw/xlsbook.py (ajouter une ligne a une feuille),
cotw/adf_ajouter_ligne.py, de l'option --stats-de de cloner_munition.py et de adf_ecriture_controle.py.

Les ADF viennent de adf_fabrique.py, dans les deux dispositions possibles (tables avant ou apres les donnees),
avec ou sans seconde instance et petit en-tete de 5 octets, avec la chaine de relocation du jeu. Chaque resultat
est relu par le vrai lecteur ADF de DECA, et sa chaine de relocation est reverifiee ; l'export xlsx de DECA
(adf_export_xlsx_0x0b73315d) sert de second lecteur, independant de xlsbook.py.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_adf_ecriture.py
"""
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401
import adf_ajouter_ligne as AL  # noqa: E402
import adf_ecriture as AE  # noqa: E402
import adf_ecriture_controle as CTL  # noqa: E402
import adf_fabrique as F  # noqa: E402
import cloner_munition as C  # noqa: E402
import faux_projet  # noqa: E402
import xlsbook as X  # noqa: E402

SP = 'equipment_ammo_243_sp_01'
NEW = 'equipment_ammo_243_test_03'
STATS = AL.STATS_UI
LAYOUTS = [(order, prefix) for order in ('tables_avant', 'donnees_avant') for prefix in (b'', b'\x01\x02\x03\x04\x05')]


def decode(data: bytes):
    from deca.ff_adf import Adf
    from deca.file import ArchiveFile
    a = Adf()
    a.deserialize(ArchiveFile(io.BytesIO(data[AE.adf_base(data):])))
    return a


def rows(data: bytes, sheet: int = 0):
    return X.sheet_rows(AE.plain_values(decode(data)), sheet)


class TestInsert(unittest.TestCase):
    def test_moving_every_array_keeps_all_values(self):
        for order, prefix in LAYOUTS:
            with self.subTest(order=order, prefix=len(prefix)):
                data = F.xlsbook(F.STATS_SHEETS, order=order, extra_instance=True, prefix=prefix)
                adf = decode(data)
                moved, added = CTL.check_moves(decode, data, adf, 64)
                self.assertGreaterEqual(moved, 8)      # Sheet, 2 CellIndex, Cell, StringData, ValueData, BoolData...
                self.assertGreater(added, 0)

    def test_offsets_after_insertion_point_move(self):
        for order, prefix in LAYOUTS:
            with self.subTest(order=order, prefix=len(prefix)):
                data = F.xlsbook(F.STATS_SHEETS, order=order, extra_instance=True, prefix=prefix)
                adf = decode(data)
                ed = AE.InstanceEditor(adf, data, 0)
                ed.grow(adf.table_instance_full_values[0].value['ColorData'], [struct.pack('<I', 0x123456)])
                out = ed.result()
                new = decode(out)
                base = AE.adf_base(out)
                self.assertEqual(struct.unpack_from('<I', out, base + AE.Header.TOTAL_POSITION)[0], len(out) - base)
                self.assertEqual((len(out) - len(data)) % AE.INSERT_ALIGN, 0)
                old0, new0 = adf.table_instance[0], new.table_instance[0]
                self.assertEqual(new0.offset, old0.offset)
                self.assertGreater(new0.size, old0.size)
                old1, new1 = adf.table_instance[1], new.table_instance[1]
                self.assertEqual(new1.offset - old1.offset, len(out) - len(data))   # seconde instance decalee
                self.assertEqual(new1.offset % 16, old1.offset % 16)
                self.assertEqual(AE.plain_values(new, 1), {'Version': 7, 'Label': b'meta_label'})
                self.assertEqual(AE.plain_values(new)['ColorData'], [0, 0xFFFFFF, 0x123456])
                self.assertEqual(new.map_stringhash[0x1122334455667788].value, b'equipment_ammo')
                self.assertEqual(out[:base], prefix)
                # tables placees avant l'instance : noms, types et StringHash inchanges, a la meme place ; seuls
                # l'en-tete (offsets, taille totale) et la table des instances (offset, taille) changent
                if order == 'tables_avant':
                    h = AE.Header(data, base)
                    first, table = base + h.tables['noms'][1], base + h.tables['instances'][1]
                    self.assertEqual(out[first:table], data[first:table])
                    self.assertEqual(AE.Header(out, base).tables, h.tables)

    def test_chain_of_fixture_matches_game_shape(self):
        data = F.xlsbook(F.STATS_SHEETS, extra_instance=True)
        adf = decode(data)
        chain = AE.verify_chain(adf, data, 0)
        self.assertEqual(chain[0], 0)                                      # tete : premier champ pointeur
        self.assertNotIn(112, chain)                                       # tableau vide (cible 0) hors chaine
        self.assertEqual(AE.verify_chain(adf, data, 1), [8])               # Meta : un seul champ, lien 0

    def test_moved_array_copies_are_linked(self):
        data = F.xlsbook(F.STATS_SHEETS)
        adf = decode(data)
        ed = AE.InstanceEditor(adf, data, 0)
        before = list(ed.chain)
        sd = adf.table_instance_full_values[0].value['StringData']
        header = ed.rel(sd.info_offset)
        _old, count = ed.array_state(header)
        ed.grow(sd, [])
        ed.link()
        new_offset, _n = ed.array_state(header)
        copies = [new_offset + 8 * i for i in range(count)]
        self.assertEqual(ed.final_chain, sorted(set(before) | set(copies)))
        # partie d'origine : liens inchanges sauf le dernier (0 -> premiere copie)
        for a, b in zip(before[:-1], before[1:]):
            self.assertEqual(struct.unpack_from('<I', ed.buf, a + 4)[0], b - a)
        self.assertEqual(struct.unpack_from('<I', ed.buf, before[-1] + 4)[0], copies[0] - before[-1])
        self.assertEqual(struct.unpack_from('<I', ed.buf, copies[-1] + 4)[0], 0)
        self.assertEqual(struct.unpack_from('<I', ed.buf, header + 12)[0], 0)   # 4e champ de l'en-tete garde

    def test_refuses_without_chain_and_on_head_change(self):
        legacy = F.xlsbook(F.STATS_SHEETS, chain=False)      # longueurs et flags a la place des liens
        with self.assertRaisesRegex(AE.EAdfEcriture, 'chaine de relocation'):
            AE.InstanceEditor(decode(legacy), legacy, 0)
        with self.assertRaisesRegex(X.EXls, 'chaine de relocation'):
            try:
                X.add_row(decode(legacy), legacy, SP, NEW)
            except AE.EAdfEcriture as e:
                raise X.EXls(str(e))
        # instance dont le premier champ pointeur est un tableau vide : le remplir changerait la tete
        t = F.Types()
        t.struct('Tiny', 0x5EE73001, 24, 8, [('A', F.A_U32, 16, 0), ('B', F.STRING, 8, 16)])
        t.array('A[uint32]', F.A_U32, F.typedef_u32)
        b = F.Blob(24)
        b.pointer(16, b.alloc(b'tiny\0', 8))
        b.link()
        tiny = F.build(t, [('tiny', 0x5EE73001, bytes(b.b))])
        adf = decode(tiny)
        ed = AE.InstanceEditor(adf, tiny, 0)
        ed.grow(adf.table_instance_full_values[0].value['A'], [struct.pack('<I', 5)])
        with self.assertRaisesRegex(AE.EAdfEcriture, 'tete de la chaine'):
            ed.result()

    def test_refusals(self):
        data = F.xlsbook(F.STATS_SHEETS, order='donnees_avant')
        adf = decode(data)
        start, size = adf.table_instance[0].offset, adf.table_instance[0].size
        original = data[start:start + size]
        bad_total = bytearray(data)
        struct.pack_into('<I', bad_total, AE.Header.TOTAL_POSITION, len(data) + 4)
        with self.assertRaisesRegex(AE.EAdfEcriture, 'taille totale'):
            AE.insert(bytes(bad_total), 0, original + b'x')
        inside = bytearray(data)
        struct.pack_into('<I', inside, AE.Header.offset_position(0, 2), start + 8)   # StringHash dans l'instance
        with self.assertRaisesRegex(AE.EAdfEcriture, 'interieur'):
            AE.insert(bytes(inside), 0, original + b'x')
        with self.assertRaisesRegex(AE.EAdfEcriture, 'raccourcie'):
            AE.insert(data, 0, original[:-1])
        with self.assertRaisesRegex(AE.EAdfEcriture, 'absente'):
            AE.insert(data, 3, original)
        with self.assertRaises(AE.EAdfEcriture):
            AE.adf_base(b'RTPC' + b'\0' * 60)

    def test_walk_chain_errors(self):
        buf = bytearray(64)
        struct.pack_into('<II', buf, 0, 40, 8)          # 0 -> 8
        struct.pack_into('<II', buf, 8, 40, 100)        # 8 -> 108 : hors de l'instance
        with self.assertRaisesRegex(AE.EAdfEcriture, 'hors de l'):
            AE.walk_chain(bytes(buf), 0)
        struct.pack_into('<II', buf, 8, 40, 6)          # lien non multiple de 4
        with self.assertRaisesRegex(AE.EAdfEcriture, 'invalide'):
            AE.walk_chain(bytes(buf), 0)
        struct.pack_into('<II', buf, 8, 40, 0)
        self.assertEqual(AE.walk_chain(bytes(buf), 0), [0, 8])
        self.assertEqual(AE.read_chain(bytes(buf), [0, 8, 24]), ([0, 8], []))   # 24 : cible 0, hors chaine
        struct.pack_into('<I', buf, 16, 40)                                      # 16 : cible non nulle, non rattache
        with self.assertRaisesRegex(AE.EAdfEcriture, 'non rattache'):
            AE.read_chain(bytes(buf), [0, 8, 16])
        self.assertEqual(AE.read_chain(bytes(buf), [0]), ([0, 8], [8]))         # 8 : en trop (ancienne copie)


class TestAddRow(unittest.TestCase):
    def test_add_row_all_layouts_and_rules(self):
        for order, prefix in LAYOUTS:
            with self.subTest(order=order, prefix=len(prefix)):
                data = F.xlsbook(F.STATS_SHEETS, order=order, extra_instance=True, prefix=prefix)
                out, plan = X.add_row(decode(data), data, SP, NEW)
                added = X.verify(decode, data, out, plan)
                self.assertEqual(added, [NEW, '2-6', 0.25, 0.75, 'sp'])
                self.assertEqual(rows(out)[:-1], rows(data))                       # lignes existantes intactes
                self.assertEqual(rows(out, 1), rows(data, 1))                      # autre feuille intacte
                self.assertEqual(plan.new_row, 4)
                adf = decode(out)
                chain = AE.verify_chain(adf, out, 0, plan.chain_head)
                sd = adf.table_instance_full_values[0].value['StringData']
                ed = AE.InstanceEditor(adf, out, 0)      # un fichier produit se relit (anciennes copies en plus)
                self.assertGreater(len(ed.dead), 0)
                pos, header = ed.element(sd, plan.string_index)
                self.assertIn(pos, chain)                                   # nouvel en-tete rattache
                self.assertEqual(struct.unpack('<II', header)[1], 0)        # dernier de la chaine
                self.assertEqual(chain[-1], pos)
                self.assertEqual(AE.verify_chain(adf, out, 1, exact=True), [8])    # autre instance intacte

    def test_trailer_stays_after_instance_and_arrays_aligned(self):
        trailer = b'\x04\0\0\0\0\0'
        for order in ('donnees_avant', 'tables_avant'):
            with self.subTest(order=order):
                data = F.xlsbook(F.STATS_SHEETS, order=order, extra_instance=True, trailer=trailer)
                out, plan = X.add_row(decode(data), data, SP, NEW)
                X.verify(decode, data, out, plan)
                new = decode(out)
                for ins in new.table_instance:                          # octets colles a la fin de chaque instance
                    end = ins.offset + ins.size
                    self.assertEqual(out[end:end + len(trailer)], trailer)
                h, h0 = AE.Header(out, 0), AE.Header(data, 0)
                for name, (count, offset) in h.tables.items():     # chaque table garde son alignement
                    if count:
                        self.assertEqual(offset % 16, h0.tables[name][1] % 16, name)
                start = new.table_instance[0].offset
                full = new.table_instance_full_values[0].value
                for key in ('Sheet', 'Cell', 'StringData', 'ValueData', 'BoolData', 'ColorData', 'Attribute'):
                    target, _link, count = struct.unpack_from('<III', out, full[key].info_offset)
                    if count:
                        self.assertEqual((start + target) % 8, 0, key)     # donnees de tableau alignees sur 8
                for s in full['Sheet'].value:
                    target, _link, count = struct.unpack_from('<III', out, s.value['CellIndex'].info_offset)
                    self.assertEqual((start + target) % 8, 0)

    def test_empty_string_cell_is_copied(self):
        sheets = [('ammo', [['name', 'note'], [SP, ''], ['equipment_ammo_243_pt_01', 'pt']])]
        data = F.xlsbook(sheets)
        out, plan = X.add_row(decode(data), data, SP, NEW)
        X.verify(decode, data, out, plan)
        self.assertEqual(rows(out)[-1], [NEW, ''])

    def test_value_cells_are_shared_with_template(self):
        data = F.xlsbook(F.STATS_SHEETS)
        before = AE.plain_values(decode(data))
        out, plan = X.add_row(decode(data), data, SP, NEW)
        after = AE.plain_values(decode(out))
        self.assertEqual(len(after['Cell']), len(before['Cell']) + 1)            # une seule cellule nouvelle
        self.assertEqual(len(after['StringData']), len(before['StringData']) + 1)
        self.assertEqual(len(after['ValueData']), len(before['ValueData']))       # aucune valeur copiee
        cols = int(after['Sheet'][0]['Cols'])
        template = list(after['Sheet'][0]['CellIndex'][cols:2 * cols])
        added = list(after['Sheet'][0]['CellIndex'][-cols:])
        self.assertEqual(added[1:], template[1:])
        self.assertEqual(added[0], plan.cell_index)

    def test_two_rows_in_sequence(self):
        data = F.xlsbook(F.STATS_SHEETS, order='donnees_avant', extra_instance=True)
        out, lines = AL.add_rows(decode, data, [(SP, NEW), ('equipment_ammo_762x54r_fmj_01', 'equipment_ammo_243_test_04')])
        self.assertEqual(len(lines), 2)
        r = rows(out)
        self.assertEqual(r[-2][0], NEW)
        self.assertEqual(r[-1], ['equipment_ammo_243_test_04', '3-7', 0.5, 0.5, 'fmj'])
        self.assertEqual(AE.plain_values(decode(out), 1)['Label'], b'meta_label')

    def test_refusals(self):
        data = F.xlsbook(F.STATS_SHEETS)
        with self.assertRaisesRegex(X.EXls, 'deja une ligne'):
            X.add_row(decode(data), data, SP, 'equipment_ammo_243_pt_01')
        with self.assertRaisesRegex(X.EXls, 'aucune ligne'):
            X.add_row(decode(data), data, 'equipment_absent_01', NEW)
        with self.assertRaisesRegex(X.EXls, 'identique'):
            X.add_row(decode(data), data, SP, SP)
        # modele present dans deux feuilles : la feuille doit etre precisee
        sheets = F.STATS_SHEETS + [('ammo_copy', [['name', 'x'], [SP, 1.0]])]
        data2 = F.xlsbook(sheets)
        with self.assertRaisesRegex(X.EXls, 'preciser la feuille'):
            X.add_row(decode(data2), data2, SP, NEW)
        out, plan = X.add_row(decode(data2), data2, SP, NEW, sheet_name='ammo_copy')
        X.verify(decode, data2, out, plan)
        self.assertEqual(rows(out, 2)[-1], [NEW, 1.0])
        with self.assertRaisesRegex(X.EXls, 'pas XLSBook'):
            meta = F.xlsbook(F.STATS_SHEETS, extra_instance=True)
            X.add_row(decode(meta), meta, SP, NEW, instance=1)

    def test_verify_detects_unlinked_pointer(self):
        data = F.xlsbook(F.STATS_SHEETS)
        out, plan = X.add_row(decode(data), data, SP, NEW)
        adf = decode(out)
        chain = AE.verify_chain(adf, out, 0, plan.chain_head)
        broken = bytearray(out)
        base = AE.adf_base(out) + adf.table_instance[0].offset
        struct.pack_into('<I', broken, base + chain[-2] + 4, 0)                 # chaine coupee avant le nouveau nom
        with self.assertRaisesRegex(X.EXls, 'non rattache'):
            X.verify(decode, data, bytes(broken), plan)

    def test_verify_detects_tampering(self):
        data = F.xlsbook(F.STATS_SHEETS)
        out, plan = X.add_row(decode(data), data, SP, NEW)
        tampered = bytearray(out)
        adf = decode(out)
        vd = adf.table_instance_full_values[0].value['ValueData']
        struct.pack_into('<f', tampered, vd.data_offset, 9.0)                    # une stat modifiee
        with self.assertRaises(X.EXls):
            X.verify(decode, data, bytes(tampered), plan)

    def test_deca_xlsx_export_sees_new_row(self):
        import openpyxl
        from deca.export_import_adf import adf_export_xlsx_0x0b73315d
        data = F.xlsbook(F.STATS_SHEETS)
        out, _plan = X.add_row(decode(data), data, SP, NEW)

        class Logger:
            def log(self, *a, **k):
                pass

        class Vfs:
            logger = Logger()

        class Node:
            v_path = STATS.encode()
            v_hash = 0
            file_type = 'adf'

        class Db:
            def read_node(self, vfs, vnode):
                return decode(out)

        with tempfile.TemporaryDirectory(prefix='cotw_xlsx_') as tmp:
            fn = adf_export_xlsx_0x0b73315d(Vfs(), Db(), Node(), tmp + '/', True)
            ws = openpyxl.load_workbook(fn)['ammo']
            last = [c.value for c in list(ws.iter_rows())[-1]]
            self.assertEqual(last, [NEW, '2-6', 0.25, 0.75, 'sp'])
            self.assertEqual(ws.max_row, 5)


class TestTools(unittest.TestCase):
    """Outils en ligne de commande sur un faux projet DECA (vrai scan)."""

    def test_ajouter_ligne_cloner_and_controle(self):
        import test_entite as TE
        import test_separateurs as TS
        stats = F.xlsbook(F.STATS_SHEETS, order='donnees_avant')
        with tempfile.TemporaryDirectory(prefix='cotw_adfw_') as root:
            project = faux_projet.build_fake_project(root, [
                (STATS, stats),
                ('settings/hp_settings/equipment_data.bin', TS.equipment_data()),
                ('settings/spawn_ammunition_defs.bin', TS.spawn_defs()),
                (TS.S.entity(SP).replace('\\', '/'), TE.entity(SP))])
            # 1. adf_ajouter_ligne
            out = Path(root) / 'mod'
            AL.main([project, STATS, '--ajouter', '{}={}'.format(SP, NEW), '--sortie', str(out)])
            produced = (out / 'dropzone' / Path(STATS)).read_bytes()
            self.assertEqual(rows(produced)[-1], [NEW, '2-6', 0.25, 0.75, 'sp'])
            with self.assertRaises(SystemExit):
                AL.main([project, STATS, '--ajouter', '{}={}'.format(SP, NEW), '--sortie', str(out)])
            with self.assertRaises(SystemExit):
                AL.main([project, STATS, '--ajouter', SP, '--sortie', str(out), '--ecraser'])
            # empilement : partir du fichier produit (--base), ajouter une seconde ligne
            AL.main([project, STATS, '--base', str(out / 'dropzone'), '--ajouter', 'equipment_ammo_243_pt_01=equipment_ammo_243_test_04',
                     '--sortie', str(out), '--ecraser'])
            r = rows((out / 'dropzone' / Path(STATS)).read_bytes())
            self.assertEqual([x[0] for x in r[-2:]], [NEW, 'equipment_ammo_243_test_04'])

            # 2. cloner_munition --stats-de
            out2 = Path(root) / 'mod2'
            C.main([project, '--source', SP, '--nom', NEW, '--prix', '77', '--entite-propre', '--stats-de', SP,
                    '--sortie', str(out2)])
            dz = out2 / 'dropzone'
            self.assertEqual(rows((dz / Path(STATS)).read_bytes())[-1][0], NEW)
            self.assertTrue((dz / 'settings' / 'hp_settings' / 'equipment_data.bin').is_file())
            report = (out2 / 'rapport_clonage.txt').read_text(encoding='utf-8')
            self.assertIn('equipment_stats_ui : ligne ajoutee', report)
            with self.assertRaises(SystemExit):        # modele sans ligne de stats : refus explicite
                C.main([project, '--source', SP, '--nom', 'equipment_ammo_243_test_05', '--stats-de',
                        'equipment_absent_01', '--sortie', str(Path(root) / 'mod3')])

            # 3. adf_ecriture_controle sur le projet
            md = Path(root) / 'controle.md'
            code = CTL.main([project, '--motif', 'settings/%', '--out', str(md)])
            text = md.read_text(encoding='utf-8')
            self.assertEqual(code, 0, text)
            self.assertIn('| ok | 1 |', text)
            self.assertIn('donnees < noms', text)
            self.assertIn('XLSBook : ligne', text)
            self.assertIn('| `0x0 0x0 0x0 0x0 0x0` | 1 |', text)
            self.assertIn('| verifiee | 1 |', text)
            self.assertIn('| tete 0x0 | 1 |', text)


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    unittest.main(verbosity=2)
