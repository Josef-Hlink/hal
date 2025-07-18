#!/usr/bin/env python3

from typing import Final
from pathlib import Path

_ROOT = Path("/opt/slippi")
_DATA = Path("~/Data/slippi").expanduser().resolve()

_ISO_PATH = _ROOT / "Super Smash Bros. Melee (USA) (En,Ja) (v1.02).iso"
_EMULATOR_PATH = _ROOT / "Slippi_Online-x86_64-ExiAI.AppImage"
_REPLAYS_PATH = _DATA / "replays"
_DOLPHIN_HOME_PATH = _ROOT / "Dolphin"

ISO_PATH: Final[str] = _ISO_PATH.as_posix()
EMULATOR_PATH: Final[str] = _EMULATOR_PATH.as_posix()
REPLAYS_PATH: Final[str] = _REPLAYS_PATH.as_posix()
DOLPHIN_HOME_PATH: Final[str] = _DOLPHIN_HOME_PATH.as_posix()

EVAL_REPLAY_DIR: Final[str] = "/opt/projects/hal2/replays"
REPO_DIR: Final[str] = "/opt/projects/hal2"
