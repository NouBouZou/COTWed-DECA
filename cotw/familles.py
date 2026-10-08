#!/usr/bin/env python3
"""
familles.py -- famille de fichiers d'un chemin, pour le nommage interne par famille.

Pourquoi : un nom de propriete RTPC se cherche bien mieux parmi les seuls hashes inconnus des fichiers ou il
apparait, avec le vocabulaire de ces fichiers. Sur COTW, les 195 hashes inconnus des trophy_feature_data_*.bin
portent 53 % des occurrences de proprietes encore inconnues ; contre si peu de cibles, des triplets de mots sont
sans risque (0,13 coincidence attendue pour 2,8 millions de candidats), alors qu'ils sont inutilisables contre
les 80 000 hashes de toutes les familles.

Regle (audit_noms_internes.py et resoudre_hashes.py l'appliquent tous deux) :
  - fichier sans nom : "(sans nom)" ;
  - extension ".bin" ou pas d'extension : le nom du fichier, chiffres remplaces par "#" et suffixe _male/_female
    retire (trophy_feature_data_male.bin -> trophy_feature_data.bin, reserve_13.bin -> reserve_#.bin) : les .bin de
    COTW sont des fichiers de reglages tous differents ;
  - sinon la derniere extension (".asb", ".blo", ".epe") : un format = un jeu de noms.
"""
import re
from typing import Iterable, List

SANS_NOM = '(sans nom)'
EXTENSIONS_PAR_NOM = {'.bin', ''}

_RX_CHIFFRES = re.compile(r'\d+')
_RX_SEXE = re.compile(r'_(?:male|female)$')
_RX_CAMEL = re.compile(r'([a-z0-9])([A-Z])')
_RX_SEPARATEURS = re.compile(r'[^A-Za-z0-9]+')


def famille(chemin: str) -> str:
    if not chemin or chemin.startswith(SANS_NOM):
        return SANS_NOM
    base = chemin.replace('\\', '/').rsplit('/', 1)[-1].lower()
    ext = '.' + base.rsplit('.', 1)[1] if '.' in base.lstrip('.') else ''
    if ext not in EXTENSIONS_PAR_NOM:
        return ext
    stem = base[:-len(ext)] if ext else base
    stem = _RX_SEXE.sub('', stem)
    return _RX_CHIFFRES.sub('#', stem) + ext


def mots(nom: str) -> List[str]:
    """Mots d'un nom : snake_case, camelCase et separateurs ; minuscules, sans les nombres seuls.
    CircumferenceBurr -> [circumference, burr] ; frame_12 -> [frame]."""
    texte = _RX_CAMEL.sub(r'\1_\2', nom)
    return [m.lower() for m in _RX_SEPARATEURS.split(texte) if m and not m.isdigit() and len(m) >= 2]


def mots_de(noms: Iterable[str]) -> List[str]:
    vus, out = set(), []
    for n in noms:
        for m in mots(n):
            if m not in vus:
                vus.add(m)
                out.append(m)
    return out
