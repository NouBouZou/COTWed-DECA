#!/usr/bin/env python3
"""
generer_noms.py -- propose des noms pour les fichiers sans nom, a partir des noms deja connus.

Pourquoi : il reste environ 10 000 fichiers sans nom sur COTW (audit du 08/10). Les noms du jeu suivent des
series : dialogues numerotes, tuiles de textures, memes fichiers d'une reserve a l'autre. L'essai du 07/10 a
retrouve 44 % des noms manquants en faisant seulement varier les numeros des noms connus.

Passes (chacune produit des candidats ; seul un candidat dont le hash32 est celui d'un fichier sans nom compte) :
  - chiffres : chaque groupe de chiffres d'un nom connu prend toutes les valeurs de sa largeur ; la largeur 3 est
    reservee aux series (au moins deux noms connus qui ne different que par ce groupe) et aux extensions qui ont
    beaucoup de cibles (au moins 5 % des fichiers sans nom ont un type attendu pour elles) ;
  - suffixes : le dernier mot d'un nom ("_dif", "_nrm"...) est remplace par les mots les plus frequents de la meme
    extension ;
  - dossiers : un fichier d'un dossier est essaye dans les dossiers freres (meme parent), tel quel et avec le nom
    du dossier remplace dans le nom du fichier ;
  - exe : chaines en forme de chemin de l'executable, et motifs printf ("zoom%d/%d.ddsc") developpes ;
  - les noms trouves servent de graines a une nouvelle iteration, tant qu'elle trouve quelque chose.
Chaque candidat est aussi essaye avec ses extensions associees (hp.json : ".ddsc" -> ".hmddsc"...).

Controle : un nom touche n'est garde que si le type detecte par DECA dans le fichier est attendu pour son
extension (noms_fichiers.ModeleTypes). Les refus mesurent le taux reel de faux positifs ; le rapport donne aussi le
nombre attendu par le calcul (candidats x cibles / 2^32). Un hash touche par deux noms acceptes est ecarte.

Le resultat est un fichier de noms pour renommer.py. Rien n'est ecrit dans la base.

Usage, depuis la racine du depot, environnement virtuel active :
    python cotw\\generer_noms.py D:\\dev\\work\\hp --sortie D:\\dev\\audits\\noms\\noms_generes.txt --rapport D:\\dev\\audits\\noms\\generer_noms.md
    python cotw\\renommer.py ajouter D:\\dev\\work\\hp --noms D:\\dev\\audits\\noms\\noms_generes.txt
Sans le jeu (portable), a partir des sorties d'un audit et de vpaths.txt :
    python cotw\\generer_noms.py --hors-ligne vpaths.txt hashes_orphelins.csv couverture_extensions.csv --sortie noms.txt
"""
import argparse
import collections
import csv
import re
import sys
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402,F401  (installe le substitut de zugbruecke sous Linux)
import noms_fichiers as NF  # noqa: E402

RX_CHIFFRES = re.compile(r'\d+')
PASSES = ('chiffres', 'suffixes', 'dossiers', 'exe')
LARGEUR_MAX = 2            # largeur des groupes de chiffres variee pour tout nom
LARGEUR_SERIE = 3          # largeur variee pour une serie (au moins SERIE_MIN noms connus)
SERIE_MIN = 2
PART_LARGE = 0.05          # part des cibles a partir de laquelle une extension merite la largeur 3
SUFFIXES_MAX = 40          # mots les plus frequents par extension (passe suffixes)
DOSSIERS_MAX_CANDIDATS = 2_000_000   # par dossier parent (passe dossiers)
PRINTF_MAX = 1000          # valeurs d'un %d seul dans un motif (voir PRINTF_PAR_NOMBRE)


def separer(path: str) -> Tuple[str, str]:
    """(radical, extension complete) : 'a/b.nl.mdic' -> ('a/b', '.nl.mdic')."""
    slash = path.rfind('/')
    dot = path.find('.', slash + 1)
    if dot <= slash + 1:
        return path, ''
    return path[:dot], path[dot:]


def extensions_associees(file_assoc: List[dict]) -> Dict[str, List[str]]:
    """Extension d'un groupe de hp.json -> autres extensions du groupe. DECA compare la DERNIERE extension d'un
    nom aux cles du groupe : une cle composee (".nl.mdic") n'est donc jamais une source, seulement une cible."""
    out: Dict[str, List[str]] = collections.defaultdict(list)
    for groupe in file_assoc or []:
        exts = [NF.to_text(k) for k in groupe]
        for e in exts:
            out[e] += [autre for autre in exts if autre != e]
    return out


# ---------------------------------------------------------------------------------------------------------
# Recherche : hash, controle de type, statistiques par passe
# ---------------------------------------------------------------------------------------------------------

class Stat:
    def __init__(self, nom: str):
        self.nom = nom
        self.candidats = 0
        self.touches = 0
        self.acceptes = 0
        self.refuses = 0
        self.non_controles = 0
        self.secondes = 0.0


class Chercheur:
    def __init__(self, etat: NF.Etat, modele: NF.ModeleTypes, assoc: Dict[str, List[str]]):
        from deca.hashes import hash32_func_bytes
        self._hash = hash32_func_bytes
        self.etat = etat
        self.modele = modele
        self.assoc = assoc
        self.cibles = etat.sans_nom
        self.trouves: Dict[int, Dict[str, str]] = collections.defaultdict(dict)   # hash -> nom -> passe
        self.non_controles: Dict[int, Dict[str, str]] = collections.defaultdict(dict)
        self.refus: List[Tuple[str, int, Set[Optional[str]], str]] = []
        self.stats: List[Stat] = []
        self.nouveaux: Set[str] = set()
        self._refus_vus: Set[Tuple[str, int]] = set()

    def essayer(self, nom: str, stat: Stat):
        stat.candidats += 1
        try:
            h = int(self._hash(nom.encode('ascii')))
        except UnicodeEncodeError:
            return
        types = self.cibles.get(h)
        if types is None:
            return
        stat.touches += 1
        if nom in self.trouves[h] or nom in self.non_controles[h]:
            return
        verdict = self.modele.accepte(nom, types)
        if verdict is False:
            if (nom, h) not in self._refus_vus:
                self._refus_vus.add((nom, h))
                stat.refuses += 1
                self.refus.append((nom, h, types, stat.nom))
        elif verdict is None:
            stat.non_controles += 1
            self.non_controles[h][nom] = stat.nom
        else:
            stat.acceptes += 1
            self.trouves[h][nom] = stat.nom
            self.nouveaux.add(nom)

    def essayer_avec_associes(self, nom: str, stat: Stat):
        """Le nom, puis ses noms associes selon la regle de DECA (db_processor.find_vpath_by_assoc) : si la
        derniere extension appartient a un groupe de hp.json, chaque autre extension du groupe la remplace
        ("x.nl" -> "x.nl.mdic", "x.ddsc" -> "x.hmddsc" ; "x.nl.mdic" ne donne rien, sa derniere extension est
        ".mdic")."""
        self.essayer(nom, stat)
        dot = nom.rfind('.')
        if dot > nom.rfind('/') + 1:
            for autre in self.assoc.get(nom[dot:], ()):
                self.essayer(nom[:dot] + autre, stat)

    def passe(self, nom: str, generateur: Callable[[Stat], None]) -> Stat:
        stat = Stat(nom)
        t = time.time()
        generateur(stat)
        stat.secondes = time.time() - t
        self.stats.append(stat)
        return stat

    def resultats(self) -> Tuple[Dict[int, Tuple[str, str]], Dict[int, List[str]]]:
        """(hash -> (nom, passe)) sans ambiguite, et hashes ambigus -> noms."""
        retenus, ambigus = {}, {}
        for h, noms in self.trouves.items():
            if len(noms) == 1:
                (nom, passe), = noms.items()
                retenus[h] = (nom, passe)
            elif noms:
                ambigus[h] = sorted(noms)
        return retenus, ambigus


# ---------------------------------------------------------------------------------------------------------
# Passes
# ---------------------------------------------------------------------------------------------------------

def _valeurs(groupe: str, largeur: int) -> List[str]:
    """Valeurs a essayer pour un groupe de chiffres. Avec un zero en tete ("07") : toutes les valeurs de meme
    largeur. Sans ("10", "5") : les nombres sans zeros de 0 a 10^max(largeur, 2) - 1 (un "5" peut avoir des
    voisins "12"), plus les valeurs de meme largeur avec zeros ("dia10" a des voisins "dia00")."""
    if len(groupe) > 1 and groupe[0] == '0':
        return [str(i).zfill(largeur) for i in range(10 ** largeur)]
    out = [str(i) for i in range(10 ** max(largeur, 2))]
    out += [str(i).zfill(largeur) for i in range(10 ** (largeur - 1))] if largeur > 1 else []
    return out


def familles_chiffres(graines: Iterable[str], deja: Set[tuple],
                      large: Callable[[str], bool] = lambda ext: False) -> List[Tuple[str, str, str, str, int]]:
    """(avant, groupe, apres, ext, largeur) pour chaque groupe de chiffres du radical des graines. Au-dela de
    LARGEUR_MAX (et jusqu'a LARGEUR_SERIE), un groupe n'est varie que pour une serie (au moins SERIE_MIN graines
    avec les memes avant/apres/ext) ou une extension ou les cibles abondent (large(ext))."""
    serie = collections.Counter()
    brutes = []
    for p in graines:
        radical, ext = separer(p)
        for m in RX_CHIFFRES.finditer(radical):
            g = m.group(0)
            if len(g) > LARGEUR_SERIE:
                continue
            cle = (radical[:m.start()], radical[m.end():], ext, len(g), g[0] == '0' and len(g) > 1)
            serie[cle] += 1
            brutes.append((cle, g))
    out = []
    for cle, g in brutes:
        avant, apres, ext, largeur, _zero = cle
        if largeur > LARGEUR_MAX and serie[cle] < SERIE_MIN and not large(ext):
            continue
        if cle in deja:
            continue
        deja.add(cle)
        out.append((avant, g, apres, ext, largeur))
    return out


def extensions_larges(etat: NF.Etat, modele: NF.ModeleTypes, part: float = PART_LARGE) -> Callable[[str], bool]:
    """Predicat : l'extension a assez de cibles (part des fichiers sans nom dont le type est attendu pour elle)
    pour justifier de varier les groupes de 3 chiffres. Sur COTW : .wavc (43 %), .ddsc (27 %), .hmddsc (23 %)."""
    cache: Dict[str, bool] = {}
    total = max(len(etat.sans_nom), 1)

    def large(ext: str) -> bool:
        if ext not in cache:
            attendus = modele.types_pour('x' + ext)
            if not attendus:
                cache[ext] = False
            else:
                n = sum(1 for types in etat.sans_nom.values() if types & attendus)
                cache[ext] = n / total >= part
        return cache[ext]
    return large


def passe_chiffres(ch: Chercheur, graines: Iterable[str], deja: Set[tuple],
                   large: Callable[[str], bool] = lambda ext: False) -> Callable[[Stat], None]:
    def run(stat: Stat):
        for avant, g, apres, ext, largeur in familles_chiffres(graines, deja, large):
            for v in _valeurs(g, largeur):
                ch.essayer_avec_associes(avant + v + apres + ext, stat)
    return run


def passe_suffixes(ch: Chercheur, noms: List[str], sources: Optional[List[str]] = None) -> Callable[[Stat], None]:
    """Dernier mot du nom ("_dif", "_nrm", "_alpha"...) remplace par les SUFFIXES_MAX mots les plus frequents de
    la meme extension, appris sur tous les noms ; seuls les noms de sources (defaut : tous) sont modifies."""
    def decouper(p):
        radical, ext = separer(p)
        if '_' not in radical.rsplit('/', 1)[-1]:
            return None
        tete, mot = radical.rsplit('_', 1)
        return tete, mot, ext

    def run(stat: Stat):
        par_ext: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        for p in noms:
            d = decouper(p)
            if d:
                par_ext[d[2]][d[1]] += 1
        top = {ext: [m for m, _n in c.most_common(SUFFIXES_MAX)] for ext, c in par_ext.items()}
        vus = set()
        for p in (noms if sources is None else sources):
            d = decouper(p)
            if not d:
                continue
            tete, mot, ext = d
            if (tete, ext) in vus or mot not in top.get(ext, ()):
                continue
            vus.add((tete, ext))
            for autre in top[ext]:
                if autre != mot:
                    ch.essayer_avec_associes(tete + '_' + autre + ext, stat)
    return run


def passe_dossiers(ch: Chercheur, noms: List[str], sources: Optional[List[str]] = None) -> Callable[[Stat], None]:
    """Chaque fichier de sources (defaut : tous les noms) est essaye dans les dossiers freres de son dossier
    (memes parent ; freres appris sur tous les noms), tel quel puis avec le nom du dossier remplace dans le nom
    du fichier ("hp_africa/x_hp_africa.ddsc" -> "hp_mexico/x_hp_mexico.ddsc")."""
    def run(stat: Stat):
        freres: Dict[str, Set[str]] = collections.defaultdict(set)
        contenu: Dict[str, Set[str]] = collections.defaultdict(set)
        for p in noms:
            parts = p.split('/')
            if len(parts) >= 3:
                parent = '/'.join(parts[:-2])
                freres[parent].add(parts[-2])
                contenu[parent + '/' + parts[-2]].add(parts[-1])
        par_parent = collections.defaultdict(list)
        for p in (noms if sources is None else sources):
            parts = p.split('/')
            if len(parts) >= 3:
                par_parent['/'.join(parts[:-2])].append((parts[-2], parts[-1]))
        for parent, fichiers in par_parent.items():
            dossiers = freres.get(parent, set())
            if len(dossiers) < 2 or len(fichiers) * (len(dossiers) - 1) > DOSSIERS_MAX_CANDIDATS:
                continue
            for d1, f in fichiers:
                for d2 in dossiers:
                    if d2 == d1:
                        continue
                    existants = contenu[parent + '/' + d2]
                    for nom in {f, f.replace(d1, d2)} if d1 in f else {f}:
                        if nom not in existants:
                            ch.essayer_avec_associes('{}/{}/{}'.format(parent, d2, nom), stat)
    return run


RX_PRINTF = re.compile(r'%(0?)(\d*)[diu]')
PRINTF_PAR_NOMBRE = {1: PRINTF_MAX, 2: 300, 3: 64}   # valeurs par conversion selon leur nombre


def developper_motif(motif: str) -> Iterator[str]:
    """Developpe les %d / %i / %u / %0Nd d'un motif printf (3 au plus). Un motif avec %s, %x ou %c n'est pas
    developpe : la valeur n'est pas un nombre decimal."""
    if re.search(r'%[-+ #]*\d*(?:\.\d+)?(?:hh|h|ll|l|z)?[sxXc]', motif):
        return
    convs = list(RX_PRINTF.finditer(motif))
    if not convs or len(convs) > 3:
        return
    maximum = PRINTF_PAR_NOMBRE[len(convs)]
    morceaux = RX_PRINTF.split(motif)   # texte, zero, largeur, texte, ...
    textes = morceaux[0::3]
    listes = []
    for m in convs:
        largeur = int(m.group(2)) if m.group(2) else 0
        if m.group(1) and largeur:
            listes.append([str(i).zfill(largeur) for i in range(min(10 ** largeur, maximum))])
        else:
            listes.append([str(i) for i in range(maximum)])

    def rec(i, prefixe):
        if i == len(listes):
            yield prefixe
            return
        for v in listes[i]:
            yield from rec(i + 1, prefixe + v + textes[i + 1])

    yield from rec(0, textes[0])


def passe_exe(ch: Chercheur, chemins: Iterable[str], motifs: Iterable[str]) -> Callable[[Stat], None]:
    def run(stat: Stat):
        for c in chemins:
            ch.essayer_avec_associes(c, stat)
        for m in motifs:
            for c in developper_motif(m):
                ch.essayer_avec_associes(c, stat)
    return run


# ---------------------------------------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------------------------------------

def generer(etat: NF.Etat, modele: NF.ModeleTypes, file_assoc: List[dict], passes: Iterable[str],
            exe_chemins: Iterable[str] = (), exe_motifs: Iterable[str] = (), iterations: int = 5) -> Chercheur:
    ch = Chercheur(etat, modele, extensions_associees(file_assoc))
    passes = list(passes)
    noms = sorted(etat.nommes)
    deja: Set[tuple] = set()
    large = extensions_larges(etat, modele)
    if 'exe' in passes:
        ch.passe('exe', passe_exe(ch, exe_chemins, exe_motifs))
    if 'chiffres' in passes:
        ch.passe('chiffres', passe_chiffres(ch, noms, deja, large))
    if 'suffixes' in passes:
        ch.passe('suffixes', passe_suffixes(ch, noms))
    if 'dossiers' in passes:
        ch.passe('dossiers', passe_dossiers(ch, noms))
    # iterations : seuls les noms trouves a l'iteration precedente sont modifies ; les series, les mots
    # frequents et les dossiers freres sont appris sur tous les noms connus
    for i in range(iterations):
        graines = sorted(ch.nouveaux)
        ch.nouveaux = set()
        if not graines:
            break
        noms = sorted(set(noms) | set(graines))
        if 'chiffres' in passes:
            ch.passe('chiffres+{}'.format(i + 1), passe_chiffres(ch, noms, deja, large))
        if 'suffixes' in passes:
            ch.passe('suffixes+{}'.format(i + 1), passe_suffixes(ch, noms, graines))
        if 'dossiers' in passes:
            ch.passe('dossiers+{}'.format(i + 1), passe_dossiers(ch, noms, graines))
    return ch


def cibles_fictives(etat: NF.Etat, graine: int = 20261008) -> NF.Etat:
    """Copie de l'etat ou les fichiers sans nom sont remplaces par autant de hashes tires au hasard (aucun n'est
    le hash d'un fichier du projet), avec la meme repartition de types. Tout nom accepte sur ces cibles est un
    faux positif : c'est la mesure directe des faux positifs qui passent le controle de type."""
    import random
    rng = random.Random(graine)
    reels = set(etat.sans_nom)
    from deca.hashes import hash32_func_bytes
    reels |= {int(hash32_func_bytes(n.encode('utf-8', 'replace'))) for n in etat.nommes}
    fictif = NF.Etat()
    fictif.nommes = etat.nommes
    fictif.types_par_extension = etat.types_par_extension
    fictif.types_par_derniere = etat.types_par_derniere
    for types in etat.sans_nom.values():
        while True:
            h = rng.getrandbits(32)
            if h not in reels and h not in fictif.sans_nom:
                break
        fictif.sans_nom[h] = set(types)
    return fictif


def etat_hors_ligne(vpaths: str, orphelins_csv: str, couverture_csv: str) -> NF.Etat:
    """Etat reconstruit sans la base : noms de vpaths.txt, fichiers sans nom et leurs types de
    hashes_orphelins.csv, types par extension de couverture_extensions.csv (sorties de deca_audit.py)."""
    etat = NF.Etat()
    with open(vpaths, encoding='utf-8', errors='replace') as f:
        for line in f:
            v = line.split('\t', 1)[0].strip()
            if v:
                etat.nommes.add(v)
    with open(orphelins_csv, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f, delimiter=';'):
            t = r['type_detecte']
            etat.sans_nom.setdefault(int(r['hash'], 16), set()).add(None if t in ('', '(aucun type)', 'NOT') else t)
    rx = re.compile(r'([^,()]+?|\(aucun\)) \((\d+)\)')
    with open(couverture_csv, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f, delimiter=';'):
            fam = r['famille']
            if fam.startswith('(sans nom)'):
                continue
            c = collections.Counter()
            for t, n in rx.findall(r['types_detectes']):
                t = t.strip()
                if t != NF.TYPE_LIEN:
                    c[None if t == '(aucun)' else t] += int(n)
            etat.types_par_extension[fam] = c
            derniere = '.' + fam.rsplit('.', 1)[1] if fam.count('.') > 1 else fam
            etat.types_par_derniere.setdefault(derniere, collections.Counter()).update(c)
    return etat


def ecrire(ch: Chercheur, sortie: Path, avec_non_controles: bool) -> Tuple[int, int]:
    retenus, _ambigus = ch.resultats()
    noms = sorted(n for n, _p in retenus.values())
    extra = []
    if avec_non_controles:
        extra = sorted(n for h, d in ch.non_controles.items() if h not in retenus and len(d) == 1 for n in d)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    with open(sortie, 'w', encoding='utf-8', newline='\n') as f:
        f.write('# generer_noms.py : {} noms (types controles){}\n'.format(
            len(noms), ', {} sans controle'.format(len(extra)) if extra else ''))
        for n in noms + extra:
            f.write(n + '\n')
    return len(noms), len(extra)


def rapport(ch: Chercheur, n_cibles: int, limite: int = 25, temoin: Optional['Chercheur'] = None) -> List[str]:
    retenus, ambigus = ch.resultats()
    L = ['# generer_noms.py', '',
         'Cibles : {} fichiers sans nom. Faux positifs attendus d\'une passe = candidats x cibles / 2^32 '
         '(majorant : un candidat produit deux fois compte deux fois). Les refus pour type incoherent mesurent les '
         'faux positifs reels.'.format(n_cibles), '',
         '| Passe | Candidats | Touches | Acceptes | Refuses (type) | Sans controle | FP attendus | Duree |',
         '|---|---:|---:|---:|---:|---:|---:|---:|']
    for s in ch.stats:
        L.append('| {} | {} | {} | {} | {} | {} | {:.2f} | {:.0f} s |'.format(
            s.nom, s.candidats, s.touches, s.acceptes, s.refuses, s.non_controles,
            NF.faux_positifs_attendus(s.candidats, n_cibles), s.secondes))
    par_ext = collections.Counter(NF.extension_complete(n) for n, _p in retenus.values())
    par_type = collections.Counter()
    for h in retenus:
        for t in ch.cibles[h]:
            par_type['(aucun)' if t is None else t] += 1
    restants = collections.Counter()
    for h, types in ch.cibles.items():
        if h not in retenus:
            restants[', '.join(sorted('(aucun)' if t is None else t for t in types))] += 1
    L += ['', '**Noms retenus : {}** sur {} fichiers sans nom ({:.1f} %) ; ambigus ecartes : {} ; refus : {}.'.format(
        len(retenus), n_cibles, 100.0 * len(retenus) / max(n_cibles, 1), len(ambigus), len(ch.refus)), '']
    if temoin is not None:
        t_retenus, _t_amb = temoin.resultats()
        L += ['**Temoin (memes passes sur {} cibles fictives de memes types)** : {} nom(s) accepte(s), {} refuse(s). '
              'Les noms acceptes du temoin sont tous des faux positifs : c\'est l\'estimation du nombre de noms '
              'faux parmi les {} retenus ci-dessus.'.format(n_cibles, len(t_retenus), len(temoin.refus), len(retenus)),
              '']
        if t_retenus:
            L += ['Exemples de faux positifs du temoin : ' + ', '.join(
                '`{}`'.format(n) for n, _p in list(t_retenus.values())[:10]), '']
    L += ['| Extension | Noms |', '|---|---:|'] + ['| `{}` | {} |'.format(e, n) for e, n in par_ext.most_common()]
    L += ['', '| Type des fichiers nommes | Fichiers |', '|---|---:|']
    L += ['| {} | {} |'.format(t, n) for t, n in par_type.most_common()]
    L += ['', '| Type des fichiers encore sans nom | Fichiers |', '|---|---:|']
    L += ['| {} | {} |'.format(t, n) for t, n in restants.most_common()]
    if ch.refus:
        L += ['', '## Refus pour type incoherent (exemples)', '', '| Nom | Hash | Type du fichier | Passe |',
              '|---|---|---|---|']
        L += ['| `{}` | {:08x} | {} | {} |'.format(n, h, ', '.join(sorted('(aucun)' if t is None else t for t in ty)), p)
              for n, h, ty, p in ch.refus[:limite]]
    if ambigus:
        L += ['', '## Ambigus (ecartes)', '']
        L += ['- {:08x} : {}'.format(h, ', '.join('`{}`'.format(n) for n in noms))
              for h, noms in list(ambigus.items())[:limite]]
    nc = [(h, n) for h, d in ch.non_controles.items() if h not in retenus for n in d]
    if nc:
        L += ['', '## Sans controle de type (extension jamais vue ; non ecrits sauf --avec-non-controles)', '']
        L += ['- `{}`'.format(n) for _h, n in nc[:limite]]
    return L


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Propose des noms pour les fichiers sans nom d\'un projet DECA.')
    ap.add_argument('projet', nargs='?', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('--hors-ligne', nargs=3, metavar=('VPATHS', 'ORPHELINS_CSV', 'COUVERTURE_CSV'),
                    help='sans la base : vpaths.txt, hashes_orphelins.csv et couverture_extensions.csv d\'un audit')
    ap.add_argument('--exe', help='executable du jeu (defaut avec un projet : celui du projet)')
    ap.add_argument('--passes', default=','.join(PASSES), help='passes : ' + ','.join(PASSES))
    ap.add_argument('--iterations', type=int, default=5, help='iterations sur les noms trouves (defaut 5)')
    ap.add_argument('--sortie', required=True, help='fichier de noms a donner a renommer.py')
    ap.add_argument('--avec-non-controles', action='store_true',
                    help='ecrire aussi les noms d\'extension jamais vue (sans controle de type)')
    ap.add_argument('--rapport', help='rapport markdown (sinon : affichage a l\'ecran)')
    ap.add_argument('--temoin', action='store_true',
                    help='rejouer les passes sur des cibles fictives pour mesurer les faux positifs acceptes '
                         '(double la duree)')
    args = ap.parse_args(argv)

    if args.hors_ligne:
        etat = etat_hors_ligne(*args.hors_ligne)
        file_assoc = _file_assoc_hp()
        exe = args.exe
    elif args.projet:
        vfs = _projet.open_project(args.projet)
        etat = NF.Etat.depuis_base(vfs)
        file_assoc = vfs.game_info.file_assoc()
        exe = args.exe or str(Path(vfs.game_info.game_dir) / vfs.game_info.exe_name)
        vfs.db_conn.close()
    else:
        raise SystemExit('ERREUR : donner un projet ou --hors-ligne')
    passes = [p for p in args.passes.split(',') if p]
    chemins, motifs = [], []
    if 'exe' in passes and exe and Path(exe).is_file():
        from deca.exe_strings import exe_path_strings
        p, m = exe_path_strings(exe)
        chemins = sorted(x.decode('ascii') for x in p)
        motifs = sorted(x.decode('ascii') for x in m)
    modele = NF.ModeleTypes(etat, file_assoc)
    ch = generer(etat, modele, file_assoc, passes, chemins, motifs, args.iterations)
    temoin = None
    if args.temoin:
        temoin = generer(cibles_fictives(etat), modele, file_assoc, passes, chemins, motifs, args.iterations)
    n, extra = ecrire(ch, Path(args.sortie), args.avec_non_controles)
    texte = '\n'.join(rapport(ch, len(etat.sans_nom), temoin=temoin)) + '\n'
    if args.rapport:
        Path(args.rapport).parent.mkdir(parents=True, exist_ok=True)
        Path(args.rapport).write_text(texte, encoding='utf-8')
    print(texte)
    print('{} noms ecrits dans {}{}.'.format(n, args.sortie, ' (+{} sans controle)'.format(extra) if extra else ''))
    print('Pour les appliquer : python cotw\\renommer.py ajouter PROJET --noms {} [--simulation]'.format(args.sortie))
    return 0


def _file_assoc_hp() -> List[dict]:
    """file_assoc de resources/deca/gameinfo/hp.json, sans ouvrir de projet."""
    import json
    hp = Path(__file__).resolve().parents[1] / 'resources' / 'deca' / 'gameinfo' / 'hp.json'
    data = json.loads(hp.read_text(encoding='utf-8'))
    return [{k.encode('ascii'): v.split(',') for k, v in g.items()} for g in data.get('file_assoc', [])]


if __name__ == '__main__':
    sys.exit(main())
