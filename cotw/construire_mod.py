#!/usr/bin/env python3
"""
construire_mod.py -- reconstruit le dropzone d'un mod (fichier .cotwmod.json) depuis les originaux du jeu.

Un mod est une liste de munitions a ajouter (memes reglages que cloner_munition.py) et de changements d'objets
existants (prix temoin, champs). Chaque construction repart des fichiers d'origine du jeu et refait tout en une
passe : pas d'option --base a enchainer, pas d'ordre de commandes a respecter (essai test14 : une commande qui
n'avait rien produit a fait partir la suivante des originaux). Le resultat est reproductible : memes octets a
chaque construction.

Fichier de mod (JSON, cles en anglais pour pouvoir le partager) :
    {
      "format": "cotwed-mod", "version": 1,
      "name": "test14", "notes": "texte libre",
      "witness_prices": {"equipment_ammo_270_sp_01": 5432},
      "changes": [{"item": "equipment_ammo_243_pt_01", "field": "locked", "value": "0"}],
      "ammo": [
        {"source": "equipment_ammo_243_sp_01", "name": "equipment_ammo_243_test_03",
         "price": 88, "label": null, "weapons": null,
         "entity": "own",
         "stats_from": "equipment_ammo_243_sp_01",
         "stats": {"Penetration": "17", "Expansion": "20", "Class": "3;4;5;6"},
         "display_name": {"all": "BALLES DE TEST 88 .243", "en": ".243 TEST 88 BULLET"},
         "description": {"all": "Munition de test."},
         "fields": {"locked": "3"},
         "ballistics": {"projectile_penetration": 0.08, "tracer_settings.enabled": 1},
         "classes": [3, 4, 5, 6],
         "icon": "icones/ma_munition.png"}
      ]
    }
  - entity : "own" (entite propre, recommande), "source" (celle de la source : l'arme charge alors la source),
    "copy" (diagnostic : copie a l'octet pres) ; "entity_path" remplace le chemin avec "source".
  - weapons : null = armes de la source ; sinon la liste complete des armes compatibles.
  - stats_from : null = pas de ligne de stats (ni stats affichees ni logo de possession) ; "stats" donne des
    valeurs par colonne (numero, en-tete, ou morceau unique d'en-tete) ; "stats_own_cells" : cellules propres.
  - display_name, description : "all" pour toutes les langues, puis un code de langue (en, fr, de, es, ru, pl,
    ja, pt, cs, zh) pour celles qui different.
  - fields : champs de la nouvelle munition (nom ou hash 0x...), "allow_unknown_weapons" : armes hors categorie.
  - ballistics, classes : balistique propre (avec "entity": "own") : champs du .ammotunec de la nouvelle entite
    (chemin ou fin de chemin unique, voir ammotune.py) et classes d'animaux ([3, 4, 5, 6] ou "3;4;5;6") ; null ou
    absent : valeurs de la source.
  - icon : image (PNG...) dont fabriquer l'icone propre (magasin, inventaire), chemin relatif au fichier de mod ;
    icon_from : objet dont copier l'icone. Absents : icone de la source (voir cloner_munition.py, --icone).

Sortie : SORTIE\\dropzone (a copier dans le dossier dropzone du jeu), SORTIE\\rapport_mod.txt, et
SORTIE\\cotwmod_manifest.json, la liste des fichiers ecrits avec leur empreinte. A la construction suivante :
  - un fichier de la liste, inchange depuis, est remplace sans demander ;
  - un fichier de la liste que le mod ne produit plus (munition retiree) est supprime ;
  - tout autre fichier deja present est refuse, sauf avec --ecraser ; un fichier de la liste modifie a la main
    depuis compte comme un autre fichier (refuse, et jamais supprime).

Usage, depuis la racine du depot, environnement virtuel active, DECA ferme :
    python cotw\\construire_mod.py D:\\dev\\work\\hp cotw\\mods\\test14.cotwmod.json --sortie D:\\dev\\mods\\test14

--comparer DOSSIER : compare chaque fichier construit a celui de meme chemin dans DOSSIER (un dropzone deja
deploye, par exemple celui d'un essai fait avec cloner_munition). string_ids est compare aussi par ses textes :
un mod construit en une passe donne un fichier plus petit que plusieurs commandes enchainees, aux memes textes.
"""
import argparse
import dataclasses
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _projet  # noqa: E402  (installe aussi le substitut de zugbruecke hors Windows)
import cloner_munition as CM  # noqa: E402

FORMAT = 'cotwed-mod'
VERSION = 1
SUFFIX = '.cotwmod.json'
MANIFEST = 'cotwmod_manifest.json'
RAPPORT = 'rapport_mod.txt'
LANGUES = ['en', 'fr', 'de', 'es', 'ru', 'pl', 'ja', 'pt', 'cs', 'zh']   # stringlookup.LANGUES
ALL = 'all'

ENTITY_TO_TOOL = {'own': CM.ENTITE_PROPRE, 'source': CM.ENTITE_SOURCE, 'copy': CM.ENTITE_COPIE}
ENTITY_FROM_TOOL = {v: k for k, v in ENTITY_TO_TOOL.items()}
AMMO_KEYS = {'source', 'name', 'label', 'price', 'weapons', 'entity', 'entity_path', 'stats_from', 'stats',
             'stats_own_cells', 'display_name', 'description', 'fields', 'allow_unknown_weapons', 'ballistics', 'classes',
             'icon', 'icon_from'}
TOP_KEYS = {'format', 'version', 'name', 'notes', 'witness_prices', 'changes', 'ammo'}


class EMod(Exception):
    """Fichier de mod invalide, ou sortie refusee : le message explique pourquoi."""


@dataclass
class ModFile:
    """Contenu d'un fichier .cotwmod.json : le mod et ses informations libres."""
    mod: CM.Mod
    name: str = ''
    notes: str = ''
    dossier: Optional[Path] = None   # dossier du fichier de mod : base des chemins relatifs ("icon")


# ---------------------------------------------------------------------------
# Textes par langue : dictionnaire JSON <-> options [LANGUE=]TEXTE de cloner_munition
# ---------------------------------------------------------------------------

def _ambiguous(text: str) -> bool:
    """Un texte "pour toutes les langues" qui commence par "fr=" serait lu comme le texte francais."""
    code, sep, _ = text.partition('=')
    return bool(sep) and code.strip().lower() in LANGUES


def texts_to_specs(texts: Optional[Dict[str, str]]) -> List[str]:
    if not texts:
        return []
    unknown = sorted(set(texts) - set(LANGUES) - {ALL})
    if unknown:
        raise EMod('langue(s) inconnue(s) : {} ; langues : {}, ou "{}"'.format(', '.join(unknown), ', '.join(LANGUES), ALL))
    default = texts.get(ALL)
    if default is not None and _ambiguous(default):
        # toutes les langues ecrites explicitement : plus d'ambiguite possible
        return ['{}={}'.format(code, texts.get(code, default)) for code in LANGUES]
    specs = [default] if default is not None else []
    return specs + ['{}={}'.format(code, texts[code]) for code in LANGUES if code in texts]


def specs_to_texts(specs: List[str]) -> Dict[str, str]:
    out = {}
    for s in specs:
        code, sep, rest = s.partition('=')
        if sep and code.strip().lower() in LANGUES:
            out[code.strip().lower()] = rest
        else:
            out[ALL] = s
    return out


def _field_specs(fields: Optional[Dict[str, object]]) -> List[str]:
    return ['{}={}'.format(k, v) for k, v in (fields or {}).items()]


# ---------------------------------------------------------------------------
# Lecture et ecriture du fichier de mod
# ---------------------------------------------------------------------------

def _require(cond: bool, message: str):
    if not cond:
        raise EMod(message)


def _str_or_none(d: dict, key: str, where: str) -> Optional[str]:
    v = d.get(key)
    _require(v is None or isinstance(v, str), '{} : "{}" doit etre un texte'.format(where, key))
    return v


def munition_from_dict(d: dict, index: int) -> CM.Munition:
    where = 'ammo[{}]'.format(index)
    _require(isinstance(d, dict), '{} : objet attendu'.format(where))
    extra = sorted(set(d) - AMMO_KEYS)
    _require(not extra, '{} : cle(s) inconnue(s) : {}'.format(where, ', '.join(extra)))
    for key in ('source', 'name'):
        _require(isinstance(d.get(key), str) and d[key], '{} : "{}" obligatoire'.format(where, key))
    price = d.get('price')
    _require(price is None or (isinstance(price, int) and not isinstance(price, bool) and price >= 0),
             '{} : "price" doit etre un entier positif'.format(where))
    weapons = d.get('weapons')
    _require(weapons is None or (isinstance(weapons, list) and all(isinstance(w, str) for w in weapons)),
             '{} : "weapons" doit etre null ou une liste de noms'.format(where))
    entity = d.get('entity', 'own')
    _require(entity in ENTITY_TO_TOOL, '{} : "entity" vaut {}'.format(where, ', '.join(ENTITY_TO_TOOL)))
    for key in ('stats', 'fields', 'display_name', 'description', 'ballistics'):
        _require(d.get(key) is None or isinstance(d.get(key), dict), '{} : "{}" doit etre un objet'.format(where, key))
    ballistics = d.get('ballistics') or {}
    _require(all(isinstance(v, (int, float, str)) and not isinstance(v, bool) for v in ballistics.values()),
             '{} : "ballistics" : valeurs nombres ou textes attendues'.format(where))
    classes = None
    if d.get('classes') is not None:
        import ammotune as AT
        try:
            classes = AT.parse_classes(d['classes'])
        except AT.EAmmotune as e:
            raise EMod('{} : {}'.format(where, e))
    return CM.Munition(
        source=d['source'], nom=d['name'], libelle=_str_or_none(d, 'label', where), prix=price,
        armes=list(weapons) if weapons else None, entite=ENTITY_TO_TOOL[entity],
        entite_chemin=_str_or_none(d, 'entity_path', where), stats_de=_str_or_none(d, 'stats_from', where),
        stats={str(k): str(v) for k, v in (d.get('stats') or {}).items()},
        stats_cellules_propres=bool(d.get('stats_own_cells', False)),
        nom_affiche=texts_to_specs(d.get('display_name')), description=texts_to_specs(d.get('description')),
        champs=_field_specs(d.get('fields')), forcer=bool(d.get('allow_unknown_weapons', False)),
        balistique={str(k): str(v) for k, v in ballistics.items()}, classes=classes,
        icone=_str_or_none(d, 'icon', where), icone_de=_str_or_none(d, 'icon_from', where))


def munition_to_dict(m: CM.Munition) -> dict:
    d = {'source': m.source, 'name': m.nom, 'price': m.prix, 'label': m.libelle, 'weapons': m.armes,
         'entity': ENTITY_FROM_TOOL[m.entite], 'stats_from': m.stats_de, 'stats': dict(m.stats),
         'display_name': specs_to_texts(m.nom_affiche), 'description': specs_to_texts(m.description),
         'fields': dict(s.split('=', 1) for s in m.champs)}
    if m.entite_chemin:
        d['entity_path'] = m.entite_chemin
    if m.stats_cellules_propres:
        d['stats_own_cells'] = True
    if m.forcer:
        d['allow_unknown_weapons'] = True
    if m.balistique:
        d['ballistics'] = {k: _number(v) for k, v in m.balistique.items()}
    if m.classes is not None:
        d['classes'] = list(m.classes)
    if m.icone:
        d['icon'] = m.icone
    if m.icone_de:
        d['icon_from'] = m.icone_de
    return d


def _number(text: str):
    """Valeur de balistique ecrite comme nombre JSON si elle en est un (lecture plus simple du fichier)."""
    for conv in (int, float):
        try:
            return conv(text)
        except ValueError:
            pass
    return text


def from_dict(d: dict, dossier=None) -> ModFile:
    """``dossier`` : dossier du fichier de mod, base des chemins relatifs (icon) ; None pour un mod pas enregistre."""
    _require(isinstance(d, dict), 'fichier de mod : objet JSON attendu')
    _require(d.get('format') == FORMAT, 'pas un fichier de mod COTWed DECA ("format": "{}" attendu)'.format(FORMAT))
    _require(d.get('version') == VERSION, 'version {} non prise en charge (attendu : {})'.format(d.get('version'), VERSION))
    extra = sorted(set(d) - TOP_KEYS)
    _require(not extra, 'cle(s) inconnue(s) : {}'.format(', '.join(extra)))
    witness = d.get('witness_prices') or {}
    _require(isinstance(witness, dict) and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
                                               for v in witness.values()),
             '"witness_prices" : {"objet": prix entier, ...} attendu')
    changes = d.get('changes') or []
    _require(isinstance(changes, list) and all(isinstance(c, dict) and {'item', 'field', 'value'} <= set(c)
                                               for c in changes),
             '"changes" : liste de {"item", "field", "value"} attendue')
    ammo = d.get('ammo') or []
    _require(isinstance(ammo, list), '"ammo" : liste attendue')
    mod = CM.Mod([munition_from_dict(a, i) for i, a in enumerate(ammo)],
                 controle_prix=['{}={}'.format(k, v) for k, v in witness.items()],
                 modifier=['{}:{}={}'.format(c['item'], c['field'], c['value']) for c in changes])
    return ModFile(mod, name=str(d.get('name') or ''), notes=str(d.get('notes') or ''),
                   dossier=Path(dossier) if dossier is not None else None)


def to_dict(mf: ModFile) -> dict:
    witness = {}
    for spec in mf.mod.controle_prix:
        name, _, value = spec.partition('=')
        witness[name] = int(value)
    changes = []
    for spec in mf.mod.modifier:
        item, fld, value = CM.parse_field_spec(spec, with_item=True)
        changes.append({'item': item, 'field': fld, 'value': value})
    return {'format': FORMAT, 'version': VERSION, 'name': mf.name, 'notes': mf.notes,
            'witness_prices': witness, 'changes': changes,
            'ammo': [munition_to_dict(m) for m in mf.mod.munitions]}


def load(path) -> ModFile:
    try:
        d = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise EMod('{} : lecture impossible ({})'.format(path, e))
    try:
        return from_dict(d, Path(path).expanduser().resolve().parent)
    except EMod as e:
        raise EMod('{} : {}'.format(path, e))


def save(mf: ModFile, path) -> None:
    Path(path).write_text(json.dumps(to_dict(mf), indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


# ---------------------------------------------------------------------------
# Sortie : dropzone, manifeste, rapport
# ---------------------------------------------------------------------------

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_manifest(sortie: Path) -> Dict[str, str]:
    """{chemin virtuel: empreinte} des fichiers ecrits par la construction precedente ({} sans manifeste)."""
    path = sortie / MANIFEST
    if not path.is_file():
        return {}
    try:
        d = json.loads(path.read_text(encoding='utf-8'))
        return {str(k): str(v) for k, v in d.get('files', {}).items()}
    except (OSError, ValueError, AttributeError):
        raise EMod('{} illisible : supprime-le si ce dossier ne contient que des fichiers produits par COTWed DECA'.format(path))


def _ours(root: Path, v_path: str, manifest: Dict[str, str]) -> bool:
    """Fichier ecrit par la construction precedente et pas modifie depuis."""
    target = root / Path(v_path)
    return v_path in manifest and target.is_file() and sha256(target.read_bytes()) == manifest[v_path]


@dataclass
class PlanSortie:
    a_ecrire: List[Tuple[str, bytes]]
    a_supprimer: List[str] = field(default_factory=list)   # fichiers de la construction precedente plus produits
    etrangers: List[str] = field(default_factory=list)     # deja presents, pas a nous : refus sans --ecraser
    gardes: List[str] = field(default_factory=list)        # de la liste precedente, modifies depuis : jamais supprimes


def plan_sortie(result: CM.Resultat, sortie: Path) -> PlanSortie:
    root = sortie / 'dropzone'
    manifest = read_manifest(sortie)
    produced = set(result.chemins())
    plan = PlanSortie(list(result.fichiers))
    for v_path in result.chemins():
        if (root / Path(v_path)).exists() and not _ours(root, v_path, manifest):
            plan.etrangers.append(v_path)
    for v_path in sorted(set(manifest) - produced):
        if not (root / Path(v_path)).exists():
            continue
        (plan.a_supprimer if _ours(root, v_path, manifest) else plan.gardes).append(v_path)
    return plan


def ecrire(result: CM.Resultat, sortie: Path, ecraser: bool, nom_mod: str = '') -> List[str]:
    """Ecrit le dropzone, le manifeste et le rapport ; renvoie les lignes ajoutees au rapport."""
    sortie = Path(sortie).expanduser().resolve()
    root = sortie / 'dropzone'
    plan = plan_sortie(result, sortie)
    if plan.etrangers and not ecraser:
        raise EMod('fichiers deja presents dans {} et qui ne viennent pas de la construction precedente '
                   '(option --ecraser pour les remplacer) : {}'.format(root, ', '.join(plan.etrangers)))
    lines = ['']
    for v_path in plan.a_supprimer:
        (root / Path(v_path)).unlink()
        lines.append('Supprime (plus produit par le mod) : {}'.format(root / Path(v_path)))
    for v_path in plan.gardes:
        lines.append('Garde (produit par une construction precedente, mais modifie depuis) : {}'.format(root / Path(v_path)))
    lines += CM.ecrire_fichiers(result.fichiers, root)
    manifest = {'format': FORMAT + '-manifest', 'version': VERSION, 'mod': nom_mod,
                'files': {v: sha256(data) for v, data in result.fichiers}}
    (sortie / MANIFEST).write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    lines.append('Manifeste : {}'.format(sortie / MANIFEST))
    lines.append('A copier : le CONTENU de {} dans le dossier dropzone du jeu.'.format(root))
    (sortie / RAPPORT).write_text('\n'.join(result.rapport + lines) + '\n', encoding='utf-8')
    return lines


def comparer(result: CM.Resultat, dossier: Path) -> List[str]:
    """Compare les fichiers construits a ceux de ``dossier`` (format dropzone) ; renvoie les lignes du rapport."""
    import stringlookup as SL
    dossier = Path(dossier).expanduser().resolve()
    lines = ['', 'Comparaison avec {} :'.format(dossier)]
    for v_path, data in result.fichiers:
        other = dossier / Path(v_path)
        if not other.is_file():
            lines.append('  absent      : {}'.format(v_path))
            continue
        old = other.read_bytes()
        if old == data:
            lines.append('  identique   : {}'.format(v_path))
            continue
        line = '  different   : {} ({} octets ici, {} la-bas)'.format(v_path, len(data), len(old))
        if v_path == SL.STRING_IDS:
            try:
                a, b = SL.Vue(data), SL.Vue(old)
                same = a.n_lang == b.n_lang and all(
                    sorted((h, a.text(t)) for h, t in a.pairs(i)) == sorted((h, b.text(t)) for h, t in b.pairs(i))
                    for i in range(a.n_lang))
                line += ' ; textes de toutes les cles, dans les {} langues : {}'.format(
                    a.n_lang, 'identiques' if same else 'DIFFERENTS')
            except SL.EStringLookup as e:
                line += ' ; textes non compares ({})'.format(e)
        lines.append(line)
    return lines


def resolved(mf: ModFile) -> CM.Mod:
    """Le mod avec ses chemins d'images absolus (relatifs au fichier de mod) ; ``mf`` n'est pas modifie (le fichier
    garde ses chemins relatifs, qui suivent le dossier du mod)."""
    munitions = []
    for m in mf.mod.munitions:
        if m.icone and not Path(m.icone).expanduser().is_absolute():
            if mf.dossier is None:
                raise EMod('{} : chemin d\'icone relatif "{}" et mod jamais enregistre : enregistre-le, ou donne un '
                           'chemin complet'.format(m.nom, m.icone))
            m = dataclasses.replace(m, icone=str(mf.dossier / Path(m.icone)))
        munitions.append(m)
    return dataclasses.replace(mf.mod, munitions=munitions)


def construire(vfs, mf: ModFile, etape: Optional[Callable[[str], None]] = None) -> CM.Resultat:
    """Construit le mod depuis les originaux du jeu (jamais depuis une base)."""
    result = CM.construire(vfs, resolved(mf), base=None, etape=etape)
    if mf.name:
        result.rapport[0:0] = ['Mod : {}'.format(mf.name)]
    return result


def command_line(project_dir: str, mod_path: str, sortie: str, ecraser: bool = False) -> str:
    """Commande equivalente (Invite de commandes Windows, depuis la racine du depot), pour le journal."""
    def q(s: str) -> str:
        s = str(s)
        return '"{}"'.format(s) if (' ' in s or not s) else s
    return 'python cotw\\construire_mod.py {} {} --sortie {}{}'.format(
        q(project_dir), q(mod_path), q(sortie), ' --ecraser' if ecraser else '')


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser(description='Reconstruit le dropzone d\'un mod depuis les originaux du jeu.')
    ap.add_argument('projet', help='projet DECA (dossier contenant db\\core.db)')
    ap.add_argument('mod', help='fichier de mod (.cotwmod.json)')
    ap.add_argument('--sortie', required=True, help='dossier de sortie ; les fichiers sont ecrits dans SORTIE\\dropzone')
    ap.add_argument('--ecraser', action='store_true',
                    help='remplacer des fichiers deja presents qui ne viennent pas de la construction precedente')
    ap.add_argument('--comparer', metavar='DOSSIER',
                    help='comparer les fichiers construits a ceux d\'un dropzone deja deploye (memes chemins)')
    args = ap.parse_args(argv)
    try:
        mf = load(args.mod)
        vfs = _projet.open_project(args.projet)
        result = construire(vfs, mf)
        lines = ecrire(result, Path(args.sortie), args.ecraser, mf.name)
        if args.comparer:
            lines += comparer(result, Path(args.comparer))
            (Path(args.sortie).expanduser().resolve() / RAPPORT).write_text(
                '\n'.join(result.rapport + lines) + '\n', encoding='utf-8')
    except (EMod, CM.EClone) as e:
        raise SystemExit('ERREUR : {}'.format(e))
    sys.stdout.write('\n'.join(result.rapport + lines) + '\n')
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
