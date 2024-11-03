import argparse
import sys
from collections import defaultdict
from collections import deque
from pathlib import Path
from typing import DefaultDict
from typing import Optional
from typing import Sequence

import melee
import torch
import torch.multiprocessing as mp
from loguru import logger
from tensordict import TensorDict

from hal.data.schema import PYARROW_DTYPE_BY_COLUMN
from hal.data.stats import load_dataset_stats
from hal.eval.emulator_helper import console_manager
from hal.eval.emulator_helper import get_console_kwargs
from hal.eval.emulator_helper import self_play_menu_helper
from hal.eval.emulator_paths import REMOTE_CISO_PATH
from hal.eval.emulator_paths import REMOTE_DOLPHIN_HOME_PATH
from hal.eval.eval_helper import extract_and_append_gamestate
from hal.eval.eval_helper import send_controller_inputs
from hal.training.io import load_model_from_artifact_dir
from hal.training.preprocess.registry import InputPreprocessRegistry
from hal.training.preprocess.registry import OutputProcessingRegistry

mp.set_start_method("spawn", force=True)

PLAYER_1_PORT = 1
PLAYER_2_PORT = 2


def get_mock_framedata(seq_len: int) -> TensorDict:
    """Mock frame data for warming up compiled model."""
    return TensorDict({k: torch.zeros(seq_len) for k in PYARROW_DTYPE_BY_COLUMN}, batch_size=(seq_len,))


def convert_frame_data_to_tensor_dict(frame_data: DefaultDict[str, Sequence]) -> TensorDict:
    return TensorDict({k: torch.tensor(v) for k, v in frame_data.items()}, batch_size=(len(frame_data["frame"])))


def pad_tensors(td: TensorDict, length: int) -> TensorDict:
    """For models with fixed input length, pad with zeros.

    Assumes tensors are of shape (T, D)."""
    if td.shape[0] < length:
        pad_size = length - td.shape[0]
        return TensorDict({k: torch.nn.functional.pad(v, (pad_size, 0)) for k, v in td.items()}, batch_size=(length,))
    return td


def model_server(
    input_queue: mp.Queue,
    output_queue: mp.Queue,
    model_dir: str,
    idx: Optional[int] = None,
) -> None:
    """Background worker process for model inference."""
    model, train_config = load_model_from_artifact_dir(Path(model_dir), idx=idx)
    model.eval()

    preprocess_inputs = InputPreprocessRegistry.get(train_config.embedding.input_preprocessing_fn)
    stats_by_feature_name = load_dataset_stats(train_config.data.stats_path)
    postprocess_outputs = OutputProcessingRegistry.get(train_config.embedding.target_preprocessing_fn)

    logger.info("Compiling model...")
    model = model.to("cuda")
    model = torch.compile(model, mode="default")
    mock_tensordict = get_mock_framedata(train_config.data.input_len)
    mock_inputs = (
        preprocess_inputs(mock_tensordict, train_config.data, "p1", stats_by_feature_name).unsqueeze(0).to("cuda")
    )
    with torch.no_grad():
        model(mock_inputs)[:, -1]

    frame_data: DefaultDict[str, deque] = defaultdict(lambda: deque(maxlen=train_config.data.input_len))

    while True:
        gamestate = input_queue.get()
        if gamestate is None:  # Sentinel value to stop the worker
            break

        extract_and_append_gamestate(gamestate=gamestate, frame_data=frame_data)
        frame_data_td = convert_frame_data_to_tensor_dict(frame_data)
        model_inputs = pad_tensors(frame_data_td, train_config.data.input_len)
        model_inputs = preprocess_inputs(model_inputs, train_config.data, "p1", stats_by_feature_name)
        model_inputs = model_inputs.unsqueeze(0).to("cuda")

        with torch.no_grad():
            outputs: TensorDict = model(model_inputs)[:, -1].to("cpu")
        controller_inputs = postprocess_outputs(outputs)
        output_queue.put(controller_inputs)


def run_episode(model_dir: str, no_gui: bool = True, idx: Optional[int] = None) -> None:
    input_queue = mp.Queue()
    output_queue = mp.Queue()

    # Start ML worker process
    ml_process = mp.Process(
        target=model_server,
        args=(input_queue, output_queue, model_dir, idx),
    )
    ml_process.start()

    console_kwargs = get_console_kwargs(no_gui=no_gui)
    console = melee.Console(**console_kwargs)

    controller_1 = melee.Controller(console=console, port=PLAYER_1_PORT, type=melee.ControllerType.STANDARD)
    controller_2 = melee.Controller(console=console, port=PLAYER_2_PORT, type=melee.ControllerType.STANDARD)

    # Run the console
    console.run(iso_path=REMOTE_CISO_PATH, dolphin_user_path=REMOTE_DOLPHIN_HOME_PATH)
    # Connect to the console
    logger.info("Connecting to console...")
    if not console.connect():
        logger.info("ERROR: Failed to connect to the console.")
        sys.exit(-1)
    logger.info("Console connected")

    # Plug our controller in
    #   Due to how named pipes work, this has to come AFTER running dolphin
    #   NOTE: If you're loading a movie file, don't connect the controller,
    #   dolphin will hang waiting for input and never receive it
    logger.info("Connecting controller 1 to console...")
    if not controller_1.connect():
        logger.info("ERROR: Failed to connect the controller.")
        sys.exit(-1)
    logger.info("Controller 1 connected")
    logger.info("Connecting controller 2 to console...")
    if not controller_2.connect():
        logger.info("ERROR: Failed to connect the controller.")
        sys.exit(-1)
    logger.info("Controller 2 connected")

    i = 0
    match_started = False
    with console_manager(console):
        logger.info("Starting episode")
        try:
            while i < 10000:
                gamestate = console.step()
                if gamestate is None:
                    logger.info("Gamestate is None")
                    break

                if console.processingtime * 1000 > 12:
                    logger.info("WARNING: Last frame took " + str(console.processingtime * 1000) + "ms to process.")

                if gamestate.menu_state not in [melee.Menu.IN_GAME, melee.Menu.SUDDEN_DEATH]:
                    if match_started:
                        break

                    self_play_menu_helper(
                        gamestate=gamestate,
                        controller_1=controller_1,
                        controller_2=controller_2,
                        character_1=melee.Character.FOX,
                        character_2=melee.Character.FOX,
                        stage_selected=melee.Stage.BATTLEFIELD,
                    )
                else:
                    if not match_started:
                        match_started = True

                    # Send gamestate to worker process
                    input_queue.put(gamestate)

                    # Get controller inputs from worker process
                    controller_inputs = output_queue.get()
                    send_controller_inputs(controller_1, controller_inputs)

                    i += 1
        finally:
            # Clean up worker process
            input_queue.put(None)  # Send sentinel value
            ml_process.join(timeout=1.0)
            if ml_process.is_alive():
                ml_process.terminate()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Melee in emulator")
    parser.add_argument("--no-gui", action="store_true", help="Run without GUI")
    parser.add_argument("--debug", action="store_true", help="Run with debug mode")
    parser.add_argument("--model_dir", type=str, help="Path to model directory")
    args = parser.parse_args()
    run_episode(model_dir=args.model_dir, no_gui=args.no_gui)
