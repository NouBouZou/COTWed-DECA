#!/usr/bin/env python3
"""
manifestes.py -- exploite les manifestes d'archives du depot Steam de debogage de theHunter: COTW (depot 518792).

Le depot de debogage (DepotDownloader, application 518790, depot 518792) livre, a cote des exe et des PDB, un dossier
archives_win64/ sans archive mais avec :
  - game_hash_names<N>.txt : le manifeste de game<N>.arc, une ligne par entree du .tab :
        HASH       BYTEOFFSET SIZE       DCOMP SIZE CONTENT DIGEST (SHA1)                     NAME
        0xC5ED121F 0x00000000 0x000032ED 0x000032ED b74ce54cbbaefd72d1f81ce535749a010aceab96  ai/aisystem.aisystunec
  - game_filelist.txt : les memes fichiers, en chemins de compilation (d:\\jenkins\\...\\intermediate\\...) ;
  - archives_digest.txt : le hash du contenu, la taille et le hash de nom de chaque .arc, .tab et manifeste.
Build 3366163 (versions.518792.txt) : 144 archives, 89 205 entrees ; hash32(NAME) = HASH pour toutes ; aucune entree
compressee (SIZE = DCOMP SIZE) ; offsets alignes sur 4 096 ; une collision de hash32 (0xf18d95ea : une .hmddsc de
game57, un .streampatch de game95).

Pourquoi : ce sont les noms officiels de toutes les entrees directes des .tab, la ou restaient les fichiers sans nom de
DECA (N1). Les SHA-1 permettent aussi de verifier qu'une installation correspond exactement a ce build (fichiers a jour,
non modifies) : un modloader doit savoir contre quelles archives il travaille.

Sous-commandes :
  noms DOSSIER [--out FICHIER]      noms de toutes les entrees (tries), apres controle hash32(nom) = hash
  stats DOSSIER                     entrees, archives, extensions, collisions
  verifier DOSSIER ARCHIVES         relit chaque entree dans ARCHIVES\\game<N>.arc (offset, taille) et compare son SHA-1

DOSSIER : le dossier archives_win64 du depot de debogage ; ARCHIVES : archives_win64 de l'installation du jeu.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\manifestes.py noms "D:\\dev\\debug files\\latest_cotw_debugfiles\\518790\\archives_win64" ^
        --out resources\\deca\\hp\\manifestes.txt
    python cotw\\manifestes.py verifier "<depot>\\archives_win64" "<jeu>\\archives_win64"
"""
import argparse
import collections
import hashlib
import re
import sys
from pathlib import Path
from typing import Dict, Iterator, List, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401  (chemin de deca)
from deca.hashes import hash32_func  # noqa: E402

HERE = Path(__file__).resolve().parent
NAMES_OUT = HERE.parent / 'resources' / 'deca' / 'hp' / 'manifestes.txt'
_RX_LINE = re.compile(r'0x([0-9A-Fa-f]{8}) 0x([0-9A-Fa-f]{8}) 0x([0-9A-Fa-f]{8}) 0x([0-9A-Fa-f]{8}) ([0-9a-f]{40})  (\S.*?)\s*$')
_RX_FILE = re.compile(r'game_hash_names(\d+)\.txt$')


class Entry(NamedTuple):
    archive: int        # N de game<N>.arc
    hash: int           # hash32 du nom
    offset: int         # position dans le .arc
    size: int           # taille stockee
    dcomp_size: int     # taille decompressee (egale a size dans le build 3366163)
    sha1: str           # SHA-1 du contenu
    name: str           # chemin virtuel


def read_manifest(path: Path, archive: int) -> Iterator[Entry]:
    with Path(path).open(encoding='utf-8', errors='replace') as f:
        for line in f:
            m = _RX_LINE.match(line)
            if m:
                yield Entry(archive, int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16),
                            int(m.group(4), 16), m.group(5), m.group(6))


def read_folder(folder: Path) -> List[Entry]:
    """Toutes les entrees des game_hash_names<N>.txt du dossier, par numero d'archive croissant."""
    files = []
    for p in Path(folder).iterdir():
        m = _RX_FILE.search(p.name)
        if m:
            files.append((int(m.group(1)), p))
    if not files:
        raise SystemExit('ERREUR : aucun game_hash_names<N>.txt dans {}'.format(folder))
    entries: List[Entry] = []
    for archive, p in sorted(files):
        entries.extend(read_manifest(p, archive))
    return entries


def check_hashes(entries: List[Entry]) -> List[Entry]:
    """Entrees dont le hash ne correspond pas au nom (aucune attendue)."""
    return [e for e in entries if hash32_func(e.name) != e.hash]


def cmd_noms(args) -> int:
    entries = read_folder(Path(args.dossier))
    bad = check_hashes(entries)
    for e in bad[:20]:
        print('hash discordant : game{} 0x{:08x} {}'.format(e.archive, e.hash, e.name))
    bad_set = set(bad)
    names = sorted({e.name for e in entries if e not in bad_set})
    out = Path(args.out)
    out.write_text('\n'.join(names) + '\n', encoding='utf-8', newline='\n')
    print('{} entrees, {} noms -> {} ({} discordants exclus)'.format(len(entries), len(names), out, len(bad)))
    return 1 if bad else 0


def cmd_stats(args) -> int:
    entries = read_folder(Path(args.dossier))
    by_hash: Dict[int, List[Entry]] = collections.defaultdict(list)
    for e in entries:
        by_hash[e.hash].append(e)
    collisions = [v for v in by_hash.values() if len({e.name for e in v}) > 1]
    print('entrees : {} ; archives : {} ; noms distincts : {} ; hashes distincts : {}'.format(
        len(entries), len({e.archive for e in entries}), len({e.name for e in entries}), len(by_hash)))
    print('hash32(nom) = hash : {} / {}'.format(len(entries) - len(check_hashes(entries)), len(entries)))
    print('compressees (size != dcomp_size) : {}'.format(sum(1 for e in entries if e.size != e.dcomp_size)))
    for v in collisions:
        print('collision 0x{:08x} : {}'.format(v[0].hash, ' | '.join('game{} {}'.format(e.archive, e.name) for e in v)))
    ext = collections.Counter(e.name.rsplit('.', 1)[-1] for e in entries)
    print('extensions : ' + ', '.join('{} {}'.format(k, n) for k, n in ext.most_common(15)))
    return 0


def cmd_verifier(args) -> int:
    """Compare le SHA-1 de chaque entree relue dans les .arc de l'installation a celui du manifeste."""
    entries = read_folder(Path(args.dossier))
    arcs = Path(args.archives)
    counts = collections.Counter()
    shown = 0
    by_archive: Dict[int, List[Entry]] = collections.defaultdict(list)
    for e in entries:
        by_archive[e.archive].append(e)
    for archive in sorted(by_archive):
        path = arcs / 'game{}.arc'.format(archive)
        if not path.is_file():
            counts['archive absente'] += len(by_archive[archive])
            print('absente : {}'.format(path))
            continue
        with path.open('rb') as f:
            for e in sorted(by_archive[archive], key=lambda x: x.offset):
                if e.size != e.dcomp_size:
                    counts['compressee, non verifiee'] += 1
                    continue
                f.seek(e.offset)
                data = f.read(e.size)
                if len(data) != e.size:
                    state = 'tronquee'
                elif hashlib.sha1(data).hexdigest() == e.sha1:
                    state = 'identique'
                else:
                    state = 'differente'
                counts[state] += 1
                if state != 'identique' and shown < args.max:
                    shown += 1
                    print('{} : game{} {}'.format(state, archive, e.name))
    print(', '.join('{} {}'.format(k, n) for k, n in sorted(counts.items())))
    return 0 if set(counts) <= {'identique'} else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('noms')
    s.add_argument('dossier')
    s.add_argument('--out', default=str(NAMES_OUT))
    s = sub.add_parser('stats')
    s.add_argument('dossier')
    s = sub.add_parser('verifier')
    s.add_argument('dossier')
    s.add_argument('archives')
    s.add_argument('--max', type=int, default=30, help='nombre maximal d\'ecarts affiches')
    args = p.parse_args(argv)
    return {'noms': cmd_noms, 'stats': cmd_stats, 'verifier': cmd_verifier}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
