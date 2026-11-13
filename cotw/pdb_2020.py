#!/usr/bin/env python3
"""
pdb_2020.py -- exploite les symboles de debogage (PDB) d'un build de theHunter: COTW du 2020-10-23.

Source : archive "[WIN] theHunter - Call of the Wild (Classic) [2020-10-23] (PDB)" (Discord de modding APEX) :
theHunterCotW_R / _P / _F (.exe et .pdb), versions.518792.txt (code 1898534, archives 1898692). Les PDB sont
complets : types (TPI), symboles globaux et publics. Ils decrivent le code de 2020, pas celui du jeu actuel : les
noms de champs et de fonctions servent de reperes, les offsets et les adresses sont a reverifier sur l'exe courant.

Le PDB n'est pas lu directement : on part des sorties texte de llvm-pdbutil (paquet LLVM ; Linux, WSL) :
    llvm-pdbutil dump -globals theHunterCotW_P.pdb > globals.txt
    llvm-pdbutil dump -types   theHunterCotW_P.pdb > types.txt
    llvm-pdbutil dump -publics theHunterCotW_P.pdb > publics.txt
(-types : environ 440 Mo de texte pour le PDB "P", 6 s ; les autres : moins d'une seconde.)

Pourquoi les constantes : le code d'Avalanche declare ses hash32 de noms en constantes compilees (S_CONSTANT), nommees
d'apres la chaine hachee : CAnimalPopulationManager_GreatOnesSpawnProbability = hash32("great_ones_spawn_probability").
Ces chaines n'existent nulle part ailleurs (ni dans l'exe, ni dans les donnees) : c'est pourquoi la capture a
l'execution du 28/10 ne les voyait pas. Le nom de la constante propose la chaine, la valeur la confirme : chaque paire
est prouvee. Seul risque : qu'une variante essayee tombe par hasard sur la valeur de SA constante, soit environ
(variantes essayees) / 2^32 par constante ; la sous-commande dictionnaire affiche ce total.

Sous-commandes :
  dictionnaire GLOBALS [--ecrire]  constantes -> noms nouveaux de hp_noms.tsv (passe pdb-constante) et hashes sans
                                   chaine retrouvee -> pdb_2020_sans_chaine.tsv (nom de la constante comme etiquette)
  index TYPES INDEX                index des types (pickle) pour vocabulaire et structure
  vocabulaire INDEX GLOBALS        identifiants du jeu (membres, enumerations, types, constantes) ->
                                   pdb_2020_identifiants.txt, pour resoudre_hashes.py --sources ...,fichier:<ce fichier>
  structure INDEX NOM...           disposition d'une structure (offsets, types des membres) ou valeurs d'une enumeration
  desassembler EXE PUBLICS MOTIF   desassemblage symbolise (llvm-objdump) des fonctions dont le nom contient MOTIF (regex)
  appelants EXE PUBLICS MOTIF      appels directs (call / jmp rel32) vers ces fonctions, nommes
  verifier                         recalcule le hash de chaque nom pdb-* de hp_noms.tsv

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\pdb_2020.py dictionnaire globals.txt --ecrire
    python cotw\\pdb_2020.py structure types.pkl CAnimalPopulationManager::SAnimalSettings
    python cotw\\pdb_2020.py appelants theHunterCotW_P.exe publics.txt CAnimalPopulation::ShouldSpawnGreatOne
"""
import argparse
import bisect
import datetime
import pickle
import re
import struct
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401  (chemin de deca)
from deca.hashes import hash32_func  # noqa: E402

HERE = Path(__file__).resolve().parent
DICTIONARY = HERE / 'dictionnaires' / 'hp_noms.tsv'
NO_STRING = HERE / 'dictionnaires' / 'pdb_2020_sans_chaine.tsv'
IDENTIFIERS = HERE / 'dictionnaires' / 'pdb_2020_identifiants.txt'
PASS_NAME = 'pdb-constante'
SOURCE_TAG = 'PDB 2020'

# ---------------------------------------------------------------------------------------------------------
# Constantes (S_CONSTANT) -> paires hash / nom
# ---------------------------------------------------------------------------------------------------------
_RX_CONSTANT = re.compile(r"S_CONSTANT \[size = \d+\] `([^`]+)`\n\s+type = 0x[0-9A-F]+ \(([^)]*)\), value = (-?\d+)")


def read_constants(globals_txt: str) -> List[Tuple[str, int]]:
    """(nom, valeur sur 32 bits) des constantes non signees de 32 bits (les seules qui peuvent etre des hash32)."""
    out = []
    for name, type_name, value in _RX_CONSTANT.findall(globals_txt):
        if 'unsigned' in type_name and '__int64' not in type_name:
            out.append((name, int(value) & 0xFFFFFFFF))
    return out


def snake(text: str) -> str:
    """CamelCase -> snake_case : GreatOnesSpawnProbability -> great_ones_spawn_probability, AIState -> ai_state."""
    text = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', text)
    text = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', text)
    return text.lower()


def string_variants(text: str) -> Iterator[str]:
    """Formes sous lesquelles le code hache un identifiant ; l'ordre fixe la forme retenue quand deux concordent."""
    yield text
    yield text.lower()
    yield snake(text)
    yield re.sub(r'([a-z])([0-9])', r'\1_\2', snake(text))
    yield text.upper()
    if text[:1].isupper():
        yield text[0].lower() + text[1:]
    yield 'm_' + text
    yield 'm' + text


def constant_candidates(name: str) -> Iterator[str]:
    """Morceaux du nom d'une constante, du plus probable au moins probable : pour CAnimalType_IsGreatOneSpecific,
    IsGreatOneSpecific d'abord (suffixes de la derniere partie), puis les prefixes, puis chaque partie qualifiee."""
    segments = [s for s in name.split('::') if s]
    seen: Set[str] = set()
    for seg in [segments[-1]] + segments[:-1][::-1]:
        parts = seg.split('_')
        spans = [(i, len(parts)) for i in range(len(parts))] + [(0, j) for j in range(1, len(parts))]
        spans += [(i, j) for i in range(1, len(parts)) for j in range(i + 1, len(parts))]
        for i, j in spans:
            piece = '_'.join(parts[i:j])
            if piece and piece not in seen:
                seen.add(piece)
                yield piece
    if name not in seen:
        yield name


def resolve_constant(name: str, value: int) -> Tuple[Optional[str], int]:
    """(chaine dont le hash32 vaut la valeur, ou None ; nombre de candidats essayes)."""
    tried = 0
    for piece in constant_candidates(name):
        for text in string_variants(piece):
            if not text:
                continue
            tried += 1
            if hash32_func(text) == value:
                return text, tried
    return None, tried


def load_dictionary_hashes(path: Path = DICTIONARY) -> Set[int]:
    hashes = set()
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            cols = line.split('\t')
            if len(cols) >= 3 and not line.startswith('#') and cols[1] == '32':
                hashes.add(int(cols[0], 16))
    return hashes


def cmd_dictionnaire(args):
    constants = read_constants(Path(args.globals).read_text(encoding='utf-8', errors='replace'))
    known = load_dictionary_hashes()
    resolved: Dict[int, Tuple[str, str]] = {}
    unresolved: Dict[int, List[str]] = {}
    tried_total = 0
    for name, value in constants:
        if value < 0x10000:      # petits entiers (tailles, masques, identifiants) : pas des hashes
            continue
        text, tried = resolve_constant(name, value)
        tried_total += tried
        if text is not None:
            resolved.setdefault(value, (text, name))
        elif value >= 0x100000 and not _THIRD_PARTY.match(name) \
                and not re.search(r'(?:^|::)(?:boost|std|Scaleform|hk\w*)::', name):
            # sans chaine : seulement ce qui ressemble a un hash du jeu (une taille ou un masque tient sous 2^20,
            # un hash n'y tombe qu'une fois sur 4 096) et hors bibliotheques tierces
            unresolved.setdefault(value, []).append(name)
    for value in resolved:
        unresolved.pop(value, None)
    expected_fp = tried_total / 2.0 ** 32
    new = {h: v for h, v in resolved.items() if h not in known}
    print('constantes non signees 32 bits : {} ; valeurs >= 0x10000 : {} distinctes'.format(
        len(constants), len(resolved) + len(unresolved)))
    print('chaine retrouvee : {} hashes ({} absents de hp_noms.tsv) ; sans chaine : {}'.format(
        len(resolved), len(new), len(unresolved)))
    print('candidats essayes : {} ; paires fausses attendues : {:.4f}'.format(tried_total, expected_fp))
    if not args.ecrire:
        print('(rien n\'est ecrit sans --ecrire)')
        return
    with DICTIONARY.open('a', encoding='utf-8', newline='\n') as f:
        f.write('# {} : PDB du build du 2020-10-23 (cotw/pdb_2020.py) ; {} : constante compilee (son nom donne la '
                'chaine, sa valeur le hash)\n'.format(datetime.date.today().isoformat(), PASS_NAME))
        for h in sorted(new):
            text, name = new[h]
            f.write('0x{:08x}\t32\t{}\t{}\t{:.4f}\t{} : constante {}\n'.format(h, text, PASS_NAME, expected_fp,
                                                                           SOURCE_TAG, name))
    with NO_STRING.open('w', encoding='utf-8', newline='\n') as f:
        f.write('# Hash32 declares en constantes dans le PDB du build de COTW du 2020-10-23, dont la chaine n\'a pas ete\n'
                '# retrouvee a partir du nom de la constante (cotw/pdb_2020.py dictionnaire). Le nom sert d\'etiquette\n'
                '# (sens du hash) et de piste pour une recherche ciblee ; ce n\'est PAS la chaine hachee.\n'
                '# Colonnes : hash, noms des constantes de meme valeur (separes par " | ").\n')
        for h in sorted(unresolved):
            f.write('0x{:08x}\t{}\n'.format(h, ' | '.join(sorted(set(unresolved[h])))))
    print('ecrit : {} lignes ajoutees a {} ; {}'.format(len(new), DICTIONARY.name, NO_STRING.name))


def cmd_verifier(_args):
    bad = n = 0
    for line in DICTIONARY.read_text(encoding='utf-8').splitlines():
        cols = line.split('\t')
        if len(cols) >= 4 and cols[3].startswith('pdb-'):
            n += 1
            if hash32_func(cols[2]) != int(cols[0], 16):
                bad += 1
                print('FAUX : ' + line)
    print('{} noms pdb-* verifies, {} faux'.format(n, bad))
    return 1 if bad else 0


# ---------------------------------------------------------------------------------------------------------
# Types (TPI) : index, dispositions, vocabulaire
# ---------------------------------------------------------------------------------------------------------
_RX_RECORD = re.compile(r'^\s+0x([0-9A-F]+) \| (LF_\w+) \[size = \d+\](?: `(.*)`)?')
_AGGREGATES = ('LF_STRUCTURE', 'LF_CLASS', 'LF_UNION', 'LF_ENUM')


def build_index(types_txt: Path) -> Dict[int, dict]:
    """{indice de type : {k : genre LF_*, n : nom, l : lignes suivantes}} ; les lignes de continuation d'un membre
    (LF_BCLASS, LF_ONEMETHOD sur deux lignes) sont recollees a leur membre."""
    index: Dict[int, dict] = {}
    cur = None
    with types_txt.open(encoding='utf-8', errors='replace') as f:
        for line in f:
            m = _RX_RECORD.match(line)
            if m:
                cur = {'k': m.group(2), 'n': m.group(3), 'l': []}
                index[int(m.group(1), 16)] = cur
            elif cur is not None:
                text = line.strip()
                if cur['k'] == 'LF_FIELDLIST' and cur['l'] and not text.startswith('- '):
                    cur['l'][-1] += ' ' + text
                else:
                    cur['l'].append(text)
    return index


class TypeIndex:
    def __init__(self, path: Path):
        with Path(path).open('rb') as f:
            self.types: Dict[int, dict] = pickle.load(f)
        self.by_name: Dict[str, int] = {}
        for ti, rec in self.types.items():
            if rec['k'] in _AGGREGATES and rec['n'] and 'forward ref' not in ' '.join(rec['l']):
                self.by_name.setdefault(rec['n'], ti)

    @staticmethod
    def _attr(rec: dict, key: str) -> Optional[str]:
        m = re.search(key + r'(?: =|:) (0x[0-9A-F]+(?: \([^)]*\))?|<no type>)', ' '.join(rec['l']))
        return m.group(1) if m else None

    def field_list(self, rec: dict) -> List[str]:
        """Membres d'un type. Une liste trop longue pour un enregistrement (CWeather, 848 octets) se termine par
        "- LF_INDEX continuation = 0x..." : la suite est dans une autre LF_FIELDLIST, qu'on enchaine."""
        fl = self._attr(rec, 'field list')
        if not fl or fl == '<no type>':
            return []
        lines: List[str] = []
        ti: Optional[int] = int(fl.split(' ')[0], 16)
        seen: Set[int] = set()
        while ti is not None and ti not in seen:
            seen.add(ti)
            ti_next = None
            for line in self.types.get(ti, {'l': []})['l']:
                m = re.match(r'- LF_INDEX continuation = (0x[0-9A-F]+)', line)
                if m:
                    ti_next = int(m.group(1), 16)
                else:
                    lines.append(line)
            ti = ti_next
        return lines

    def type_name(self, ref: str) -> str:
        m = re.match(r'(0x[0-9A-F]+)(?: \((.*)\))?', ref)
        if m.group(2):
            return m.group(2)
        rec = self.types.get(int(m.group(1), 16))
        if rec is None:
            return ref
        kind, text = rec['k'], ' '.join(rec['l'])
        if kind in _AGGREGATES:
            return rec['n']
        if kind == 'LF_POINTER':
            return self.type_name(self._attr(rec, 'referent')) + '*'
        if kind == 'LF_MODIFIER':
            mm = re.search(r'referent = (0x[0-9A-F]+(?: \([^)]*\))?), modifiers = (.*)', text)
            return '{} {}'.format(mm.group(2).strip().lower(), self.type_name(mm.group(1))) if mm else kind
        if kind == 'LF_ARRAY':
            # "size: 120, index type: 0x0023 (unsigned __int64), element type: 0x77912"
            mm = re.search(r'element type(?: =|:) (0x[0-9A-F]+(?: \([^)]*\))?)', text)
            size = re.search(r'\bsize(?: =|:) (\d+)', text)
            element = self.type_name(mm.group(1)) if mm else '?'
            return '{}[{} octets]'.format(element, size.group(1) if size else '?')
        if kind == 'LF_BITFIELD':
            mm = re.search(r'type = (0x[0-9A-F]+(?: \([^)]*\))?), bit offset = (\d+), # bits = (\d+)', text)
            return '{} bits {}..{}'.format(self.type_name(mm.group(1)), mm.group(2),
                                           int(mm.group(2)) + int(mm.group(3)) - 1) if mm else kind
        if kind in ('LF_PROCEDURE', 'LF_MFUNCTION'):
            return 'fonction'
        return kind

    def layout(self, name: str, out=print, indent: str = '', base: int = 0, depth: int = 0, max_depth: int = 1):
        ti = self.by_name.get(name)
        if ti is None:
            out(indent + 'introuvable : ' + name)
            return
        rec = self.types[ti]
        if rec['k'] == 'LF_ENUM':
            out(indent + 'enum ' + name)
            for line in self.field_list(rec):
                m = re.match(r'- LF_ENUMERATE \[(.*) = (-?\d+)\]', line)
                if m:
                    out('{}  {} = {}'.format(indent, m.group(1), m.group(2)))
            return
        if depth == 0:
            size = re.search(r'sizeof (\d+)', ' '.join(rec['l']))
            out('{}{} {}  (sizeof {})'.format(indent, rec['k'][3:].lower(), name, size.group(1) if size else '?'))
        for line in self.field_list(rec):
            m = re.match(r'- LF_BCLASS.*?type = (0x[0-9A-F]+), offset = (\d+)', line)
            if m:
                base_name = self.type_name(m.group(1))
                out('{}  +0x{:X} base {}'.format(indent, base + int(m.group(2)), base_name))
                if depth < max_depth:
                    self.layout(base_name, out, indent + '    ', base + int(m.group(2)), depth + 1, max_depth)
                continue
            m = re.match(r'- LF_MEMBER \[name = `([^`]*)`, Type = (0x[0-9A-F]+(?: \([^)]*\))?), offset = (\d+)', line)
            if m:
                out('{}  +0x{:X} {:<40} {}'.format(indent, base + int(m.group(3)), self.type_name(m.group(2)),
                                                    m.group(1)))
            elif line.startswith('- LF_VFUNCTAB'):
                out('{}  +0x{:X} vftable*'.format(indent, base))


# Bibliotheques tierces liees a l'exe : leurs identifiants ne nomment aucune donnee du jeu.
_THIRD_PARTY = re.compile(r'^(?:hk|hcl|Scaleform|boost|std|stdext|D3D|DXGI|ID3D|IDXGI|Nv|NV|nv|fmod|FMOD|Bink|'
                          r'physx|Px|Steam|ISteam|Concurrency|Microsoft|ATL|_|tinyxml|TiXml|rapidjson|Json|'
                          r'oodle|Oodle|rad|RAD|tm|Granny|Wwise|AK|Ak|Gfx|GFx|dynamo|curl|ssl|SSL|EVP|BIO|X509)')
_RX_IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]{1,80}')


def game_identifiers(index: TypeIndex, globals_txt: str) -> Set[str]:
    """Identifiants du code du jeu : noms des types, de leurs membres et enumerateurs, et des constantes (avec les
    morceaux de leur nom). Les prefixes m_, s_, k_ sont laisses : resoudre_hashes.py fait ses propres variantes."""
    out: Set[str] = set()
    for name, ti in index.by_name.items():
        top = name.split('::')[0].split('<')[0]
        if _THIRD_PARTY.match(top) or '<' in name:
            continue
        out.update(p for p in name.split('::') if _RX_IDENT.fullmatch(p))
        for line in index.field_list(index.types[ti]):
            m = re.match(r'- LF_(?:MEMBER|STMEMBER|ONEMETHOD|NESTTYPE) \[name = `([^`<]+)`', line) or \
                re.match(r'- LF_ENUMERATE \[(\w+) = ', line)
            if m and _RX_IDENT.fullmatch(m.group(1)) and not m.group(1).startswith('operator'):
                out.add(m.group(1))
    for name, _value in read_constants(globals_txt):
        if _THIRD_PARTY.match(name):
            continue
        for seg in name.split('::'):
            parts = seg.split('_')
            out.update(p for p in parts if _RX_IDENT.fullmatch(p))
            if len(parts) > 1:
                out.add('_'.join(parts[1:]))
    return {s for s in out if len(s) >= 3}


# ---------------------------------------------------------------------------------------------------------
# Exe : symboles publics, desassemblage, appelants
# ---------------------------------------------------------------------------------------------------------
class Image:
    """Sections d'un exe PE64 et symboles publics du PDB (demangles par llvm-undname quand il est present)."""

    def __init__(self, exe: Path, publics_txt: Path):
        self.path = Path(exe)
        self.data = self.path.read_bytes()
        pe = struct.unpack_from('<I', self.data, 0x3C)[0]
        count = struct.unpack_from('<H', self.data, pe + 6)[0]
        opt_size = struct.unpack_from('<H', self.data, pe + 20)[0]
        self.image_base = struct.unpack_from('<Q', self.data, pe + 24 + 24)[0]
        self.sections = []      # (nom, rva, taille virtuelle, offset fichier, taille fichier)
        for i in range(count):
            name, vsize, rva, rsize, raw = struct.unpack_from('<8sIIII', self.data, pe + 24 + opt_size + 40 * i)
            self.sections.append((name.rstrip(b'\0').decode('ascii', 'replace'), rva, vsize, raw, rsize))
        text = Path(publics_txt).read_text(encoding='utf-8', errors='replace')
        found = re.findall(r'`([^`]+)`\n\s+flags = ([^,]*), addr = (\d+):(\d+)', text)
        names = self._demangle([n for n, *_ in found])
        self.symbols = sorted((self.image_base + self.sections[int(s) - 1][1] + int(off), names.get(n, n),
                               'function' in flags)
                              for n, flags, s, off in found if 0 < int(s) <= len(self.sections))
        self.addresses = [a for a, _, _ in self.symbols]

    @staticmethod
    def _demangle(mangled: List[str]) -> Dict[str, str]:
        try:
            out = subprocess.run(['llvm-undname'], input='\n'.join(mangled), capture_output=True, text=True).stdout
        except OSError:
            return {}
        names = {}
        for block in out.split('\n\n'):
            lines = block.strip('\n').split('\n')
            if len(lines) >= 2:
                names[lines[0]] = lines[1]
        return names

    def name_at(self, va: int) -> Optional[str]:
        i = bisect.bisect_right(self.addresses, va) - 1
        if i < 0:
            return None
        addr, name, _ = self.symbols[i]
        name = re.sub(r'^(?:public|private|protected): |virtual |static |__cdecl ', '', name)
        return name if va == addr else '{}+0x{:X}'.format(name, va - addr)

    def functions(self, pattern: str) -> List[Tuple[int, int, str]]:
        """(adresse, fin, nom) des fonctions dont le nom contient le motif ; fin = symbole suivant."""
        out = []
        for i, (addr, name, is_function) in enumerate(self.symbols):
            if is_function and re.search(pattern, name):
                end = self.symbols[i + 1][0] if i + 1 < len(self.symbols) else addr + 0x400
                out.append((addr, end, name))
        return out

    def disassemble(self, addr: int, end: int) -> List[str]:
        text = subprocess.run(['llvm-objdump', '-d', '--no-show-raw-insn', '--x86-asm-syntax=intel',
                               '--start-address=0x{:X}'.format(addr), '--stop-address=0x{:X}'.format(end),
                               str(self.path)], capture_output=True, text=True).stdout
        lines = []
        for line in text.splitlines():
            if not re.match(r'\s*[0-9a-f]+:', line) or line.rstrip().endswith('int3'):
                continue
            line = re.sub(r' <[^>]*>', '', line)

            def label(m):
                va = int(m.group(1), 16)
                name = self.name_at(va) if va >= self.image_base else None
                return '{}  <{}>'.format(m.group(0), name) if name else m.group(0)
            lines.append(re.sub(r'0x(14[0-9a-f]{7})(?![0-9a-f])', label, line))
        return lines

    def callers(self, target: int) -> List[Tuple[int, str]]:
        """Sites des call / jmp rel32 dont la cible est target (sections executables seulement)."""
        out = []
        for name, rva, _vsize, raw, rsize in self.sections:
            if name != '.text':
                continue
            code = self.data[raw:raw + rsize]
            for m in re.finditer(b'[\xe8\xe9]', code):
                i = m.start()
                if i + 5 > len(code):
                    break
                src = self.image_base + rva + i
                if src + 5 + struct.unpack_from('<i', code, i + 1)[0] == target:
                    out.append((src, 'call' if code[i] == 0xE8 else 'jmp'))
        return out


# ---------------------------------------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('dictionnaire')
    s.add_argument('globals', help='sortie de llvm-pdbutil dump -globals')
    s.add_argument('--ecrire', action='store_true', help='ajouter les noms nouveaux a hp_noms.tsv')
    s = sub.add_parser('index')
    s.add_argument('types', help='sortie de llvm-pdbutil dump -types')
    s.add_argument('index', help='index a ecrire (pickle)')
    s = sub.add_parser('vocabulaire')
    s.add_argument('index')
    s.add_argument('globals')
    s.add_argument('--out', default=str(IDENTIFIERS))
    s = sub.add_parser('structure')
    s.add_argument('index')
    s.add_argument('noms', nargs='+')
    s.add_argument('--profondeur', type=int, default=1, help='niveaux de classes de base developpes')
    for name in ('desassembler', 'appelants'):
        s = sub.add_parser(name)
        s.add_argument('exe')
        s.add_argument('publics', help='sortie de llvm-pdbutil dump -publics')
        s.add_argument('motif', help='expression reguliere sur le nom demangle')
    sub.add_parser('verifier')
    args = p.parse_args(argv)

    if args.cmd == 'dictionnaire':
        cmd_dictionnaire(args)
    elif args.cmd == 'verifier':
        return cmd_verifier(args)
    elif args.cmd == 'index':
        with Path(args.index).open('wb') as f:
            pickle.dump(build_index(Path(args.types)), f, protocol=4)
    elif args.cmd == 'vocabulaire':
        words = game_identifiers(TypeIndex(Path(args.index)),
                                 Path(args.globals).read_text(encoding='utf-8', errors='replace'))
        Path(args.out).write_text('# Identifiants du code de COTW (PDB du build du 2020-10-23), cotw/pdb_2020.py\n' +
                                  '\n'.join(sorted(words)) + '\n', encoding='utf-8', newline='\n')
        print('{} identifiants -> {}'.format(len(words), args.out))
    elif args.cmd == 'structure':
        index = TypeIndex(Path(args.index))
        for name in args.noms:
            index.layout(name, max_depth=args.profondeur)
    else:
        image = Image(Path(args.exe), Path(args.publics))
        for addr, end, name in image.functions(args.motif):
            print('=== {}  @0x{:X} ({} octets)'.format(name, addr, end - addr))
            if args.cmd == 'desassembler':
                print('\n'.join(image.disassemble(addr, end)))
            else:
                for src, kind in image.callers(addr):
                    print('   {} depuis 0x{:X}  {}'.format(kind, src, image.name_at(src)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
