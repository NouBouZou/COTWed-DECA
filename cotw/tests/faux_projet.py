"""
Construction d'un faux projet DECA pour les tests des outils de cotw/.

Le faux jeu contient un exe (contenu choisi par le test) et une archive TAB v3/ARC avec les fichiers
donnes. Le vrai pipeline de scan DECA est lance dans un dossier temporaire, puis chaque fichier recoit son
chemin virtuel directement dans la base temporaire.

Pourquoi ne pas passer les chemins par un dictionnaire de noms : DECA lit les dictionnaires de
l'utilisateur dans <depot>\\..\\work, c'est-a-dire a cote du vrai projet. Un test ne doit rien y ecrire.

Ne pas utiliser vfs_structure_new(), qui ecrirait dans <depot>\\..\\work\\hp et ecraserait le projet.
"""
import os
import sqlite3
import struct
import sys
from typing import Iterable, Tuple

if not sys.platform.startswith('win'):
    import ctypes
    sys.modules.setdefault('zugbruecke.ctypes', ctypes)

DEFAULT_EXE = b'MZ' + b'\0' * 4094


def build_fake_game(root: str, files: Iterable[Tuple[str, bytes]], exe: bytes = DEFAULT_EXE) -> str:
    """
    Cree le faux jeu dans root et le fichier project.json d'un projet pas encore scanne ; renvoie son chemin.

    Ouvrir ce projet (vfs_structure_open) lance le scan complet : c'est ce que font les tests de l'interface pour
    verifier un scan en arriere-plan sans passer par vfs_structure_new.
    """
    from deca.hashes import hash32_func
    from deca.game_info import determine_game
    from deca.path import UniPath

    files = list(files)
    game_dir = os.path.join(root, 'theHunterCotW')
    arch_dir = os.path.join(game_dir, 'archives_win64', 'initial')
    os.makedirs(arch_dir)
    with open(os.path.join(game_dir, 'theHunterCotW_F.exe'), 'wb') as f:
        f.write(exe)

    arc = bytearray()
    entries = []
    for v_path, data in files:
        while len(arc) % 16:
            arc.append(0)
        entries.append((int(hash32_func(v_path.encode('utf-8'))), len(arc), len(data)))
        arc += data
    with open(os.path.join(arch_dir, 'game0.arc'), 'wb') as f:
        f.write(arc)
    with open(os.path.join(arch_dir, 'game0.tab'), 'wb') as f:
        f.write(b'TAB\x00' + struct.pack('<HHI', 2, 1, 4096) + b''.join(struct.pack('<III', *e) for e in entries))

    game_info = determine_game(UniPath.join(game_dir, ''), 'theHunterCotW_F.exe')
    if game_info is None:
        raise RuntimeError('le faux jeu n\'est pas reconnu comme COTW (exe_match de hp.json)')
    working_dir = UniPath.join(root, 'work', 'hp', '')
    os.makedirs(working_dir, exist_ok=True)
    game_info.save(UniPath.join(working_dir, 'project.json'))
    return UniPath.join(working_dir, 'project.json')


def build_fake_project(root: str, files: Iterable[Tuple[str, bytes]], exe: bytes = DEFAULT_EXE) -> str:
    """Cree le faux jeu dans root, le scanne et renvoie le dossier du projet (contient db/core.db)."""
    from deca.hashes import hash32_func
    from deca.db_processor import vfs_structure_prep
    from deca.path import UniPath

    files = list(files)
    project_file = build_fake_game(root, files, exe)
    working_dir = UniPath.join(UniPath.split(project_file)[0], '')
    vfs = vfs_structure_prep(project_file, working_dir)
    vfs.db_conn.close()

    db = sqlite3.connect(os.path.join(working_dir, 'db', 'core.db'))
    for v_path, _data in files:
        db.execute('UPDATE core_nodes SET v_path = ? WHERE v_hash = ? AND v_path IS NULL',
                   (v_path.encode('utf-8'), int(hash32_func(v_path.encode('utf-8')))))
    db.commit()
    db.close()
    return working_dir
