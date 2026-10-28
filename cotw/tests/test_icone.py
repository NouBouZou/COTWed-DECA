#!/usr/bin/env python3
"""
Tests de l'icone propre d'une munition : format AVTX et compression BC3 (icone.py), puis --icone / --icone-de de
cloner_munition et "icon" / "icon_from" des fichiers de mod (construire_mod.py), sur le projet demo.

Les textures sont relues par deca.ff_avtx / deca.dxgi, decodeur independant de l'encodeur teste. Aucune image du
jeu : icones et images dessinees par tests/texture_fabrique.py.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_icone.py
"""
import contextlib
import io
import json
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
# Au niveau du module : en mode spawn, les processus du scan reimportent ce fichier (substitut zugbruecke).
import _projet  # noqa: E402
import icone as I  # noqa: E402
import texture_fabrique as T  # noqa: E402

SP = 'equipment_ammo_243_sp_01'
S270 = 'equipment_ammo_270_sp_01'
NEW = 'equipment_ammo_243_icone_01'
NEW_ICON = 'textures/ui/items/ammo/{}_alpha.ddsc'.format(NEW)
# 0x00..0x2f de l'en-tete des 116 icones de munitions du jeu (releve du 2026-10-06), zeros ensuite jusqu'a 0x80
GAME_HEADER = bytes.fromhex('41565458 01000402 4d000000 80024001 01000000 09090000 00000000 00000000'
                            '80000000 202b0400 10000000 00000000') + bytes(0x50)


def png(path: Path, rgba: np.ndarray) -> Path:
    from PIL import Image
    Image.fromarray(rgba, 'RGBA').save(str(path))
    return path


def color_blocks(data: bytes):
    """(couleur0, couleur1, indices) de chaque bloc BC3 du corps."""
    f = I.forme(data)
    body = data[f.taille_entete:]
    for i in range(0, len(body), 16):
        c0, c1, idx = struct.unpack_from('<HHI', body, i + 8)
        yield c0, c1, idx


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------

class TestFormat(unittest.TestCase):
    def test_header_of_game_icons(self):
        self.assertEqual(I.entete(640, 320, 9), GAME_HEADER)
        f = I.forme(GAME_HEADER + bytes(273184))
        self.assertEqual((f.largeur, f.hauteur, f.format, f.niveaux, f.taille_entete, f.taille_corps),
                         (640, 320, 77, 9, 128, 273184))

    def test_mip_sizes(self):
        sizes = I.tailles_niveaux(640, 320, 9)
        self.assertEqual(sizes[0], (640, 320))
        self.assertEqual(sizes[-1], (2, 1))
        self.assertEqual(sum(I.taille_bc3(*s) for s in sizes), 273184)
        self.assertEqual(I.taille_bc3(1, 1), 16)

    def test_refusals(self):
        good = T.icone_ddsc()
        I.forme(good)
        bad_mips = bytearray(good)
        bad_mips[21] = 5                       # 7 niveaux dont 5 dans le fichier : le reste dans un .hmddsc
        flags = bytearray(good)
        flags[18] = 1
        for data, message in ((b'DDS ' + good[4:], 'pas une texture AVTX'),
                              (good + b'\0', 'taille'),
                              (bytes(bad_mips), '.hmddsc'),
                              (bytes(flags), 'non prise en charge')):
            with self.subTest(message=message):
                with self.assertRaises(I.EIcone) as ctx:
                    I.forme(data)
                self.assertIn(message, str(ctx.exception))


# ---------------------------------------------------------------------------
# Compression BC3
# ---------------------------------------------------------------------------

class TestEncoder(unittest.TestCase):
    def decode(self, rgba: np.ndarray) -> np.ndarray:
        return I.decoder(I.ddsc_bc3(rgba, 1))[0]

    def test_solid_565_colors_are_exact(self):
        rgba = np.zeros((8, 8, 4), dtype=np.uint8)
        rgba[..., 3] = 255
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]   # exactes en 565
        for k, c in enumerate(colors):
            rgba[(k // 2) * 4:(k // 2) * 4 + 4, (k % 2) * 4:(k % 2) * 4 + 4, :3] = c
        self.assertTrue(np.array_equal(self.decode(rgba), rgba))

    def test_alpha_extremes_exact_and_gradient_close(self):
        rgba = np.zeros((4, 16, 4), dtype=np.uint8)
        rgba[..., :3] = 128
        rgba[:, :4, 3] = 0
        rgba[:, 4:8, 3] = 255
        rgba[:, 8:, 3] = np.linspace(0, 255, 8).astype(np.uint8)[None, :]
        got = self.decode(rgba)
        self.assertTrue(np.array_equal(got[:, :8, 3], rgba[:, :8, 3]))
        self.assertLessEqual(int(np.abs(got[:, 8:, 3].astype(int) - rgba[:, 8:, 3]).max()), 255 // 14 + 1)

    def test_color0_always_greater(self):
        """deca.dxgi lit le bloc couleur du BC3 comme du BC1 : couleur0 <= couleur1 y passerait en mode 3 couleurs."""
        rng = np.random.default_rng(7)
        rgba = rng.integers(0, 256, (64, 64, 4), dtype=np.uint8)
        rgba[:16, :16] = (10, 20, 30, 255)                  # bloc uni : couleur0 == couleur1, indices nuls
        data = I.ddsc_bc3(rgba, 7)
        for c0, c1, idx in color_blocks(data):
            self.assertTrue(c0 > c1 or (c0 == c1 and idx == 0), (c0, c1, idx))

    def test_deca_reads_like_the_reference(self):
        """deca.dxgi (dxgi_bc, depuis le 2026-10-28) : meme image que le decodage de reference, tous niveaux ; le blanc
        est lu 255 (l'ancien decodeur, qui etendait le 565 par decalage, le lisait 248)."""
        rgba = np.zeros((4, 4, 4), dtype=np.uint8)
        rgba[...] = (255, 255, 255, 255)
        self.assertEqual(I.lire(I.ddsc_bc3(rgba, 1))[0][0, 0].tolist(), [255, 255, 255, 255])
        rgba = T.cartouches(128, 64)
        data = I.ddsc_bc3(rgba, 7)
        for ref, deca in zip(I.decoder(data), I.lire(data)):
            diff = np.abs(ref.astype(int) - deca.astype(int))
            self.assertLessEqual(diff[..., :3].max(), I.ECART_DECA_COULEUR)
            self.assertLessEqual(diff[..., 3].max(), I.ECART_DECA_ALPHA)

    def test_drawing_quality(self):
        """Dessin a bords francs a la taille des icones du jeu : au-dessus du seuil du controle, avec de la marge
        (mesure : 37,8 dB en couleur, 47,7 dB en alpha)."""
        rgba = T.cartouches(640, 320)
        got = self.decode(rgba)
        visible = rgba[..., 3] >= 128
        self.assertGreater(I.psnr(got[..., :3], rgba[..., :3], visible), I.PSNR_MIN + 3)
        self.assertGreater(I.psnr(got[..., 3], rgba[..., 3]), I.PSNR_MIN + 10)


# ---------------------------------------------------------------------------
# Fabrication
# ---------------------------------------------------------------------------

class TestFabriquer(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='cotwed_icone_'))
        self.modele = T.icone_ddsc(pointe=T.VERT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_png_of_other_size(self):
        image = png(self.tmp / 'a.png', T.cartouches(128, 64))
        out, lines = I.fabriquer(str(image), self.modele)
        self.assertEqual(out[:128], self.modele[:128])
        self.assertEqual(len(out), len(self.modele))
        self.assertIn('mise a 64x32', '\n'.join(lines))
        self.assertIn('lecture DECA concordante', lines[-1])
        levels = I.lire(out)
        self.assertEqual([lv.shape[:2] for lv in levels], [(32, 64), (16, 32), (8, 16), (4, 8), (2, 4), (1, 2), (1, 1)])

    def test_aspect_kept_centered_on_transparent(self):
        square = np.zeros((32, 32, 4), dtype=np.uint8)
        square[...] = (255, 0, 0, 255)
        out, lines = I.fabriquer(str(png(self.tmp / 'carre.png', square)), self.modele)
        top = I.decoder(out)[0]
        self.assertTrue((top[:, :16, 3] == 0).all() and (top[:, 48:, 3] == 0).all())
        self.assertTrue((top[:, 16:48, 3] == 255).all())
        self.assertTrue((top[:, 16:48, :3] == (255, 0, 0)).all())
        self.assertIn('centree dans 64x32', '\n'.join(lines))

    def test_image_without_alpha_noted(self):
        from PIL import Image
        Image.new('RGB', (64, 32), (0, 0, 255)).save(str(self.tmp / 'rgb.png'))
        _, lines = I.fabriquer(str(self.tmp / 'rgb.png'), self.modele)
        self.assertIn('sans transparence', '\n'.join(lines))

    def test_control_detects_bad_body(self):
        rgba, _ = I.preparer(png(self.tmp / 'a.png', T.cartouches(64, 32)), 64, 32)
        levels = I.niveaux(rgba, I.tailles_niveaux(64, 32, 7))
        good = self.modele[:128] + b''.join(I.encoder_bc3(lv) for lv in levels)
        I.controler(good, self.modele, levels)
        bad = bytearray(good)
        for i in range(128, 128 + 512, 16):                 # blocs couleur du niveau 0 mis a zero
            bad[i + 8:i + 16] = bytes(8)
        with self.assertRaises(I.EIcone) as ctx:
            I.controler(bytes(bad), self.modele, levels)
        self.assertIn('niveau 0', str(ctx.exception))

    def test_model_not_bc3_refused(self):
        other = bytearray(self.modele)
        other[8:12] = struct.pack('<I', 71)
        with self.assertRaises(I.EIcone) as ctx:
            I.fabriquer(str(png(self.tmp / 'a.png', T.cartouches(64, 32))), bytes(other))
        self.assertIn('BC3_UNORM', str(ctx.exception))

    def test_cli(self):
        (self.tmp / 'modele.ddsc').write_bytes(self.modele)
        image = png(self.tmp / 'a.png', T.cartouches(64, 32))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            I.main(['fabriquer', str(image), '--modele', str(self.tmp / 'modele.ddsc'), '--sortie', str(self.tmp / 'b.ddsc')])
            I.main(['lire', str(self.tmp / 'b.ddsc'), '--png', str(self.tmp / 'b.png')])
        self.assertIn('64x32, format DXGI 77, 7 niveaux', out.getvalue())
        self.assertTrue((self.tmp / 'b.png').is_file())


# ---------------------------------------------------------------------------
# cloner_munition et construire_mod
# ---------------------------------------------------------------------------

class TestIconPath(unittest.TestCase):
    def test_new_icon_path(self):
        import cloner_munition as CM
        self.assertEqual(CM.new_icon_path('textures\\ui\\items\\ammo\\{}_alpha.ddsc'.format(SP), NEW),
                         'textures\\ui\\items\\ammo\\{}_alpha.ddsc'.format(NEW))
        self.assertEqual(CM.new_icon_path('textures/ui/items/ammo/autre.ddsc', NEW),
                         'textures/ui/items/ammo/{}.ddsc'.format(NEW))
        with self.assertRaises(CM.EClone):
            CM.new_icon_path('textures/sans_extension', NEW)

    def test_path_of_a_game_file_refused(self):
        import cloner_munition as CM
        import deca.ff_rtpc_write as W
        from deca.ff_rtpc import k_type_str

        class Node:
            offset = 0

        class Vfs:
            def nodes_where_match(self, **kw):
                return [Node()] if kw.get('v_path') == NEW_ICON else []

        item = W.make_node(NEW, [W.make_prop('display_icon', k_type_str,
                                             'textures\\ui\\items\\ammo\\{}_alpha.ddsc'.format(SP).encode())])
        with self.assertRaises(CM.EClone) as ctx:
            CM.own_icon(Vfs(), None, item, CM.Munition(SP, NEW, icone_de=S270), None)
        self.assertIn('serait remplace', str(ctx.exception))


class Demo:
    root = None
    project = None

    @classmethod
    def get(cls) -> str:
        if cls.project is None:
            import demo_projet
            cls.root = Path(tempfile.mkdtemp(prefix='cotwed_icone_demo_'))
            with contextlib.redirect_stdout(io.StringIO()):
                cls.project = str(demo_projet.build(cls.root / 'demo').parent)
        return cls.project


def tearDownModule():
    if Demo.root is not None:
        shutil.rmtree(Demo.root, ignore_errors=True)


class TestClone(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import cloner_munition as CM
        import construire_mod as CMOD
        cls.CM, cls.CMOD = CM, CMOD
        cls.project = Demo.get()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='cotwed_icone_'))
        self.vfs = _projet.open_project(self.project)
        self.addCleanup(self.vfs.db_conn.close)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, **kw):
        CM = self.CM
        return dict(CM.construire(self.vfs, CM.Mod([CM.Munition(SP, NEW, entite=CM.ENTITE_PROPRE, **kw)])).fichiers)

    def display_icon(self, files) -> str:
        import deca.ff_rtpc_write as W
        tree = W.rtpc_from_bytes(files[self.CM.EQUIPMENT_DATA])
        _, item = self.CM.find_item(tree.root_node, NEW, self.CM.H_NAME)
        return self.CM.icon_path(item, NEW)

    def test_icon_from_copies_the_texture(self):
        files = self.build(icone_de=S270)
        self.assertEqual(files[NEW_ICON], _projet.read_by_path(self.vfs, 'textures/ui/items/ammo/{}_alpha.ddsc'.format(S270)))
        self.assertEqual(self.display_icon(files), NEW_ICON.replace('/', '\\'))
        self.assertEqual(list(files).index(NEW_ICON), 3)     # apres equipment_data, spawn, entite

    def test_icon_from_image(self):
        image = png(self.tmp / 'icone.png', T.cartouches(640, 320, pointe=(255, 0, 255)))
        files = self.build(icone=str(image))
        source = _projet.read_by_path(self.vfs, 'textures/ui/items/ammo/{}_alpha.ddsc'.format(SP))
        self.assertEqual(files[NEW_ICON][:128], source[:128])
        self.assertEqual(len(files[NEW_ICON]), len(source))
        top = I.decoder(files[NEW_ICON])[0]
        tip = top[(top[..., 0] > 200) & (top[..., 2] > 200) & (top[..., 1] < 60)]
        self.assertGreater(len(tip), 20)                     # pointes magenta : l'image fournie, pas la source
        self.assertEqual(self.display_icon(files), NEW_ICON.replace('/', '\\'))

    def test_without_icon_option_nothing_changes(self):
        files = self.build()
        self.assertNotIn(NEW_ICON, files)
        self.assertEqual(self.display_icon(files), 'textures\\ui\\items\\ammo\\{}_alpha.ddsc'.format(SP))

    def test_refusals(self):
        CM = self.CM
        image = png(self.tmp / 'icone.png', T.cartouches(64, 32))
        for kw, message in (({'icone': str(image), 'icone_de': S270}, 'incompatibles'),
                            ({'icone': str(self.tmp / 'absente.png')}, 'introuvable'),
                            ({'icone_de': 'equipment_ammo_absente_01'}, 'introuvable')):
            with self.subTest(message=message):
                with self.assertRaises(CM.EClone) as ctx:
                    self.build(**kw)
                self.assertIn(message, str(ctx.exception))
        (self.tmp / 'pas_une_image.png').write_bytes(b'texte')
        with self.assertRaises(CM.EClone) as ctx:
            self.build(icone=str(self.tmp / 'pas_une_image.png'))
        self.assertIn('illisible', str(ctx.exception))

    def test_cli_equals_mod_with_relative_path(self):
        CM, CMOD = self.CM, self.CMOD
        (self.tmp / 'mod' / 'icones').mkdir(parents=True)
        png(self.tmp / 'mod' / 'icones' / 'a.png', T.cartouches(320, 160))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            CM.main([self.project, '--source', SP, '--nom', NEW, '--entite-propre',
                     '--icone', str(self.tmp / 'mod' / 'icones' / 'a.png'), '--sortie', str(self.tmp / 'cli')])
            CM.main([self.project, '--source', SP, '--nom', 'equipment_ammo_243_icone_02', '--entite-propre',
                     '--icone-de', S270, '--base', str(self.tmp / 'cli' / 'dropzone'), '--sortie', str(self.tmp / 'cli2')])
        self.assertIn('icone fabriquee depuis', out.getvalue())
        mod = self.tmp / 'mod' / 'm.cotwmod.json'
        mod.write_text(json.dumps({'format': CMOD.FORMAT, 'version': CMOD.VERSION, 'name': 'icones', 'ammo': [
            {'source': SP, 'name': NEW, 'entity': 'own', 'icon': 'icones/a.png'},
            {'source': SP, 'name': 'equipment_ammo_243_icone_02', 'entity': 'own', 'icon_from': S270}]}),
            encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            CMOD.main([self.project, str(mod), '--sortie', str(self.tmp / 'built')])

        def files(folder):
            root = self.tmp / folder / 'dropzone'
            return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}

        cli = files('cli')
        cli.update(files('cli2'))
        built = files('built')
        self.assertEqual(sorted(built), sorted(cli))
        self.assertEqual(built, cli)
        # le fichier de mod garde son chemin relatif ; mod jamais enregistre : refus explicite
        mf = CMOD.load(mod)
        self.assertEqual(CMOD.to_dict(mf)['ammo'][0]['icon'], 'icones/a.png')
        self.assertEqual(CMOD.to_dict(mf)['ammo'][1]['icon_from'], S270)
        with self.assertRaises(CMOD.EMod) as ctx:
            CMOD.construire(self.vfs, CMOD.from_dict(CMOD.to_dict(mf)))
        self.assertIn('jamais enregistre', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
