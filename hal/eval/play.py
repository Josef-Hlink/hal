import argparse
import concurrent.futures
import sys
from pathlib import Path

import melee
import torch
from gamestate_utils import extract_gamestate_as_tensordict
from loguru import logger
from tensordict import TensorDict

from hal.emulator_helper import console_manager
from hal.emulator_helper import get_gui_console_kwargs
from hal.emulator_helper import self_play_menu_helper
from hal.emulator_helper import send_controller_inputs
from hal.eval.eval_helper import mock_framedata_as_tensordict
from hal.preprocess.preprocessor import Preprocessor
from hal.training.config import TrainConfig
from hal.training.io import load_config_from_artifact_dir
from hal.training.io import load_model_from_artifact_dir

EMULATOR_PATH = "/Users/ericgu/Library/Application Support/Slippi Launcher/netplay"
CISO_PATH = "/Users/ericgu/data/ssbm/ssbm.ciso"
BOT_PLAYER = "p1"


def load_model(artifact_dir: str, device: torch.device) -> torch.nn.Module:
    torch.set_float32_matmul_precision("high")
    model, _ = load_model_from_artifact_dir(Path(artifact_dir), device=device)
    model.eval()
    model.to(device)
    return model


def play(artifact_dir: str):
    device: torch.device = torch.device(
        "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    )
    train_config: TrainConfig = load_config_from_artifact_dir(Path(artifact_dir))
    preprocessor = Preprocessor(data_config=train_config.data)
    seq_len = preprocessor.seq_len

    model = load_model(artifact_dir, device)

    context_window_BL: TensorDict = mock_framedata_as_tensordict(preprocessor.trajectory_sampling_len).unsqueeze(0)
    context_window_BL = context_window_BL.to(device)
    logger.info(f"Context window shape: {context_window_BL.shape}, device: {context_window_BL.device}")

    # Warmup CUDA graphs with dummy inputs
    logger.info("Compiling model...")
    model = torch.compile(model, mode="default")
    with torch.no_grad():
        model(context_window_BL)
    logger.info("Warmup step finished")

    console_kwargs = get_gui_console_kwargs(EMULATOR_PATH)
    console = melee.Console(**console_kwargs)
    ego_controller = melee.Controller(console=console, port=1, type=melee.ControllerType.STANDARD)
    opponent_controller = melee.Controller(console=console, port=2, type=melee.ControllerType.STANDARD)
    console.run(iso_path=CISO_PATH)  # Do not pass dolphin_user_path to avoid overwriting init kwargs
    # Connect to the console
    logger.debug("Connecting to console...")
    if not console.connect():
        logger.debug("ERROR: Failed to connect to the console.")
        sys.exit(-1)
    logger.debug("Console connected")

    # Plug our controller in
    #   Due to how named pipes work, this has to come AFTER running dolphin
    #   NOTE: If you're loading a movie file, don't connect the controller,
    #   dolphin will hang waiting for input and never receive it
    logger.debug("Connecting controller 1 to console...")
    if not ego_controller.connect():
        logger.debug("ERROR: Failed to connect the controller.")
        sys.exit(-1)
    logger.debug("Controller 1 connected")
    logger.debug("Connecting controller 2 to console...")
    if not opponent_controller.connect():
        logger.debug("ERROR: Failed to connect the controller.")
        sys.exit(-1)
    logger.debug("Controller 2 connected")

    i = 0
    match_started = False

    # Wrap console manager inside a thread for timeouts
    # Important that console manager context goes second to gracefully handle keyboard interrupts, timeouts, and all other exceptions
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor, console_manager(console=console):
        logger.debug("Starting episode")
        while i < 30000:
            # Wrap `console.step()` in a thread with timeout
            future = executor.submit(console.step)
            try:
                gamestate = future.result(timeout=5.0)
            except concurrent.futures.TimeoutError:
                logger.error("console.step() timed out")
                raise

            if gamestate is None:
                logger.info("Gamestate is None")
                continue

            if console.processingtime * 1000 > 1400:
                logger.debug("Last frame took " + str(console.processingtime * 1000) + "ms to process.")

            if gamestate.menu_state not in [melee.Menu.IN_GAME, melee.Menu.SUDDEN_DEATH]:
                if match_started:
                    logger.debug("Match ended")
                    break

                self_play_menu_helper(
                    gamestate=gamestate,
                    controller_1=ego_controller,
                    controller_2=opponent_controller,
                    character_1=melee.Character.FOX,
                    character_2=melee.Character.FOX,
                    stage_selected=melee.Stage.BATTLEFIELD,
                    opponent_cpu_level=9,
                    # character_2=None,  # allow human player to select character
                    # stage_selected=None,  # allow human player to select stage
                    # opponent_cpu_level=0,  # human
                )
            else:
                if not match_started:
                    match_started = True
                    logger.debug("Match started")

                # Yield gamestate and receive controller inputs
                # logger.debug(f"Yielding gamestate {i}")
                gamestate_td = extract_gamestate_as_tensordict(gamestate)
                model_inputs = preprocessor.preprocess_inputs(gamestate_td, BOT_PLAYER)

                if i < seq_len:
                    # While context window is not full, fill in from the left
                    context_window_BL[:, i].copy_(model_inputs, non_blocking=True)
                else:
                    # Update the context window by rolling frame data left and adding new data on the right
                    context_window_BL[:, :-1].copy_(context_window_BL[:, 1:].clone())
                    context_window_BL[:, -1].copy_(model_inputs, non_blocking=True)

                seq_idx = min(seq_len - 1, i)
                model_outputs_B = model(model_inputs)[:, seq_idx]
                controller_inputs = preprocessor.postprocess_preds(model_outputs_B)
                if controller_inputs is None:
                    logger.error("Controller inputs are None")
                else:
                    # logger.debug("Sending controller inputs")
                    send_controller_inputs(ego_controller, controller_inputs)

                i += 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact_dir", type=str, required=True)
    args = parser.parse_args()
    play(args.artifact_dir)
