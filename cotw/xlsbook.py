"""
Feuilles XLSBook (ADF, type 0x0b73315d) : lecture en lignes, et ajout d'une ligne copiee d'une autre.

Le jeu range ses tableaux de reglages issus d'Excel (equipment_stats_ui.bin...) sous cette forme, lue comme
deca.export_import_adf.adf_export_xlsx_0x0b73315d :
  Sheet[]      : Name (chaine), Cols, Rows, CellIndex[] (Rows x Cols indices dans Cell, ligne par ligne) ;
  Cell[]       : Type (0 booleen, 1 chaine, 2 nombre), DataIndex (index dans BoolData, StringData ou ValueData),
                 AttributeIndex (format) ;
  StringData[], ValueData[], BoolData[], Attribute[], ColorData[].
Une meme cellule (Cell) peut servir a plusieurs cases : CellIndex ne fait que la designer.

Ajouter une ligne copiee d'un modele, sous un nouveau nom :
  StringData += nouveau nom ; Cell += copie de la cellule du nom du modele, DataIndex = nouveau nom ;
  CellIndex de la feuille += les indices de la ligne modele, la case du nom remplacee ; Rows += 1.
Les autres cases (stats) designent les memes cellules que le modele, sauf celles dont on donne la valeur
(values) : elles recoivent leur propre cellule (et leur valeur, ajoutee a ValueData, StringData ou BoolData si
elle n'y est pas deja). La ligne est ajoutee en fin de feuille.
Les nouveaux champs pointeurs (en-tetes de chaine recopies et ajoutes) sont rattaches a la chaine de relocation
(voir adf_ecriture.py).
Constat en jeu (test8) : le jeu retrouve une ligne par le texte du nom ; [hypothese] l'ordre des lignes est
indifferent.
"""
import copy
import struct
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import adf_ecriture as AE

XLSBOOK_TYPE = 0x0b73315d
CELL_BOOL, CELL_STRING, CELL_VALUE = 0, 1, 2


class EXls(Exception):
    pass


def require_book(values):
    for key in ('Sheet', 'Cell', 'StringData'):
        if not isinstance(values, dict) or key not in values:
            raise EXls('pas une feuille XLSBook (membre "{}" absent)'.format(key))


def cell_text(values, cell) -> Optional[bytes]:
    if int(cell['Type']) != CELL_STRING:
        return None
    i = int(cell['DataIndex'])
    return bytes(values['StringData'][i]) if i < len(values['StringData']) else None


def cell_value(values, cell):
    """Valeur affichee d'une cellule (chaine, nombre ou booleen), comme l'export xlsx de DECA."""
    t, i = int(cell['Type']), int(cell['DataIndex'])
    data = {CELL_BOOL: 'BoolData', CELL_STRING: 'StringData', CELL_VALUE: 'ValueData'}.get(t)
    if data is None or data not in values or i >= len(values[data]):
        return None
    v = values[data][i]
    return bytes(v).decode('utf-8', 'replace') if t == CELL_STRING else (bool(v) if t == CELL_BOOL else float(v))


def sheet_rows(values, sheet_index: int) -> List[List[object]]:
    s = values['Sheet'][sheet_index]
    cols, rows, ci = int(s['Cols']), int(s['Rows']), s['CellIndex']
    return [[cell_value(values, values['Cell'][int(ci[c + cols * r])]) for c in range(cols)] for r in range(rows)]


@dataclass
class RowMatch:
    sheet: int
    sheet_name: str
    row: int
    columns: List[int]      # colonnes dont la cellule est le texte cherche


def find_rows(values, text: str, sheet_name: Optional[str] = None) -> List[RowMatch]:
    """Lignes dont une case est exactement le texte (cellule de type chaine)."""
    require_book(values)
    target = text.encode('utf-8')
    out = []
    for si, s in enumerate(values['Sheet']):
        name = bytes(s['Name']).decode('utf-8', 'replace')
        if sheet_name is not None and name != sheet_name:
            continue
        cols, rows, ci = int(s['Cols']), int(s['Rows']), s['CellIndex']
        for r in range(rows):
            hit = [c for c in range(cols) if cell_text(values, values['Cell'][int(ci[c + cols * r])]) == target]
            if hit:
                out.append(RowMatch(si, name, r, hit))
    return out


@dataclass
class AddRowPlan:
    match: RowMatch
    new_name: str
    string_index: int               # index du nouveau nom dans StringData
    cell_index: int                 # index de la cellule du nouveau nom dans Cell
    template_cell: int              # cellule du nom du modele
    new_indices: List[int]          # CellIndex de la ligne ajoutee
    chain: str                      # longueur de la chaine de relocation, avant -> apres
    new_row: int                    # index de la ligne ajoutee (derniere ligne de la feuille)
    chain_head: Optional[int]
    new_strings: List[bytes] = field(default_factory=list)            # ajoutes a StringData (le nom d'abord)
    new_values: List[float] = field(default_factory=list)             # ajoutes a ValueData (float32)
    new_bools: List[int] = field(default_factory=list)                # ajoutes a BoolData
    new_cells: List[Tuple[int, int]] = field(default_factory=list)    # ajoutees a Cell : (cellule copiee, DataIndex)
    expected_row: List[object] = field(default_factory=list)          # valeurs affichees par la ligne ajoutee
    changed: List[str] = field(default_factory=list)                  # colonnes modifiees, pour le rapport


def _fold(text: str) -> str:
    """Minuscules sans accents, pour comparer un nom de colonne tape a la main."""
    return ''.join(c for c in unicodedata.normalize('NFD', text.lower()) if unicodedata.category(c) != 'Mn').strip()


def resolve_column(header: List[object], key: str) -> int:
    """Colonne designee par son numero (0 = premiere), par son en-tete exact, ou par un morceau unique de son
    en-tete, sans tenir compte des majuscules ni des accents (ex : "penetration" -> "Stat Bullet Penetration")."""
    key = str(key).strip()
    if key.isdigit():
        c = int(key)
        if not 0 <= c < len(header):
            raise EXls('colonne {} absente ({} colonnes)'.format(c, len(header)))
        return c
    k = _fold(key)
    texts = [(c, _fold(h)) for c, h in enumerate(header) if isinstance(h, str)]
    for matches in ([c for c, h in texts if h == k], [c for c, h in texts if k in h]):
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise EXls('colonne "{}" ambigue : {}'.format(key, ', '.join('"{}"'.format(header[c]) for c in matches)))
    raise EXls('colonne "{}" introuvable ; en-tetes : {}'.format(key, ', '.join(repr(h) for h in header)))


def _parse(kind: int, text: str):
    if kind == CELL_VALUE:
        try:
            return struct.unpack('<f', struct.pack('<f', float(str(text).replace(',', '.'))))[0]
        except ValueError:
            raise EXls('nombre attendu, recu "{}"'.format(text))
    if kind == CELL_BOOL:
        v = _fold(str(text))
        if v in ('true', 'vrai', 'oui', '1'):
            return True
        if v in ('false', 'faux', 'non', '0'):
            return False
        raise EXls('booleen attendu (true/false), recu "{}"'.format(text))
    return str(text)


def add_row(adf, data: bytes, template: str, new_name: str, sheet_name: Optional[str] = None,
            instance: int = 0, values: Optional[Dict[str, str]] = None,
            own_cells: bool = False) -> Tuple[bytes, AddRowPlan]:
    """Ajoute a la feuille une ligne copiee de celle du modele, sous new_name. Renvoie (fichier, plan).
    values : {colonne: valeur} a donner a la nouvelle ligne (colonne : voir resolve_column ; le type est celui de la
    cellule du modele : nombre, texte ou booleen). Chaque colonne modifiee recoit sa propre cellule ; les autres
    designent les cellules du modele, sauf avec own_cells (toutes les cases ont alors leur propre cellule)."""
    if adf.table_instance[instance].type_hash != XLSBOOK_TYPE:
        raise EXls('instance {} : type 0x{:08x}, pas XLSBook (0x{:08x})'.format(
            instance, adf.table_instance[instance].type_hash, XLSBOOK_TYPE))
    book = AE.plain_values(adf, instance)
    require_book(book)
    if not new_name or new_name == template:
        raise EXls('nouveau nom vide ou identique au modele')
    if find_rows(book, new_name):
        raise EXls('"{}" a deja une ligne dans le fichier'.format(new_name))
    matches = find_rows(book, template, sheet_name)
    if not matches:
        raise EXls('aucune ligne pour "{}"{}'.format(template, ' dans la feuille "{}"'.format(sheet_name) if sheet_name else ''))
    if len(matches) > 1:
        raise EXls('"{}" a {} lignes ({}) : preciser la feuille'.format(
            template, len(matches), ', '.join('{} ligne {}'.format(mm.sheet_name, mm.row) for mm in matches)))
    m = matches[0]

    full = adf.table_instance_full_values[instance].value
    ed = AE.InstanceEditor(adf, data, instance)
    sheet = book['Sheet'][m.sheet]
    cols = int(sheet['Cols'])
    row_indices = [int(x) for x in sheet['CellIndex'][m.row * cols:(m.row + 1) * cols]]
    template_cell = row_indices[m.columns[0]]
    if any(row_indices[c] != template_cell for c in m.columns):
        raise EXls('le nom du modele occupe plusieurs cellules differentes dans sa ligne')
    shown = sheet_rows(book, m.sheet)
    header, template_row = shown[0], shown[m.row]

    # valeurs demandees, par colonne, converties au type de la cellule du modele
    wanted: Dict[int, object] = {}
    for key, text in (values or {}).items():
        c = resolve_column(header, key)
        if c in m.columns:
            raise EXls('la colonne du nom ne se change pas par --valeur')
        wanted[c] = _parse(int(book['Cell'][row_indices[c]]['Type']), text)
    own = sorted(set(wanted) | (set(range(cols)) - set(m.columns) if own_cells else set()))

    # donnees : reutiliser une valeur existante identique (le jeu partage ainsi les siennes), sinon l'ajouter
    new_strings, new_values, new_bools = [new_name.encode('utf-8')], [], []
    n_str, n_val = len(book['StringData']), len(book['ValueData'])
    bools = list(bytes(book['BoolData'])) if 'BoolData' in book else []
    cell_specs = []                                       # (colonne, cellule copiee, DataIndex)
    for c in own:
        tcell = row_indices[c]
        kind = int(book['Cell'][tcell]['Type'])
        if c not in wanted:
            cell_specs.append((c, tcell, int(book['Cell'][tcell]['DataIndex'])))
            continue
        v = wanted[c]
        if kind == CELL_STRING:
            b = v.encode('utf-8')
            known = [bytes(s) for s in book['StringData']] + new_strings
            idx = known.index(b) if b in known else n_str + len(new_strings)
            if b not in known:
                new_strings.append(b)
        elif kind == CELL_VALUE:
            known = [struct.unpack('<f', struct.pack('<f', float(x)))[0] for x in book['ValueData']] + new_values
            idx = known.index(v) if v in known else n_val + len(new_values)
            if v not in known:
                new_values.append(v)
        else:
            known = bools + new_bools
            idx = known.index(int(v)) if int(v) in known else len(bools) + len(new_bools)
            if int(v) not in known:
                new_bools.append(int(v))
        cell_specs.append((c, tcell, idx))

    # 1. chaines (le nouveau nom d'abord), valeurs, booleens : ajoutes en fin d'instance (les en-tetes de chaine
    #    sont rattaches a la chaine de relocation par ed.result())
    string_index = ed.grow(full['StringData'], [ed.new_string(s) for s in new_strings])
    if new_values:
        _h, vt, vsize, _a = ed.array(full['ValueData'])
        fmt = {4: '<f', 8: '<d'}.get(vsize)
        if fmt is None:
            raise EXls('ValueData : elements de {} octets, format inconnu'.format(vsize))
        ed.grow(full['ValueData'], [struct.pack(fmt, x) for x in new_values])
    if new_bools:
        ed.grow(full['BoolData'], [bytes([x]) for x in new_bools])

    # 2. cellules : celle du nom (copie de celle du modele, DataIndex = nouveau nom), puis les cellules propres
    cell_type = full['Cell'].value[template_cell].type_id
    member = ed.member(cell_type, 'DataIndex')

    def cell_with(tcell: int, data_index: int) -> bytes:
        _pos, raw = ed.element(full['Cell'], tcell)
        b = bytearray(raw)
        b[member.offset:member.offset + member.size] = data_index.to_bytes(member.size, 'little')
        return bytes(b)

    new_cells = [(template_cell, string_index)] + [(tcell, idx) for _c, tcell, idx in cell_specs]
    cell_index = ed.grow(full['Cell'], [cell_with(tc, di) for tc, di in new_cells])
    own_index = {c: cell_index + 1 + k for k, (c, _t, _i) in enumerate(cell_specs)}

    # 3. la ligne : indices du modele, cases du nom et des cellules propres remplacees ; puis Rows += 1
    new_indices = [cell_index if c in m.columns else own_index.get(c, x) for c, x in enumerate(row_indices)]
    sheet_value = full['Sheet'].value[m.sheet]
    ci_value = sheet_value.value['CellIndex']
    _h, ci_type, ci_size, _a = ed.array(ci_value)
    ed.grow(ci_value, [x.to_bytes(ci_size, 'little') for x in new_indices])
    rows_pos, rows_member = ed.member_pos(sheet_value, 'Rows')
    ed.write_uint(rows_pos, rows_member.size, ed.read_uint(rows_pos, rows_member.size) + 1)

    expected_row = [new_name if c in m.columns else wanted.get(c, template_row[c]) for c in range(cols)]
    out = ed.result()
    plan = AddRowPlan(m, new_name, string_index, cell_index, template_cell, new_indices,
                      '{} -> {} champs'.format(len(ed.chain), len(ed.final_chain)), int(sheet['Rows']),
                      ed.chain[0] if ed.chain else None, new_strings, new_values, new_bools, new_cells,
                      expected_row, ['{} = {!r}'.format(header[c], wanted[c]) for c in sorted(wanted)])
    return out, plan


def expected_values(old_values, plan: AddRowPlan):
    """Ce que DECA doit relire apres add_row : l'original, plus exactement les ajouts du plan."""
    exp = copy.deepcopy(old_values)
    exp['StringData'].extend(plan.new_strings or [plan.new_name.encode('utf-8')])
    if plan.new_values:
        conv = type(exp['ValueData'][0]) if exp['ValueData'] else float
        exp['ValueData'] = list(exp['ValueData']) + [conv(x) for x in plan.new_values]
    if plan.new_bools:
        exp['BoolData'] = bytes(exp['BoolData']) + bytes(plan.new_bools)
    for tcell, data_index in (plan.new_cells or [(plan.template_cell, plan.string_index)]):
        cell = copy.deepcopy(old_values['Cell'][tcell])
        cell['DataIndex'] = type(cell['DataIndex'])(data_index)
        exp['Cell'].append(cell)
    s = exp['Sheet'][plan.match.sheet]
    old_ci = list(s['CellIndex'])                       # DECA rend une liste (scalaires numpy ou entiers)
    conv = type(old_ci[0]) if old_ci else int
    s['CellIndex'] = old_ci + [conv(x) for x in plan.new_indices]
    s['Rows'] = type(s['Rows'])(int(s['Rows']) + 1)
    return exp


def verify(decode, old_data: bytes, new_data: bytes, plan: AddRowPlan, instance: int = 0):
    """Relit les deux fichiers avec DECA (decode : octets -> Adf) : la nouvelle lecture doit etre l'ancienne plus
    les ajouts du plan, rien d'autre ; la ligne ajoutee doit montrer les valeurs du modele, au nom et aux valeurs
    demandees pres ; les autres instances doivent etre identiques."""
    old, new = decode(old_data), decode(new_data)
    if old is None or new is None:
        raise EXls('verification : fichier illisible par DECA')
    ov, nv = AE.plain_values(old, instance), AE.plain_values(new, instance)
    if AE.normalize(expected_values(ov, plan)) != AE.normalize(nv):
        raise EXls('verification : la relecture differe de l\'original plus la ligne ajoutee')
    rows = sheet_rows(nv, plan.match.sheet)
    added = rows[-1]
    for c, (want, got) in enumerate(zip(plan.expected_row, added)):
        if AE.normalize(want) != AE.normalize(got):
            raise EXls('verification : colonne {} de la ligne ajoutee : {!r} au lieu de {!r}'.format(c, got, want))
    for j in range(len(old.table_instance)):
        if j != instance and AE.normalize(AE.plain_values(old, j)) != AE.normalize(AE.plain_values(new, j)):
            raise EXls('verification : instance {} modifiee'.format(j))
    # chaine de relocation du fichier produit : complete, croissante, meme tete
    try:
        AE.verify_chain(new, new_data, instance, plan.chain_head)
    except AE.EAdfEcriture as e:
        raise EXls('verification : {}'.format(e))
    return rows[-1]
