#!/usr/bin/env python3
"""
population.py -- lecture des populations d'animaux d'une sauvegarde (animal_population_N), sans rien modifier,
et comparaison de deux instantanes : animaux retires, animaux apparus, file de repopulation.

Format (journal Great Ones, 2026-11-12, releve sur les sauvegardes du PC utilisateur) : meme conteneur que le
profil (sauvegarde.py : SAVE, COMP, zlib, 01 01 00 00 00, ADF qui porte ses types). Instance
AnimalPopulationReserveData_9 :
  ReserveSeed, AtypicalsCooldownTime, HuntingPressureMap (65 536 octets), Populations[] ;
  population : NameHashId = hash32("<espece>_population"), Groups[], RepopulateData[] { Gender, Weight, Score },
    InitialTotalScore, InitialNumAnimals, Revision, RepopulationSkipCounter, ConditionsFulfilled[] ;
  groupe : SpawnAreadId, Id, NeedZonePathGuids[], Animals[] ;
  animal : Gender (1 male, 2 femelle), Weight, Score, FeatureModifiers.Flags, IsScripted, VisualVariationSeed, Id,
    MapPosition. Les anciennes versions ont un champ IsGreatOne a la place de FeatureModifiers.

Ce que montre la comparaison de deux instantanes [teste : t0 / t1 / t2 du 2026-11-12] :
  - un animal preleve est retire de son groupe ; ses Gender, Weight et Score sont recopies dans RepopulateData ;
  - plus tard, une entree de RepopulateData est consommee et un animal neuf apparait, dans un autre groupe, avec un
    autre poids, un autre score et une autre graine : c'est a ce moment que se decide un Great One.
Les animaux n'ont pas d'identifiant stable (Id vaut 0) : ils sont apparies par leur contenu
(sexe, poids, score, graine, drapeaux).

FeatureModifiers.Flags : bit 0 = Great One [teste : sauvegarde du 2026-11-12, deux Great Ones crees en jeu relus avec
Flags = 1 et des poids de la plage Great One], bit 1 = atypique [code : pose par le jeu dans l'enregistrement en memoire].
La file (RepopulateData) ne garde pas le drapeau d'eligibilite au Great One que le jeu tient en memoire : une entree
relue depuis la sauvegarde n'est pas eligible tant qu'un lot avorte ne l'a pas remise en file.

Usage, depuis la racine du depot :
    python cotw\\population.py FICHIER                      resume par espece
    python cotw\\population.py AVANT APRES [SUIVANT ...]    resume du premier, puis comparaisons successives
    python cotw\\population.py FICHIER --animaux whitetail  liste des animaux d'une espece
"""
import argparse
import collections
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402  (installe aussi le substitut de zugbruecke hors Windows)
import sauvegarde as SV  # noqa: E402

RESERVE_TYPE = 'AnimalPopulationReserveData'      # prefixe du nom de type (_9 en 2026-11)
FLAG_GREAT_ONE, FLAG_ATYPIQUE = 1, 2
GENRES = {1: 'M', 2: 'F'}

# Noms courts des especes : NameHashId = hash32(nom + "_population"). Liste ouverte : un hash que rien ne nomme
# est affiche en hexadecimal.
ESPECES = (
    'whitetail', 'blacktail', 'moose', 'elk', 'roosevelt_elk', 'coyote', 'black_bear', 'jackrabbit', 'mallard',
    'wild_turkey', 'fallow', 'fallow_deer', 'roe', 'roe_deer', 'red_deer', 'red_fox', 'fox', 'wild_boar', 'boar',
    'canada_goose', 'eu_bison', 'european_bison', 'bison', 'eu_rabbit', 'pheasant', 'brown_bear', 'gray_wolf',
    'reindeer', 'musk_deer', 'lynx', 'eurasian_lynx', 'western_capercaillie', 'eurasian_wigeon', 'blue_wildebeest',
    'gemsbok', 'lesser_kudu', 'lion', 'scrub_hare', 'sidestriped_jackal', 'springbok', 'warthog', 'cape_buffalo',
    'water_buffalo', 'puma', 'mule_deer', 'axis_deer', 'blackbuck', 'cinnamon_teal', 'caribou', 'grizzly_bear',
    'plains_bison', 'bighorn_sheep', 'mountain_goat', 'pronghorn', 'mountain_lion', 'harlequin_duck',
    'iberian_wolf', 'iberian_mouflon', 'ibex', 'beceite_ibex', 'gredos_ibex', 'ronda_ibex', 'southeastern_ibex',
    'eu_hare', 'european_hare', 'chamois', 'feral_pig', 'feral_goat', 'sika_deer', 'turkey', 'fallow_deer',
    'jaguar', 'taruca', 'tahr', 'collared_peccary', 'bobcat', 'green_wing_teal', 'antelope_jackrabbit',
    'bobwhite_quail', 'rio_grande_turkey', 'american_alligator', 'wild_hog', 'gray_fox', 'raccoon',
    'eastern_cottontail_rabbit', 'eastern_wild_turkey', 'mexican_bobcat', 'rockymountain_elk', 'saltwater_crocodile',
    'banteng', 'sambar', 'hog_deer', 'magpie_goose', 'stubble_quail', 'eastern_gray_kangaroo', 'javan_rusa',
    'red_kangaroo', 'tundra_bean_goose', 'greylag_goose', 'goldeneye', 'tufted_duck', 'black_grouse',
    'hazel_grouse', 'rock_ptarmigan', 'willow_ptarmigan', 'mountain_hare', 'raccoon_dog', 'bengal_tiger',
    'nilgai', 'barasingha', 'woolly_hare', 'blue_sheep', 'himalayan_tahr', 'snow_leopard', 'wild_yak',
)


class EPopulation(Exception):
    """Le fichier n'est pas une population lisible."""


@dataclass(frozen=True)
class Animal:
    genre: int
    poids: float
    score: float
    drapeaux: int                 # FeatureModifiers.Flags, ou 1 si IsGreatOne (anciennes versions)
    scripte: int
    graine: int
    id: int
    groupe: int                   # rang du groupe dans la population
    rang: int                     # rang de l'animal dans le groupe

    @property
    def great_one(self) -> bool:
        return bool(self.drapeaux & FLAG_GREAT_ONE)

    def cle(self) -> Tuple:
        """Identite d'un animal entre deux instantanes : il n'a pas d'identifiant stable."""
        return self.genre, self.poids, self.score, self.graine, self.drapeaux, self.scripte

    def texte(self) -> str:
        marques = ''.join(m for bit, m in ((FLAG_GREAT_ONE, ' GREAT ONE'), (FLAG_ATYPIQUE, ' atypique'))
                          if self.drapeaux & bit)
        return '{} poids {:8.3f} score {:8.3f} graine {:10d} groupe {:3d}{}'.format(
            GENRES.get(self.genre, '?{}'.format(self.genre)), self.poids, self.score, self.graine, self.groupe, marques)


@dataclass
class Population:
    hash: int
    nom: str
    animaux: List[Animal]
    groupes: List[Tuple[int, int, int]]            # (Id, SpawnAreadId, nombre d'animaux)
    file: List[Tuple[int, float, float]]           # RepopulateData : (Gender, Weight, Score)
    compteurs: Dict[str, object] = field(default_factory=dict)

    def nombre(self, genre: Optional[int] = None) -> int:
        return sum(1 for a in self.animaux if genre is None or a.genre == genre)


@dataclass
class Reserve:
    chemin: str
    type: str
    graine: Optional[int]
    recharge_atypiques: Optional[float]
    pression: Tuple[int, int]                      # (cases non nulles, maximum) de HuntingPressureMap
    populations: List[Population]
    remarques: List[str] = field(default_factory=list)


def _simple(v):
    """Valeur ADF de DECA -> dict / liste / scalaire."""
    x = getattr(v, 'value', v)
    if isinstance(x, dict):
        return {k: _simple(e) for k, e in x.items()}
    if isinstance(x, (list, tuple)):
        return [_simple(e) for e in x]
    if hasattr(x, 'tolist'):                       # scalaires et tableaux numpy
        return x.tolist()
    return x


def noms_especes() -> Dict[int, str]:
    from deca.hashes import hash32_func
    return {int(hash32_func(n + '_population')): n for n in ESPECES}


def lire(chemin) -> Reserve:
    data = Path(chemin).read_bytes()
    try:
        c = SV.decoder(data)
        adf = SV.lire_adf(c.charge)
    except SV.ESauvegarde as e:
        raise EPopulation(str(e))
    racine = None
    for ins, valeur in zip(adf.table_instance, adf.table_instance_full_values):
        if SV.type_name(adf, ins.type_hash).startswith(RESERVE_TYPE):
            racine, type_nom = _simple(valeur), SV.type_name(adf, ins.type_hash)
            break
    if racine is None:
        raise EPopulation('pas d\'instance {} dans {}'.format(RESERVE_TYPE, chemin))
    noms = noms_especes()
    pops = []
    for p in racine.get('Populations', []):
        h = p['NameHashId'] & 0xffffffff
        animaux, groupes = [], []
        for gi, g in enumerate(p.get('Groups', [])):
            groupes.append((g.get('Id', 0), g.get('SpawnAreadId', 0), len(g.get('Animals', []))))
            for ai, a in enumerate(g['Animals']):
                if 'FeatureModifiers' in a:
                    drapeaux = a['FeatureModifiers'].get('Flags', 0)
                else:
                    drapeaux = FLAG_GREAT_ONE if a.get('IsGreatOne') else 0
                animaux.append(Animal(a['Gender'], a['Weight'], a['Score'], drapeaux, a.get('IsScripted', 0),
                                      a.get('VisualVariationSeed', 0), a.get('Id', 0), gi, ai))
        compteurs = {k: p[k] for k in ('InitialTotalScore', 'InitialNumAnimals', 'Revision',
                                       'RepopulationSkipCounter', 'ConditionsFulfilled') if k in p}
        pops.append(Population(h, noms.get(h, '0x{:08x}'.format(h)), animaux, groupes,
                               [(e['Gender'], e['Weight'], e['Score']) for e in p.get('RepopulateData', [])],
                               compteurs))
    carte = bytes(racine.get('HuntingPressureMap') or b'')
    return Reserve(str(chemin), type_nom, racine.get('ReserveSeed'), racine.get('AtypicalsCooldownTime'),
                   (sum(1 for x in carte if x), max(carte) if carte else 0), pops, list(c.remarques))


def resume(r: Reserve) -> List[str]:
    L = ['Population : {}'.format(r.chemin),
         '  {} ; ReserveSeed {} ; AtypicalsCooldownTime {} ; pression de chasse : {} cases, maximum {}'.format(
             r.type, r.graine, r.recharge_atypiques, *r.pression),
         '  {:<22} {:>7} {:>7} {:>5} {:>5} {:>3} {:>4}  {}'.format('espece', 'groupes', 'animaux', 'M', 'F', 'GO',
                                                                    'file', 'compteurs')]
    for p in r.populations:
        cpt = ' '.join('{}={}'.format(k, round(v, 3) if isinstance(v, float) else v)
                       for k, v in p.compteurs.items())
        L.append('  {:<22} {:>7} {:>7} {:>5} {:>5} {:>3} {:>4}  {}'.format(
            p.nom, len(p.groupes), p.nombre(), p.nombre(1), p.nombre(2), sum(1 for a in p.animaux if a.great_one),
            len(p.file), cpt))
    return L


def _file_texte(e: Tuple[int, float, float]) -> str:
    return '{} poids {:8.3f} score {:8.3f}'.format(GENRES.get(e[0], '?{}'.format(e[0])), e[1], e[2])


def comparer(a: Reserve, b: Reserve) -> List[str]:
    """Differences de b par rapport a a, population par population."""
    L = ['Comparaison : {} -> {}'.format(a.chemin, b.chemin)]
    if (a.graine, a.recharge_atypiques) != (b.graine, b.recharge_atypiques):
        L.append('  ReserveSeed {} -> {} ; AtypicalsCooldownTime {} -> {}'.format(
            a.graine, b.graine, a.recharge_atypiques, b.recharge_atypiques))
    if a.pression != b.pression:
        L.append('  pression de chasse : {} cases (max {}) -> {} cases (max {})'.format(*a.pression, *b.pression))
    avant = {p.hash: p for p in a.populations}
    rien = True
    for pb in b.populations:
        pa = avant.pop(pb.hash, None)
        if pa is None:
            L.append('  {} : population nouvelle ({} animaux)'.format(pb.nom, pb.nombre()))
            rien = False
            continue
        ca = collections.Counter(x.cle() for x in pa.animaux)
        cb = collections.Counter(x.cle() for x in pb.animaux)
        retires = [x for x in pa.animaux if ca[x.cle()] > cb.get(x.cle(), 0)]
        apparus = [x for x in pb.animaux if cb[x.cle()] > ca.get(x.cle(), 0)]
        fa, fb = collections.Counter(pa.file), collections.Counter(pb.file)
        consommees = list((fa - fb).elements())
        ajoutees = list((fb - fa).elements())
        cpt = ['{} {} -> {}'.format(k, pa.compteurs.get(k), v) for k, v in pb.compteurs.items()
               if pa.compteurs.get(k) != v]
        if not (retires or apparus or consommees or ajoutees or cpt):
            continue
        rien = False
        L.append('  {} : {} -> {} animaux ; file {} -> {}'.format(pb.nom, pa.nombre(), pb.nombre(), len(pa.file),
                                                                  len(pb.file)))
        L += ['    retire   ' + x.texte() for x in retires]
        L += ['    apparu   ' + x.texte() for x in apparus]
        L += ['    file +   ' + _file_texte(e) for e in ajoutees]
        L += ['    file -   ' + _file_texte(e) for e in consommees]
        L += ['    ' + c for c in cpt]
    for pa in avant.values():
        L.append('  {} : population disparue'.format(pa.nom))
        rien = False
    if rien:
        L.append('  aucune difference dans les populations')
    return L


def animaux(r: Reserve, espece: str) -> List[str]:
    L = []
    for p in r.populations:
        if espece in (p.nom, '0x{:08x}'.format(p.hash)):
            L.append('{} : {} animaux'.format(p.nom, p.nombre()))
            L += ['  ' + x.texte() for x in sorted(p.animaux, key=lambda x: -x.score)]
    return L or ['espece {} absente de {}'.format(espece, r.chemin)]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description='Populations d\'animaux d\'une sauvegarde de COTW (rien n\'est modifie).')
    ap.add_argument('fichiers', nargs='+', help='animal_population_N ; plusieurs : comparaisons successives')
    ap.add_argument('--animaux', metavar='ESPECE', help='lister les animaux d\'une espece (nom court ou 0x...)')
    ap.add_argument('--out', help='ecrire le rapport dans ce fichier')
    args = ap.parse_args(argv)
    try:
        reserves = [lire(f) for f in args.fichiers]
    except (EPopulation, OSError) as e:
        print('erreur : {}'.format(e), file=sys.stderr)
        return 1
    L = resume(reserves[0])
    if args.animaux:
        L += [''] + animaux(reserves[0], args.animaux)
    for a, b in zip(reserves, reserves[1:]):
        L += [''] + comparer(a, b)
    text = '\n'.join(L) + '\n'
    if args.out:
        Path(args.out).write_text(text, encoding='utf-8')
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
