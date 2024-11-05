from enum import Enum
from typing import Dict
from typing import Final
from typing import Literal
from typing import Tuple

import numpy as np
from melee import Action
from melee import Character
from melee import Stage

VALID_PLAYERS: Final[Tuple[str, str]] = ("p1", "p2")
Player = Literal["p1", "p2"]
PLAYER_1_PORT: Final[int] = 1
PLAYER_2_PORT: Final[int] = 2


# Gamestate
STAGES: Final[Tuple[str, ...]] = (
    "final_destination",
    "battlefield",
    "pokemon_stadium",
    "dreamland",
    "fountain_of_dreams",
    "yoshis_story",
)
CHARACTERS: Final[Tuple[str, ...]] = (
    "mario",
    "fox",
    "cptfalcon",
    "dk",
    "kirby",
    "bowser",
    "link",
    "sheik",
    "ness",
    "peach",
    "popo",
    "pikachu",
    "samus",
    "yoshi",
    "jigglypuff",
    "mewtwo",
    "luigi",
    "marth",
    "zelda",
    "ylink",
    "doc",
    "falco",
    "pichu",
    "gameandwatch",
    "ganondorf",
    "roy",
)


class Character(Enum):
    MARIO = 0x00
    FOX = 0x01
    CPTFALCON = 0x02
    DK = 0x03
    KIRBY = 0x04
    BOWSER = 0x05
    LINK = 0x06
    SHEIK = 0x07
    NESS = 0x08
    PEACH = 0x09
    POPO = 0x0A
    PIKACHU = 0x0C
    SAMUS = 0x0D
    YOSHI = 0x0E
    JIGGLYPUFF = 0x0F
    MEWTWO = 0x10
    LUIGI = 0x11
    MARTH = 0x12
    ZELDA = 0x13
    YLINK = 0x14
    DOC = 0x15
    FALCO = 0x16
    PICHU = 0x17
    GAMEANDWATCH = 0x18
    GANONDORF = 0x19
    ROY = 0x1A


class Stage(Enum):
    FINAL_DESTINATION = 1
    BATTLEFIELD = 2
    POKEMON_STADIUM = 3
    DREAMLAND = 4
    FOUNTAIN_OF_DREAMS = 5
    YOSHIS_STORY = 6


###################
# Gamestate      #
###################

INCLUDED_STAGES: Tuple[str, ...] = (
    "FINAL_DESTINATION",
    "BATTLEFIELD",
    "POKEMON_STADIUM",
    "DREAMLAND",
    "FOUNTAIN_OF_DREAMS",
    "YOSHIS_STORY",
)
IDX_BY_STAGE: Dict[Stage, int] = {
    stage: i for i, stage in enumerate(stage for stage in Stage if stage.name in INCLUDED_STAGES)
}
IDX_BY_STAGE_STR: Dict[str, int] = {stage.name: i for stage, i in IDX_BY_STAGE.items()}
STAGE_BY_IDX: Dict[int, str] = {i: stage.name for stage, i in IDX_BY_STAGE.items()}

INCLUDED_CHARACTERS: Tuple[str, ...] = (
    "MARIO",
    "FOX",
    "CPTFALCON",
    "DK",
    "KIRBY",
    "BOWSER",
    "LINK",
    "SHEIK",
    "NESS",
    "PEACH",
    "POPO",
    "PIKACHU",
    "SAMUS",
    "YOSHI",
    "JIGGLYPUFF",
    "MEWTWO",
    "LUIGI",
    "MARTH",
    "ZELDA",
    "YLINK",
    "DOC",
    "FALCO",
    "PICHU",
    "GAMEANDWATCH",
    "GANONDORF",
    "ROY",
)
IDX_BY_CHARACTER: Dict[Character, int] = {
    char: i for i, char in enumerate(char for char in Character if char.name in INCLUDED_CHARACTERS)
}
IDX_BY_CHARACTER_STR: Dict[str, int] = {char.name: i for char, i in IDX_BY_CHARACTER.items()}
CHARACTER_BY_IDX: Dict[int, str] = {i: char.name for char, i in IDX_BY_CHARACTER.items()}

IDX_BY_ACTION: Dict[Action, int] = {action: i for i, action in enumerate(Action)}
ACTION_BY_IDX: Dict[int, str] = {i: action.name for action, i in IDX_BY_ACTION.items()}

INCLUDED_BUTTONS: Tuple[str, ...] = (
    "BUTTON_A",
    "BUTTON_B",
    "BUTTON_X",
    "BUTTON_Z",
    "BUTTON_L",
    "NO_BUTTON",
)


###################
# Embeddings      #
###################

REPLAY_UUID: Tuple[str] = ("replay_uuid",)
FRAME: Tuple[str] = ("frame",)
STAGE: Tuple[str, ...] = ("stage",)
PLAYER_INPUT_FEATURES_TO_EMBED: Tuple[str, ...] = ("character", "action")
PLAYER_INPUT_FEATURES_TO_NORMALIZE: Tuple[str, ...] = (
    "percent",
    "stock",
    "facing",
    "invulnerable",
    "jumps_left",
    "on_ground",
)
PLAYER_INPUT_FEATURES_TO_INVERT_AND_NORMALIZE: Tuple[str, ...] = ("shield_strength",)
PLAYER_POSITION: Tuple[str, ...] = (
    "position_x",
    "position_y",
)
# Optional input features
PLAYER_ACTION_FRAME_FEATURES: Tuple[str, ...] = (
    "action_frame",
    "hitlag_left",
    "hitstun_left",
)
PLAYER_SPEED_FEATURES: Tuple[str, ...] = (
    "speed_air_x_self",
    "speed_y_self",
    "speed_x_attack",
    "speed_y_attack",
    "speed_ground_x_self",
)
PLAYER_ECB_FEATURES: Tuple[str, ...] = (
    "ecb_bottom_x",
    "ecb_bottom_y",
    "ecb_top_x",
    "ecb_top_y",
    "ecb_left_x",
    "ecb_left_y",
    "ecb_right_x",
    "ecb_right_y",
)
# Target features
TARGET_FEATURES_TO_ONE_HOT_ENCODE: Tuple[str, ...] = (
    "a",
    "b",
    "x",
    "z",
    "l",
    "no_button",
)

STICK_XY_CLUSTER_CENTERS_V0: np.ndarray = np.array(
    [
        [0.5, 0.5],
        [1.0, 0.5],
        [0.0, 0.5],
        [0.50, 0.0],
        [0.50, 1.0],
        [0.50, 0.25],
        [0.50, 0.75],
        [0.75, 0.5],
        [0.25, 0.5],
        [0.15, 0.15],
        [0.85, 0.15],
        [0.85, 0.85],
        [0.15, 0.85],
        [0.28, 0.93],
        [0.28, 0.07],
        [0.72, 0.07],
        [0.72, 0.93],
        [0.07, 0.28],
        [0.07, 0.72],
        [0.93, 0.72],
        [0.93, 0.28],
    ]
)
