"""
Tables de traduction de COTW (.stringlookup, ADF "StringLookup") : lecture, et ajout de textes sans rien ecraser.

Le jeu range les noms et descriptions affiches dans settings/hp_settings/string_ids.stringlookup (38,8 Mo, releve du
2026-10-03 ; ui/ui_localization.stringlookup a le meme format). Types, lus dans le fichier :
  StringLookup         { Languages : A[LanguageStringLookup], Text : A[int8] }
  LanguageStringLookup { SortedPairs : A[HashPropertiesPair], SortedDialogueLines : A[DialogueLine] }
  HashPropertiesPair   { Hash u32, TextOffset u32 }
  DialogueLine         { Hash u32, NameOffset u32, Subtitles : A[Subtitle], FMODEvent u32, IncomingCall StringHash,
                         CharacterName StringHash, Flags u32 }
  Subtitle             { LineHash u32, Start f32, Duration f32 }
Regles relevees sur le fichier du jeu :
  - Hash = hash32 (Jenkins) du texte de la cle, ex : hash32("equipment_ammo_243_sp_name") = 0x40911ad1 ;
  - dans chaque langue, les paires sont TRIEES par hash croissant (recherche par dichotomie, sans doute) ;
  - une ligne de dialogue par cle, parallele aux paires (meme hash, meme ordre), meme pour un nom d'objet :
    NameOffset designe la cle en clair (partagee par les langues), Subtitles un tableau d'un element propre a la
    langue (LineHash = hash de la cle) ;
  - Text : un bloc de chaines UTF-8 terminees par 0 ; TextOffset et NameOffset sont relatifs a son debut. Il est
    a la fin de l'instance : on l'allonge sur place.
  - 10 langues ; ordre deduit des textes de la .243 SP (voir LANGUES).

Ajouter une cle : son texte (et ceux de chaque langue) a la fin du bloc Text ; dans chaque langue, une paire et une
ligne de dialogue (copiee de celle d'une cle modele : hash, NameOffset, sous-titre propre) inserees a leur place
dans l'ordre des hash ; chaine de relocation tenue a jour par adf_ecriture.
"""
import io
import struct
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import adf_ecriture as AE

STRINGLOOKUP_TYPE = 0xc7254276
STRING_IDS = 'settings/hp_settings/string_ids.stringlookup'
# Ordre des langues, deduit des 10 textes de equipment_ammo_243_sp_name (".243 SOFT-POINT BULLET",
# "BALLES A TETE MOLLE .243", "TMG .243 WINCHESTER", ...).
LANGUES = ['en', 'fr', 'de', 'es', 'ru', 'pl', 'ja', 'pt', 'cs', 'zh']


class EStringLookup(Exception):
    pass


def key_hash(key: str) -> int:
    from deca.hashes import hash32_func
    return int(hash32_func(key.encode('utf-8')))


class Vue:
    """Lecture rapide d'un .stringlookup, sans lire les valeurs avec DECA : positions prises dans les en-tetes,
    membres et tailles d'apres les types du fichier."""

    def __init__(self, data: bytes):
        from deca.ff_adf import Adf
        from deca.file import ArchiveFile
        self.data = data
        base = AE.adf_base(data)
        adf = Adf()
        adf.deserialize(ArchiveFile(io.BytesIO(data[base:])), process_instances=False)
        if len(adf.table_instance) != 1 or adf.table_instance[0].type_hash != STRINGLOOKUP_TYPE:
            raise EStringLookup('pas un fichier StringLookup (une instance de type 0x{:08x} attendue)'.format(
                STRINGLOOKUP_TYPE))
        ins = adf.table_instance[0]
        self.i0 = base + ins.offset
        self.size = ins.size
        types = {t.name.decode('utf-8', 'replace'): t for t in adf.table_typedef}

        def member(tname: str, mname: str) -> int:
            for m in types[tname].members:
                if m.name_utf8 == mname:
                    return m.offset
            raise EStringLookup('type {} sans membre {}'.format(tname, mname))

        try:
            self.m = {
                'Languages': member('StringLookup', 'Languages'), 'Text': member('StringLookup', 'Text'),
                'SortedPairs': member('LanguageStringLookup', 'SortedPairs'),
                'SortedDialogueLines': member('LanguageStringLookup', 'SortedDialogueLines'),
                'Hash': member('HashPropertiesPair', 'Hash'), 'TextOffset': member('HashPropertiesPair', 'TextOffset'),
                'DHash': member('DialogueLine', 'Hash'), 'NameOffset': member('DialogueLine', 'NameOffset'),
                'Subtitles': member('DialogueLine', 'Subtitles'), 'LineHash': member('Subtitle', 'LineHash'),
            }
            self.lang_size = types['LanguageStringLookup'].size
            self.pair_size = types['HashPropertiesPair'].size
            self.line_size = types['DialogueLine'].size
            self.sub_size = types['Subtitle'].size
        except KeyError as e:
            raise EStringLookup('type {} absent'.format(e))
        lang_off, self.n_lang = self.array(self.m['Languages'])
        self.text_off, self.text_len = self.array(self.m['Text'])
        self.text_blob = data[self.i0 + self.text_off:self.i0 + self.text_off + self.text_len]
        self.lang_pos = [lang_off + i * self.lang_size for i in range(self.n_lang)]

    def array(self, rel: int) -> Tuple[int, int]:
        off, _link, count = struct.unpack_from('<III', self.data, self.i0 + rel)
        return off, count

    def text(self, offset: int) -> str:
        end = self.text_blob.find(b'\0', offset)
        return self.text_blob[offset:end if end >= 0 else None].decode('utf-8', 'replace')

    def pairs(self, lang: int) -> List[Tuple[int, int]]:
        off, n = self.array(self.lang_pos[lang] + self.m['SortedPairs'])
        h, t = self.m['Hash'], self.m['TextOffset']
        return [(struct.unpack_from('<I', self.data, self.i0 + off + k * self.pair_size + h)[0],
                 struct.unpack_from('<I', self.data, self.i0 + off + k * self.pair_size + t)[0]) for k in range(n)]

    def lines(self, lang: int) -> List[Tuple[int, int, int, int]]:
        """[(Hash, NameOffset, offset des sous-titres, nombre de sous-titres)]."""
        off, n = self.array(self.lang_pos[lang] + self.m['SortedDialogueLines'])
        out = []
        for k in range(n):
            p = self.i0 + off + k * self.line_size
            h = struct.unpack_from('<I', self.data, p + self.m['DHash'])[0]
            name = struct.unpack_from('<I', self.data, p + self.m['NameOffset'])[0]
            s_off, _link, s_n = struct.unpack_from('<III', self.data, p + self.m['Subtitles'])
            out.append((h, name, s_off, s_n))
        return out

    def subtitle_hash(self, s_off: int) -> int:
        return struct.unpack_from('<I', self.data, self.i0 + s_off + self.m['LineHash'])[0]

    def texts(self, key: str) -> List[Optional[str]]:
        """Texte de la cle dans chaque langue (None si absente)."""
        h = key_hash(key)
        out = []
        for lang in range(self.n_lang):
            pairs = self.pairs(lang)
            hashes = [p[0] for p in pairs]
            i = bisect_left(hashes, h)
            out.append(self.text(pairs[i][1]) if i < len(hashes) and hashes[i] == h else None)
        return out


def decode(data: bytes):
    """Octets -> Adf lu par DECA. Les types d'un .stringlookup sont tous dans le fichier : pas besoin de la base
    de types du projet (contrairement a adf_ajouter_ligne.decoder)."""
    from deca.ff_adf import Adf
    from deca.file import ArchiveFile
    adf = Adf()
    adf.deserialize(ArchiveFile(io.BytesIO(data[AE.adf_base(data):])))
    return adf


def language_index(code: str) -> int:
    c = code.strip().lower()
    if c not in LANGUES:
        raise EStringLookup('langue "{}" inconnue ; langues : {}'.format(code, ', '.join(LANGUES)))
    return LANGUES.index(c)


@dataclass
class Entree:
    cle: str
    textes: List[str]          # un texte par langue, dans l'ordre de LANGUES
    modele: Optional[str] = None   # cle existante dont la ligne de dialogue est copiee (defaut : la premiere ;
                                   # dans le fichier du jeu, FMODEvent, CharacterName et Flags sont les memes partout)


@dataclass
class Plan:
    entrees: List[Entree]
    hashes: List[int]
    key_offsets: Dict[str, int] = field(default_factory=dict)
    text_offsets: Dict[Tuple[str, int], int] = field(default_factory=dict)
    chain: str = ''
    head: Optional[int] = None
    text_in_place: bool = False


def textes_par_langue(specs: Sequence[str], n_lang: int = len(LANGUES)) -> List[str]:
    """["texte"] ou ["texte", "en=text", "de=..."] : texte sans prefixe pour toutes les langues, puis langue=texte
    pour celles qui different. Un prefixe n'est reconnu que s'il est un code de LANGUES."""
    default, per = None, {}
    for s in specs:
        code, sep, rest = s.partition('=')
        if sep and code.strip().lower() in LANGUES:
            per[language_index(code)] = rest
        else:
            if default is not None:
                raise EStringLookup('deux textes sans langue reconnue : "{}" et "{}" (langues : {})'.format(
                    default, s, ', '.join(LANGUES)))
            default = s
    if default is None:
        if len(per) != n_lang:
            raise EStringLookup('texte par defaut absent : il faut un texte sans langue, ou les {} langues'.format(n_lang))
        default = ''
    return [per.get(i, default) for i in range(n_lang)]


def add(decode: Callable[[bytes], object], data: bytes, entrees: List[Entree]) -> Tuple[bytes, Plan]:
    """Ajoute les entrees (cles nouvelles) dans toutes les langues. decode : octets -> Adf lu par DECA."""
    vue = Vue(data)
    if not entrees:
        raise EStringLookup('aucune entree')
    pairs0 = {h for h, _t in vue.pairs(0)}
    hashes = []
    for e in entrees:
        if len(e.textes) != vue.n_lang:
            raise EStringLookup('{} : {} textes pour {} langues'.format(e.cle, len(e.textes), vue.n_lang))
        if any(b'\0' in t.encode('utf-8') for t in e.textes) or not e.cle:
            raise EStringLookup('{} : cle vide ou texte contenant un octet nul'.format(e.cle))
        h = key_hash(e.cle)
        if h in pairs0:
            raise EStringLookup('la cle "{}" (hash 0x{:08x}) existe deja dans le fichier'.format(e.cle, h))
        if h in hashes:
            raise EStringLookup('deux entrees de meme hash 0x{:08x}'.format(h))
        if e.modele and key_hash(e.modele) not in pairs0:
            raise EStringLookup('cle modele "{}" absente du fichier'.format(e.modele))
        hashes.append(h)

    adf = decode(data)
    if adf is None:
        raise EStringLookup('fichier illisible par DECA')
    ed = AE.InstanceEditor(adf, data, 0)
    full = adf.table_instance_full_values[0].value
    plan = Plan(entrees, hashes, head=ed.chain[0] if ed.chain else None)

    # 1. textes : cles en clair puis textes, a la fin du bloc Text (sur place s'il finit l'instance) ; un texte
    #    identique (meme texte dans plusieurs langues) n'est ecrit qu'une fois
    blob = bytearray()
    known: Dict[str, int] = {}

    def put(s: str) -> int:
        if s not in known:
            known[s] = vue.text_len + len(blob)
            blob.extend(s.encode('utf-8') + b'\0')
        return known[s]

    for e in entrees:
        plan.key_offsets[e.cle] = put(e.cle)
        for lang, t in enumerate(e.textes):
            plan.text_offsets[(e.cle, lang)] = put(t)
    before = len(ed.buf)
    ed.grow(full['Text'], [bytes([b]) for b in blob])
    plan.text_in_place = len(ed.buf) - before == len(blob)

    # 2. chaque langue : paires et lignes de dialogue, nouvelles inserees a leur place dans l'ordre des hash
    m = vue.m
    for lang in range(vue.n_lang):
        lv = full['Languages'].value[lang].value
        p_hdr, _t, p_size, _a = ed.array(lv['SortedPairs'])
        d_hdr, _t, d_size, _a = ed.array(lv['SortedDialogueLines'])
        p_off, p_n = ed.array_state(p_hdr)
        d_off, d_n = ed.array_state(d_hdr)
        if p_n != d_n:
            raise EStringLookup('langue {} : {} paires mais {} lignes de dialogue'.format(lang, p_n, d_n))
        old_pairs = [bytes(ed.buf[p_off + k * p_size:p_off + (k + 1) * p_size]) for k in range(p_n)]
        old_lines = [bytes(ed.buf[d_off + k * d_size:d_off + (k + 1) * d_size]) for k in range(d_n)]
        old_hashes = [struct.unpack_from('<I', b, m['Hash'])[0] for b in old_pairs]
        if any(b <= a for a, b in zip(old_hashes, old_hashes[1:])):
            raise EStringLookup('langue {} : paires non triees dans le fichier d\'origine'.format(lang))
        new_items = []
        for e, h in zip(entrees, hashes):
            mi = bisect_left(old_hashes, key_hash(e.modele)) if e.modele else 0
            model_line = old_lines[mi]
            s_off, _l, s_n = struct.unpack_from('<III', model_line, m['Subtitles'])
            if s_n < 1:
                raise EStringLookup('ligne de dialogue modele sans sous-titre ({}, langue {})'.format(e.modele, lang))
            sub = bytearray(ed.buf[s_off:s_off + vue.sub_size])
            struct.pack_into('<I', sub, m['LineHash'], h)
            sub_pos = ed.new_array(bytes(sub))
            pair = bytearray(p_size)
            struct.pack_into('<I', pair, m['Hash'], h)
            struct.pack_into('<I', pair, m['TextOffset'], plan.text_offsets[(e.cle, lang)])
            line = bytearray(model_line)
            struct.pack_into('<I', line, m['DHash'], h)
            struct.pack_into('<I', line, m['NameOffset'], plan.key_offsets[e.cle])
            struct.pack_into('<III', line, m['Subtitles'], sub_pos, 0, 1)
            new_items.append((h, bytes(pair), bytes(line)))
        merged = sorted([(h, p, d) for h, p, d in zip(old_hashes, old_pairs, old_lines)] + new_items, key=lambda x: x[0])
        ed.replace(lv['SortedPairs'], [p for _h, p, _d in merged])
        ed.replace(lv['SortedDialogueLines'], [d for _h, _p, d in merged])

    out = ed.result()
    plan.chain = '{} -> {} champs'.format(len(ed.chain), len(ed.final_chain))
    return out, plan


def verify(decode: Callable[[bytes], object], old_data: bytes, new_data: bytes, plan: Plan):
    """Controle : dans chaque langue, paires strictement triees et paralleles aux lignes de dialogue ; chaque cle
    d'origine donne le meme texte ; chaque cle ajoutee donne son texte, sa ligne de dialogue designe la cle en clair
    et un sous-titre propre ; le bloc Text d'origine est intact ; DECA relit le fichier et la chaine de relocation
    couvre tous les champs pointeurs."""
    vo, vn = Vue(old_data), Vue(new_data)
    if vn.n_lang != vo.n_lang:
        raise EStringLookup('verification : nombre de langues change')
    if vn.text_blob[:len(vo.text_blob)] != vo.text_blob:
        raise EStringLookup('verification : le bloc Text d\'origine a change')
    new_hashes = dict(zip(plan.hashes, plan.entrees))
    for lang in range(vn.n_lang):
        pn, ln = vn.pairs(lang), vn.lines(lang)
        hn = [h for h, _t in pn]
        if any(b <= a for a, b in zip(hn, hn[1:])):
            raise EStringLookup('verification : langue {} : paires non triees'.format(lang))
        if hn != [x[0] for x in ln]:
            raise EStringLookup('verification : langue {} : lignes de dialogue non paralleles aux paires'.format(lang))
        tn = dict(pn)
        po = vo.pairs(lang)
        if len(pn) != len(po) + len(plan.hashes):
            raise EStringLookup('verification : langue {} : {} paires au lieu de {}'.format(
                lang, len(pn), len(po) + len(plan.hashes)))
        for h, t in po:
            if h not in tn or vn.text(tn[h]) != vo.text(t):
                raise EStringLookup('verification : langue {} : texte d\'origine change (hash 0x{:08x})'.format(lang, h))
        lines = {x[0]: x for x in ln}
        for h, e in new_hashes.items():
            if h not in tn or vn.text(tn[h]) != e.textes[lang]:
                raise EStringLookup('verification : langue {} : texte de "{}" incorrect'.format(lang, e.cle))
            _h, name, s_off, s_n = lines[h]
            if vn.text(name) != e.cle or s_n != 1 or vn.subtitle_hash(s_off) != h:
                raise EStringLookup('verification : langue {} : ligne de dialogue de "{}" incorrecte'.format(lang, e.cle))
    adf = decode(new_data)
    if adf is None:
        raise EStringLookup('verification : fichier produit illisible par DECA')
    try:
        AE.verify_chain(adf, new_data, 0, plan.head)
    except AE.EAdfEcriture as e:
        raise EStringLookup('verification : {}'.format(e))
