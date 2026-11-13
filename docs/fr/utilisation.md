# Utilisation (Windows, Invite de commandes)

[← Documentation détaillée](README.md) · [README du dépôt (anglais)](../../README.md)

Prérequis : Python **3.12.10** 64 bits (numba 0.60.0 n'a pas de paquet Windows pour 3.13+), Git.

```
py -3.12 -m venv venv
venv\Scripts\activate.bat
pip install -r python\requirements.txt
pip install -e python\deca -e python\deca_gui
```

- **Scan du jeu** : `cotw\lancer_deca.bat`, puis **File → New Project…** et `theHunterCotW_F.exe`. Le projet est créé dans `..\work\hp`.
- **Ouvrir un projet au lancement** : `python python\deca_gui\deca_gui\entry_point.py <project.json ou son dossier>`.
- **Sans le jeu** (portable) : `cotw\lancer_demo.bat` crée au besoin le projet de démonstration dans `..\work_demo` et l'ouvre. Le vrai projet `..\work\hp` n'est jamais touché.
- **Atelier** : DECA ouvert sur le projet, **Tools → Ammo Workshop…**. **Open mod…** charge un `.cotwmod.json` ; **Build** écrit `<sortie>\dropzone`, à copier dans le `dropzone` du jeu. La sortie par défaut est `..\mods\<nom du mod>`.
- **Mod en ligne de commande** : `python cotw\construire_mod.py ..\work\hp cotw\mods\test14.cotwmod.json --sortie ..\mods\test14`.
- **Sauvegarde** : **File → Open Save…** dans DECA, ou `python cotw\sauvegarde.py <fichier> --projet ..\work\hp --langue fr`.
- **Audit de lecture** : `python cotw\deca_audit.py ..\work\hp` (DECA fermé). Le dossier de sortie (`..\work\audits\…`) contient `rapport.md` (section 3 : couverture par extension et priorités), `couverture_extensions.csv` et `extraire_echantillons.bat`, qui copie quelques fichiers de chaque format non lu dans `echantillons\`. `--compare <ancien metrics.json>` montre l'évolution.
- **Nommer les fichiers sans nom** (DECA fermé) : `python cotw\generer_noms.py ..\work\hp --sortie noms.txt --temoin`, puis `python cotw\renommer.py ajouter ..\work\hp --noms noms.txt --exe --fmod` (`--fmod` : banques FMOD citées par la banque de chaînes) ; `python cotw\renommer.py annuler ..\work\hp` revient en arrière.
- **Noms internes** (DECA fermé) : `python cotw\audit_noms_internes.py ..\work\hp --out ..\work\audits\noms-internes`, puis `python cotw\resoudre_hashes.py chercher ..\work\hp --cibles ..\work\audits\noms-internes\inconnus.csv --temoin --out resoudre.md` (écrit `cotw\dictionnaires\hp_noms.tsv`, que les visionneuses RTPC et ADF lisent directement ; `resoudre_hashes.py injecter ..\work\hp` les ajoute aussi à la base, pour la vue texte d'origine de DECA).
- **Typer sans rescanner** (DECA fermé) : `python cotw\retyper.py ajouter ..\work\hp` ; `python cotw\retyper.py annuler ..\work\hp` revient en arrière.
- **Outils** : `python cotw\<outil>.py --help`, DECA fermé.
- **Tests** : `python cotw\tests\test_<nom>.py`.
- **Charger les fichiers modifiés en jeu** : option de lancement Steam
  `--vfs-fs dropzone --vfs-archive archives_win64 --vfs-archive patch_win64 --vfs-archive dlc_win64 --vfs-fs .`

## Décodeur de textures en C (facultatif)

Sans lui, DECA décode les textures en Python (numba), plus lentement. Il faut Visual Studio 2022 Build Tools (« Développement Desktop en C++ ») et CMake. Depuis l'invite **x64 Native Tools Command Prompt for VS 2022**, à la racine du dépôt :

```
mkdir build
cd build
cmake -G "NMake Makefiles" -DCMAKE_BUILD_TYPE=Release ..
nmake
mkdir ..\root\lib
copy cpp\process_image\process_image.dll ..\root\lib\
```

Au premier décodage, la console affiche `Using C version of process_image from …`. `build\` et `root\` sont ignorés par git. Le sous-module HavokLib et `bin2xml` ne sont plus nécessaires : le fork lit les squelettes Havok en Python depuis le 2026-10-23.
