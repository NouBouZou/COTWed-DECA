# Ajouter une munition

[← Documentation détaillée](README.md) · [README du dépôt (anglais)](../../README.md)

## État au 2026-10-07

**Une munition ajoutée fonctionne en jeu.** Elle est visible au magasin, s'achète, affiche ses propres stats (`--stats-de` et `--stat`), son nom et sa description, ainsi que le logo de possession. Elle se charge, se tire et consomme ses propres munitions. Plusieurs copies d'une même munition coexistent avec elle sans bloquer le changement de munition (test14). Pour cela, quatre fichiers sont déposés dans `dropzone`, tous écrits sans rien écraser :

| Fichier | Format | Rôle |
|---|---|---|
| `settings/hp_settings/equipment_data.bin` | RTPC | l'objet (prix, compatibilités, DLC, `locked`…) |
| `settings/spawn_ammunition_defs.bin` | RTPC | lien nom → entité |
| `editor/entities/…/<nom>.ee` | SARC | entité propre (`.epe` avec `item_id`, `tuning_file`, `_object_id` nouveaux ; instance du `.ammotunec` renommée) |
| `settings/hp_settings/equipment_stats_ui.bin` | ADF (XLSBook) | stats affichées et logo de possession |

**Nom et description propres** (`--nom-affiche`, `--description`, vérifiés en jeu) : un cinquième fichier, `settings/hp_settings/string_ids.stringlookup` (ADF StringLookup, table de traduction des 10 langues), reçoit les clés `<nom>_name` et `<nom>_description`, que désignent `display_name` et `display_description` de la nouvelle munition. Le fichier passe de 38,8 à 49,4 Mo (les anciennes copies des tableaux triés restent en place).

**Balistique propre** (`--balistique CHAMP=VALEUR`, `--classes`, ou `"ballistics"` / `"classes"` dans un fichier de mod ; test15 : réglage modifié accepté par le jeu, effet de la traçante non observé) : les valeurs du `.ammotunec` de l'entité propre sont modifiées (pénétration, expansion, masse, traçante, classes d'animaux…), sans toucher la source. `python cotw\ammotune.py PROJET OBJET` liste les champs et leurs valeurs. L'Ammo Workshop ne les édite pas encore ; il les garde quand il enregistre un mod. Les stats affichées viennent de la ligne de stats : les mettre en accord.

**Ce que font les champs du `.ammotunec`** (lu dans le code d'un build de 2020 grâce à ses symboles de débogage, `pdb_2020.py` ; à revérifier sur le jeu actuel) :
- **Vitesse initiale :** `v0 = √(2 × kinetic_energy / mass)`. L'énergie reçoit d'abord le bonus du module de l'arme, puis le multiplicateur du perk d'énergie à la bouche ; `velocity_deviance` (0 à 1) retire ensuite une part aléatoire de `v0`. Unités SI : pour la .243 SP (2 618 J, 0,00616 kg), `v0` ≈ 922 m/s.
- **Trajectoire**, à chaque image :
  - traînée `½ × ρ × drag_coefficient × (π × diameter² / 4) × v²`, où ρ est la densité de l'air donnée par la météo ;
  - vent `½ × ρ × drag_coefficient × S × vent²` : la force ne dépend pas de la vitesse de la balle ; S va de la section frontale (vent dans l'axe) à `diameter × length` (vent de travers), et le vent est réduit sous le couvert (occlusion du ciel) ;
  - gravité : `gravity_strength` du réglage **de l'arme**, pas de la munition ;
  - dans l'eau, la balle perd 200 m/s par seconde.
- `ballistic_coefficient_front` et `_side` existaient en 2020 sans être lus par la trajectoire ; ils ont disparu du `.ammotunec` actuel.
- **Blessures :** chaque organe traversé ajoute une perte de santé par seconde, pendant la durée propre à l'organe (−1 : permanente), égale à `(expansion + projectile_damage) × énergie à l'impact × multiplicateur de l'organe`.
  - L'expansion croît avec la profondeur au taux `projectile_expansion_rate` jusqu'à `projectile_max_expansion`, puis décroît au taux `projectile_contraction_rate`.
  - L'énergie restante baisse de `½ × densité de l'organe × (mass / section) × projectile_penetration` par mètre d'organe, et la balle s'arrête dans l'organe où elle tombe à zéro. Telle qu'écrite, une `projectile_penetration` plus grande freine davantage : à confronter aux valeurs des munitions du jeu.

**Icône propre** (`--icone IMAGE` ou `--icone-de OBJET`, ou `"icon"` / `"icon_from"` dans un fichier de mod ; vérifiée en jeu, test16) : une texture `textures/ui/items/ammo/<nom>_alpha.ddsc` est ajoutée, et `display_icon` de la nouvelle munition la désigne. `--icone` la fabrique depuis une image (PNG à fond transparent de préférence, mise à 640 × 320 en gardant les proportions) : compression BC3 et 9 niveaux de détail par `cotw/icone.py`, en-tête repris de l'icône de la source, contrôle par un décodage de référence et par la lecture de DECA. `--icone-de` copie l'icône d'un autre objet. Dans un fichier de mod, le chemin de l'image est relatif au fichier. L'Ammo Workshop ne les édite pas encore ; il les garde quand il enregistre un mod.

**Modèle de cartouche** (`--modele-de OBJET`, `--modele`, `--modele-douille`, ou `"model_from"` / `"model"` / `"casing_model"` dans un fichier de mod) : l'entité d'une munition peut déclarer le modèle de la cartouche chargée et de la douille éjectée (`base_model` des composants `fresh_round` et `empty_casing` du `.epe`), et l'outil les écrit dans l'entité propre de la nouvelle munition. **L'arme a le dernier mot** (test17 : la Ranger .243 a ignoré le modèle déclaré) :
- une arme sans composant de cartouche (`CModelAttachementWeaponComponent` aux points `ammo_reload` / `ammo_used`), comme les .223 et .270 du jeu, affiche le modèle de la munition ;
- une arme qui a ses propres composants (Ranger .243…) affiche toujours les siens. L'outil refuse alors, sauf avec `--basculer-armes` (`"convert_weapons": true`) : les composants sont retirés de l'entité de l'arme, et leur modèle est déclaré dans chaque munition compatible qui n'en déclare pas, pour que leur aspect ne change pas (à vérifier en jeu, test18). Les entités de ces munitions, et celle de l'arme, sont alors des fichiers du mod ;
- une arme dont les cartouches changent sur événement (`show_event` / `hide_event` ; relevé : 12GA semi-automatique type 2, 16GA 1897, 10GA superposé et semi-automatique, .410) n'est pas prise en charge : refus.

`python cotw\modele_munition.py PROJET OBJET` affiche les modèles déclarés par une munition.

**Étiquettes de spawn** (`--etiquettes A,B,C`, ou `"tags"` dans un fichier de mod) : les étiquettes de l'entrée de `spawn_ammunition_defs.bin` (`ammo_cal_243`, `ammo_type_sp`, `ammunition`…), à la place de celles de la source ; le nom de la munition reste en tête, comme dans le jeu. Leur rôle en jeu est inconnu : une étiquette absente du jeu est signalée, le nom d'un autre objet est refusé.

**Dépendances restantes** (2026-10-07, `cotw/dependances.py`) : chaque construction (ligne de commande, `construire_mod`, atelier) se termine par une section qui dit, élément par élément, ce que chaque munition ajoutée doit encore à un objet d'origine, d'après les fichiers produits :

| Statut | Sens |
|---|---|
| `partage` | le jeu lit une ressource d'un objet d'origine : entité, clé de traduction, icône, modèle de l'arme, son (`.fmod_guids`), nom d'instance du réglage… ; « (munition du mod) » quand c'est une autre munition du même mod |
| `absent` | élément manquant : pas de ligne de stats, aucun modèle |
| `inconnu` | non déterminé (fichier illisible, ligne de stats placée après la première ligne vide…) |
| `herite` | valeur recopiée, encore égale à celle d'origine : champs d'`equipment_data`, étiquettes, valeurs balistiques… |
| `propre` | propre à la munition |

Elle signale aussi les incohérences connues, par exemple des classes affichées (ligne de stats) différentes de celles du réglage balistique. `python cotw\dependances.py PROJET MOD.cotwmod.json [--json FICHIER]` construit le mod en mémoire et n'affiche que cette analyse.

**Tout cela se fait aussi dans l'interface** (**Tools → Ammo Workshop…**, depuis le 2026-10-04). On y édite un **fichier de mod** (`.cotwmod.json`) qui liste les munitions à ajouter. **Build** reconstruit le dropzone du mod depuis les originaux du jeu, en une passe, avec exactement les mêmes octets que la ligne de commande. Exemple : [`cotw/mods/test14.cotwmod.json`](../../cotw/mods/test14.cotwmod.json).
