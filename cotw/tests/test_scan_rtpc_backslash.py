#!/usr/bin/env python3
"""
Test de non-regression : un RTPC dont la propriete "name" contient un antislash ne doit plus
interrompre le scan DECA.

Contexte (theHunter: COTW, 2026-09-29) : le scan s'arretait pendant process_rtpc_initial avec
    IndexError: list index out of range   (db_core.py, object_info_add_many)
Cause : DbWrap.propose_string() remplace '\\' par '/' avant de stocker une chaine, alors que
object_info_add_many() recherchait la valeur brute dans core_strings.

Le test fabrique, dans un dossier temporaire, un faux jeu COTW (exe vide + une archive TAB v3/ARC
contenant un RTPC v1 minimal), lance le vrai pipeline de scan DECA dans un dossier de travail
temporaire, puis verifie :
  1. que le scan est termine (PRAGMA user_version == 2) ;
  2. que l'objet est enregistre avec son nom exact, antislash et accent compris.

Il n'utilise PAS vfs_structure_new(), qui ecrirait dans <depot>\\..\\work\\hp et ecraserait ton projet.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\tests\\test_scan_rtpc_backslash.py
Code de sortie 0 = succes, 1 = echec.
"""
import os
import sqlite3
import struct
import sys
import tempfile

if not sys.platform.startswith('win'):
    # Sous Linux, deca.decompress importe zugbruecke (pont vers les DLL Windows, qui exige Wine).
    # COTW n'utilise pas Oodle : on substitue ctypes pour pouvoir lancer le test sans Wine.
    import ctypes
    import types
    sys.modules.setdefault('zugbruecke', types.ModuleType('zugbruecke')).ctypes = ctypes  # paquet parent
    sys.modules['zugbruecke.ctypes'] = ctypes

K_TYPE_STR = 3  # deca.ff_rtpc.PropType.type_str
NAME_VALUE = 'hp_weapons\\test_équipement_01'  # antislash + caractere non ASCII
CLASS_VALUE = 'CRigidObject'


def build_rtpc(str_props, hash32_func):
    """
    RTPC v1 minimal, dans la disposition que lit deca.ff_rtpc :
      0x00  'RTPC', u32 version (1)
      0x08  noeud racine : u32 name_hash, u32 data_offset, u16 prop_count, u16 child_count
      0x14  proprietes : u32 name_hash, u32 donnee (offset de la chaine), u8 type   (9 octets)
      ...   chaines terminees par un octet nul, alignees sur 4
    """
    n = len(str_props)
    props_off = 8 + 12
    strings_off = props_off + 9 * n
    strings_off += (4 - strings_off % 4) % 4
    blob_props, blob_strings = b'', b''
    for name, value in str_props:
        blob_props += struct.pack('<IIB', hash32_func(name), strings_off + len(blob_strings), K_TYPE_STR)
        blob_strings += value + b'\0'
    body = b'RTPC' + struct.pack('<I', 1) + struct.pack('<IIHH', 0, props_off, n, 0) + blob_props
    return body + b'\0' * (strings_off - len(body)) + blob_strings


def make_fake_game(root, hash32_func):
    game_dir = os.path.join(root, 'theHunterCotW')
    arch_dir = os.path.join(game_dir, 'archives_win64', 'initial')
    os.makedirs(arch_dir)
    exe_path = os.path.join(game_dir, 'theHunterCotW_F.exe')
    with open(exe_path, 'wb') as f:
        f.write(b'MZ' + b'\0' * 4094)
    data = build_rtpc([(b'_class', CLASS_VALUE.encode('utf-8')),
                       (b'name', NAME_VALUE.encode('utf-8'))], hash32_func)
    with open(os.path.join(arch_dir, 'game0.arc'), 'wb') as f:
        f.write(data)
    # TAB v3 : en-tete 'TAB\0', u16 2, u16 1, u32 4096, puis entrees (u32 hash, u32 offset, u32 taille)
    with open(os.path.join(arch_dir, 'game0.tab'), 'wb') as f:
        f.write(b'TAB\x00' + struct.pack('<HHI', 2, 1, 4096) + struct.pack('<III', 0xA1B2C3D4, 0, len(data)))
    return game_dir, exe_path


def main():
    from deca.hashes import hash32_func
    from deca.game_info import determine_game
    from deca.db_processor import vfs_structure_prep
    from deca.path import UniPath

    with tempfile.TemporaryDirectory(prefix='deca_test_') as root:
        game_dir, exe_path = make_fake_game(root, hash32_func)
        game_info = determine_game(UniPath.join(game_dir, ''), 'theHunterCotW_F.exe')
        if game_info is None:
            print('ECHEC : le faux jeu n\'est pas reconnu comme COTW (exe_match de hp.json)')
            return 1

        working_dir = UniPath.join(root, 'work', 'hp', '')
        project_file = UniPath.join(working_dir, 'project.json')
        os.makedirs(working_dir, exist_ok=True)
        game_info.save(project_file)

        try:
            vfs = vfs_structure_prep(project_file, working_dir)
            vfs.db_conn.close()
        except Exception as e:
            print('ECHEC : le scan s\'est interrompu : {!r}'.format(e))
            return 1

        db = sqlite3.connect(os.path.join(working_dir, 'db', 'core.db'))
        version = db.execute('PRAGMA user_version').fetchone()[0]
        rows = db.execute(
            'SELECT cs.string, ns.string FROM core_objects o '
            'LEFT JOIN core_strings cs ON cs.rowid = o.class_str_rowid '
            'LEFT JOIN core_strings ns ON ns.rowid = o.name_str_rowid').fetchall()
        db.close()

    ok = True
    if version != 2:
        print('ECHEC : scan incomplet (user_version = {})'.format(version))
        ok = False
    if rows != [(CLASS_VALUE, NAME_VALUE)]:
        print('ECHEC : objets enregistres = {!r}, attendu [({!r}, {!r})]'.format(rows, CLASS_VALUE, NAME_VALUE))
        ok = False
    if ok:
        print('OK : scan termine, objet enregistre avec son nom exact : {!r}'.format(NAME_VALUE))
    return 0 if ok else 1


if __name__ == '__main__':
    # Indispensable sous Windows : DECA lance des processus de travail (multiprocessing, mode spawn).
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
