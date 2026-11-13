# COTWed DECA

A data reader and modding toolkit for **theHunter: Call of the Wild**, built on [DECA](https://github.com/kk49/deca) by kk49, the explorer for Avalanche Studios' APEX engine games.

COTWed DECA has two goals:

1. **Read everything the game ships.** Open, decode and name every file of COTW, and every hash inside those files, where the original DECA stops.
2. **Add content without overwriting anything.** Lay the groundwork for a community modloader that adds new items (ammunition first, weapons later) next to the game's own instead of replacing them.

> Personal project under active development. Windows only. Not affiliated with Expansive Worlds or Avalanche Studios. The repository contains no game files: you need your own copy of the game.

## What works today

### Adding ammunition

A new ammunition type, copied from an existing one, works in game. It shows up in the store, can be bought, and has its own stats, name, description and icon. It loads, fires and uses its own rounds. Its ballistics (penetration, expansion, mass, animal classes…) can be changed, and so can its cartridge model, on weapons that do not impose their own.

All the files go to the game's `dropzone` folder; no original file is overwritten.

- The **Ammo Workshop** (**Tools → Ammo Workshop…** in the GUI) edits a mod file (`.cotwmod.json`) that lists the new ammunition. **Build** regenerates the mod's `dropzone` from the original game files in a single pass.
- Each build ends with a **dependency report**: for each added item, what it still borrows from an original one (shared, inherited, missing, or its own).

### Reading game files

- The scan no longer stops on an unreadable file. The GUI has new viewers for RTPC and ADF trees, string tables, XLSBook sheets, SARC archives, Havok packfiles, FMOD banks, save files and many smaller COTW formats.
- Decoders for meshes (more vertex formats, materials), skeletons and animations (glTF export, no external tool), collision shapes (Havok Physics 2014, OBJ export), textures (BC1 to BC5 and BC7 decoders rewritten, full AVTX header) and sounds (FSB5 to Ogg Vorbis).
- Save files: owned items, loadouts, weapons, and animal populations, including the Great One flag.
- Measured on 2026-10-18: 185,726 of the game's 185,751 files open in a viewer, and about 96 % of them have a name.

### Naming files and hashes

DECA only applied new names by rescanning the game, which takes about an hour. COTWed DECA names files and internal hashes in an existing project, estimates false positives, and can undo every change:

- file names are generated from known names, archive order and the executable's strings;
- internal names (RTPC properties and nodes, events…) are recovered by targeted searches and stored in [`cotw/dictionnaires/hp_noms.tsv`](cotw/dictionnaires/hp_noms.tsv);
- strings hashed by the running game were captured and added to the dictionaries.
- the debug symbols (PDB) of a 2020 build declare the game's name hashes as named compile-time constants: 7,309 proven names were added to the dictionary.

Every feature and tool is described in the [detailed documentation (French)](docs/fr/README.md).

## Requirements

- Windows 10 or 11, 64-bit
- theHunter: Call of the Wild
- Python **3.12.10**, 64-bit: numba 0.60.0 has no Windows package for Python 3.13 or later
- Git

## Installation

In a Command Prompt (`cmd.exe`, not PowerShell, which blocks the venv activation script by default):

```bat
git clone https://github.com/NouBouzou/COTWed-DECA.git deca
cd deca
py -3.12 -m venv venv
venv\Scripts\activate.bat
pip install -r python\requirements.txt
pip install -e python\deca -e python\deca_gui
```

DECA keeps its projects in a `work` folder next to the repository: `..\work\hp` for COTW.

## Quick start

| Task | How |
|---|---|
| Scan the game | Run `lancer_deca.bat` in the cotw directory, then **File → New Project…** and pick `theHunterCotW_F.exe`. The first scan takes about an hour. |
| Try the GUI without the game | Run `cotw\lancer_demo.bat`. It builds and opens a small fake COTW project in `..\work_demo`. |
| Build an ammo mod | **Tools → Ammo Workshop…**, or `python cotw\construire_mod.py ..\work\hp cotw\mods\test14.cotwmod.json --sortie ..\mods\test14` |
| Load a mod in game | Copy the contents of the build's `dropzone` folder into the game's `dropzone` folder, then set these Steam launch options: `--vfs-fs dropzone --vfs-archive archives_win64 --vfs-archive patch_win64 --vfs-archive dlc_win64 --vfs-fs .` |
| Read a save | **File → Open Save…**, or `python cotw\sauvegarde.py <save file> --projet ..\work\hp --langue en` |
| Use a tool | `python cotw\<tool>.py --help`, with DECA closed |
| Run the tests | `python cotw\tests\test_<name>.py` (they run without the game) |

## Repository layout

| Path | Contents |
|---|---|
| [`python/deca`](python/deca) | DECA's core library, with the fork's fixes and its new format readers and writers |
| [`python/deca_gui`](python/deca_gui) | the GUI (PySide6): file viewers, save viewer, Ammo Workshop |
| [`cotw`](cotw) | command-line tools for COTW: mod building, audits, naming, dumps |
| [`cotw/tests`](cotw/tests) | tests, on generated files and a demo project |
| [`cotw/dictionnaires`](cotw/dictionnaires) | the fork's name dictionaries |
| [`cotw/mods`](cotw/mods) | mod files of the in-game tests test14 to test18 |
| [`resources/deca`](resources/deca) | COTW file names and the string lists of other APEX games, used by the scan to name hashes |
| [`cpp/process_image`](cpp/process_image) | optional native texture decoder; without it DECA decodes textures in Python ([build steps](docs/fr/utilisation.md#décodeur-de-textures-en-c-facultatif)) |
| [`docs/fr`](docs/fr/README.md) | detailed documentation (French) |
| [`docs/deca`](docs/deca/README.md) | DECA's original README, changelog and notes |

The GUI is in English. Tool names, command-line options and messages, code comments and the detailed documentation are in French.

## Credits and license

COTWed DECA started from [DECA](https://github.com/kk49/deca) by Krzysztof Kamieniecki (kk49), `develop` branch, commit `9231177` (2025-12-10). DECA is no longer maintained; this repository does not follow it anymore and only targets theHunter: Call of the Wild.

The Vorbis header tables used to rebuild `.ogg` files come from [python-fsb5](https://github.com/HearthSim/python-fsb5) (MIT).

MIT license, see [LICENSE](LICENSE). DECA's code remains under the copyright of Krzysztof Kamieniecki.
