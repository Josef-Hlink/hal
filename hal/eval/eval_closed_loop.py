import argparse
import concurrent.futures
import sys
import time
from multiprocessing.synchronize import Event as EventType
from pathlib import Path
from typing import Generator
from typing import List
from typing import Optional

import attr
import melee
import torch
import torch.multiprocessing as mp
from loguru import logger
from tensordict import TensorDict

from hal.constants import PLAYER_1_PORT
from hal.constants import PLAYER_2_PORT
from hal.constants import Player
from hal.constants import get_opponent
from hal.eval.emulator_helper import console_manager
from hal.eval.emulator_helper import find_open_udp_ports
from hal.eval.emulator_helper import get_console_kwargs
from hal.eval.emulator_helper import get_replay_dir
from hal.eval.emulator_helper import self_play_menu_helper
from hal.eval.emulator_paths import REMOTE_CISO_PATH
from hal.eval.eval_helper import EpisodeStats
from hal.eval.eval_helper import mock_framedata_as_tensordict
from hal.eval.eval_helper import mock_preds_as_tensordict
from hal.eval.eval_helper import send_controller_inputs
from hal.eval.eval_helper import share_and_pin_memory
from hal.gamestate_utils import extract_gamestate_as_tensordict
from hal.training.config import TrainConfig
from hal.training.io import load_config_from_artifact_dir
from hal.training.io import load_model_from_artifact_dir
from hal.training.preprocess.preprocessor import Preprocessor


def setup_cpu_logger(debug: bool = False) -> None:
    logger.remove()
    logger_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "rank={extra[rank]} - <level>{message}</level>"
    )
    logger.configure(extra={"rank": ""})  # Default values
    logger.add(sys.stderr, format=logger_format, level="DEBUG" if debug else "INFO")


def _get_port(player: Player) -> int:
    return PLAYER_1_PORT if player == "p1" else PLAYER_2_PORT


@attr.s(auto_attribs=True)
class EmulatorManager:
    udp_port: int
    player: Player
    opponent_cpu_level: int = 9
    replay_dir: Path | None = None
    episode_stats: EpisodeStats = EpisodeStats()
    max_steps: int = 8 * 60 * 60
    latency_warning_threshold: float = 14.0
    console_timeout: float = 2.0
    enable_ffw: bool = True
    debug: bool = False

    def __attrs_post_init__(self) -> None:
        self.console_logger = melee.Logger() if self.debug else None
        console_kwargs = get_console_kwargs(
            enable_ffw=self.enable_ffw,
            udp_port=self.udp_port,
            replay_dir=self.replay_dir,
            console_logger=self.console_logger,
        )
        self.console = melee.Console(**console_kwargs)
        self.ego_controller = melee.Controller(
            console=self.console, port=_get_port(self.player), type=melee.ControllerType.STANDARD
        )
        self.opponent_controller = melee.Controller(
            console=self.console, port=_get_port(get_opponent(self.player)), type=melee.ControllerType.STANDARD
        )
        logger.info(f"Emu manager initialized for {attr.asdict(self)}")

    def gamestate_generator(self) -> Generator[melee.GameState, TensorDict, None]:
        """Generator that yields gamestates and receives controller inputs.

        Yields:
            Optional[melee.GameState]: The current game state, or None if the episode is over

        Sends:
            TensorDict: Controller inputs to be applied to the game
        """
        # Run the console
        self.console.run(iso_path=REMOTE_CISO_PATH)  # Do not pass dolphin_user_path to avoid overwriting init kwargs
        # Connect to the console
        logger.debug("Connecting to console...")
        if not self.console.connect():
            logger.debug("ERROR: Failed to connect to the console.")
            sys.exit(-1)
        logger.debug("Console connected")

        # Plug our controller in
        #   Due to how named pipes work, this has to come AFTER running dolphin
        #   NOTE: If you're loading a movie file, don't connect the controller,
        #   dolphin will hang waiting for input and never receive it
        logger.debug("Connecting controller 1 to console...")
        if not self.ego_controller.connect():
            logger.debug("ERROR: Failed to connect the controller.")
            sys.exit(-1)
        logger.debug("Controller 1 connected")
        logger.debug("Connecting controller 2 to console...")
        if not self.opponent_controller.connect():
            logger.debug("ERROR: Failed to connect the controller.")
            sys.exit(-1)
        logger.debug("Controller 2 connected")

        i = 0
        match_started = False

        # Wrap console manager inside a thread for timeouts
        # Important that console manager context goes second to gracefully handle keyboard interrupts, timeouts, and all other exceptions
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor, console_manager(
            console=self.console, console_logger=self.console_logger
        ):
            logger.debug("Starting episode")
            while i < self.max_steps:
                # Wrap `console.step()` in a thread with timeout
                future = executor.submit(self.console.step)
                try:
                    gamestate = future.result(timeout=self.console_timeout)
                except concurrent.futures.TimeoutError:
                    logger.error("console.step() timed out")
                    raise

                if gamestate is None:
                    logger.info("Gamestate is None")
                    break

                if self.console.processingtime * 1000 > self.latency_warning_threshold:
                    logger.debug("Last frame took " + str(self.console.processingtime * 1000) + "ms to process.")
                # logger.debug(f"Got gamestate for frame {gamestate.frame if gamestate else 'None'}")

                if gamestate.menu_state not in [melee.Menu.IN_GAME, melee.Menu.SUDDEN_DEATH]:
                    if match_started:
                        logger.debug("Match ended")
                        break

                    self_play_menu_helper(
                        gamestate=gamestate,
                        controller_1=self.ego_controller,
                        controller_2=self.opponent_controller,
                        # TODO: select characters programmatically
                        character_1=melee.Character.FOX,
                        character_2=melee.Character.FOX,
                        stage_selected=melee.Stage.BATTLEFIELD,
                        opponent_cpu_level=self.opponent_cpu_level,
                    )
                else:
                    if not match_started:
                        match_started = True
                        logger.debug("Match started")

                    # Yield gamestate and receive controller inputs
                    # logger.debug(f"Yielding gamestate {i}")
                    controller_inputs = yield gamestate
                    # logger.debug(f"Controller inputs: {controller_inputs}")
                    if controller_inputs is None:
                        logger.error("Controller inputs are None")
                    else:
                        # logger.debug("Sending controller inputs")
                        send_controller_inputs(self.ego_controller, controller_inputs)

                    self.episode_stats.update(gamestate)
                    if self.console_logger is not None:
                        self.console_logger.writeframe()
                    i += 1


def cpu_worker(
    shared_batched_model_input: TensorDict,
    shared_batched_model_output: TensorDict,
    rank: int,
    port: int,
    player: Player,
    replay_dir: Path,
    preprocessor: Preprocessor,
    model_input_ready_flag: EventType,
    model_output_ready_flag: EventType,
    stop_event: EventType,
    episode_stats_queue: mp.Queue,
    enable_ffw: bool = True,
    debug: bool = False,
) -> None:
    """
    CPU worker that preprocesses data, writes it into shared memory,
    and sends controller inputs to the emulator from model predictions.
    """
    setup_cpu_logger(debug=debug)

    with logger.contextualize(rank=rank):
        try:
            emulator_manager = EmulatorManager(
                rank=rank,
                udp_port=port,
                player=player,
                replay_dir=replay_dir,
                enable_ffw=enable_ffw,
                debug=debug,
            )
            gamestate_generator = emulator_manager.gamestate_generator()
            last_controller_inputs = None  # Store previous inputs

            for i, gamestate in enumerate(gamestate_generator):
                if gamestate is None:
                    stop_event.set()
                    break

                preprocess_start = time.perf_counter()
                gamestate_td = extract_gamestate_as_tensordict(gamestate)
                model_inputs = preprocessor.preprocess_inputs(gamestate_td, player)

                # # TODO refactor this into preprocessor
                # if last_controller_inputs is not None:
                #     model_inputs.update(
                #         {
                #             "main_stick": last_controller_inputs["main_stick_onehot"],
                #             "c_stick": last_controller_inputs["c_stick_onehot"],
                #             "buttons": last_controller_inputs["button_onehot"],
                #         }
                #     )

                preprocess_time = time.perf_counter() - preprocess_start

                transfer_start = time.perf_counter()
                sharded_model_input: TensorDict = shared_batched_model_input[rank]
                # Update our rank of the shared buffer with the last frame
                sharded_model_input.update_(model_inputs[-1], non_blocking=True)
                transfer_time = time.perf_counter() - transfer_start

                if debug and i % 60 == 0:
                    logger.debug(f"Preprocess: {preprocess_time*1000:.2f}ms, Transfer: {transfer_time*1000:.2f}ms")

                model_input_ready_flag.set()

                # Wait for the output to be ready
                while not model_output_ready_flag.is_set() and not stop_event.is_set():
                    time.sleep(0.0001)  # Sleep briefly to avoid busy waiting

                if stop_event.is_set():
                    break

                # Read the output and store one-hot encodings for next iteration
                model_output = shared_batched_model_output[rank].clone()
                # TODO refactor this into some eval helper class
                last_controller_inputs = preprocessor.postprocess_preds(model_output)
                gamestate_generator.send(last_controller_inputs)

                # Clear the output ready flag for the next iteration
                model_output_ready_flag.clear()

            logger.success(f"CPU worker {rank} complete.")
        except Exception as e:
            logger.error(f"CPU worker {rank} encountered an error: {e}")
        finally:
            model_input_ready_flag.set()
            stop_event.set()
            logger.debug(f"CPU worker {rank} episode stats: {emulator_manager.episode_stats}")
            episode_stats_queue.put(emulator_manager.episode_stats)
            logger.info(f"CPU worker {rank} stopped")


def gpu_worker(
    shared_batched_model_input_B: TensorDict,  # (n_workers,)
    shared_batched_model_output_B: TensorDict,  # (n_workers,)
    model_input_ready_flags: List[EventType],
    model_output_ready_flags: List[EventType],
    seq_len: int,
    stop_events: List[EventType],
    artifact_dir: Path,
    device: torch.device | str,
    checkpoint_idx: Optional[int] = None,
    cpu_flag_timeout: float = 5.0,
) -> None:
    """
    GPU worker that batches data from shared memory, updates the context window,
    performs inference with model, and writes output back to shared memory.
    """
    torch.set_float32_matmul_precision("high")
    model, _ = load_model_from_artifact_dir(Path(artifact_dir), idx=checkpoint_idx)
    model.eval()
    model.to(device)

    # Stack along time dimension
    # shape: (n_workers, seq_len)
    context_window_BL: TensorDict = torch.stack([shared_batched_model_input_B for _ in range(seq_len)], dim=-1).to(device)  # type: ignore
    logger.info(f"Context window shape: {context_window_BL.shape}, device: {context_window_BL.device}")

    # Warmup CUDA graphs with dummy inputs
    logger.info("Compiling model...")
    model = torch.compile(model, mode="default")
    with torch.no_grad():
        model(context_window_BL)
    logger.info("Warmup step finished")

    iteration = 0
    while not all(event.is_set() for event in stop_events):
        iteration_start = time.perf_counter()

        # Wait for all CPU workers to signal that data is ready
        flag_wait_start = time.perf_counter()
        for i, (input_flag, stop_event) in enumerate(zip(model_input_ready_flags, stop_events)):
            while not input_flag.is_set() and not stop_event.is_set():
                if not input_flag.is_set() and time.perf_counter() - flag_wait_start > cpu_flag_timeout:
                    logger.warning(f"CPU worker {i} input flag wait took too long, stopping episode")
                    input_flag.set()
                    stop_event.set()
                time.sleep(0.0001)  # Sleep briefly to avoid busy waiting

        if all(event.is_set() for event in stop_events):
            break

        # Update the context window by rolling and adding new data
        transfer_start = time.perf_counter()
        context_window_BL[:, :-1].copy_(context_window_BL[:, 1:].clone())
        context_window_BL[:, -1].copy_(shared_batched_model_input_B, non_blocking=True)
        transfer_time = time.perf_counter() - transfer_start

        inference_start = time.perf_counter()
        with torch.no_grad():
            outputs_B: TensorDict = model(context_window_BL)[:, -1]  # (n_workers,)
        inference_time = time.perf_counter() - inference_start

        writeback_start = time.perf_counter()
        # Write last frame of model preds to shared buffer
        shared_batched_model_output_B.copy_(outputs_B)
        writeback_time = time.perf_counter() - writeback_start

        total_time = time.perf_counter() - iteration_start

        if iteration % 60 == 0:
            logger.debug(
                f"Iteration {iteration}: Total: {total_time*1000:.2f}ms "
                f"(Transfer: {transfer_time*1000:.2f}ms, "
                f"Inference: {inference_time*1000:.2f}ms, "
                f"Writeback: {writeback_time*1000:.2f}ms)"
            )

        iteration += 1

        # Signal to CPU workers that output is ready
        for output_flag in model_output_ready_flags:
            output_flag.set()

        # Clear model_input_ready_flags for the next iteration
        for input_flag in model_input_ready_flags:
            input_flag.clear()


def run_closed_loop_evaluation(
    artifact_dir: Path,
    n_workers: int,
    checkpoint_idx: Optional[int] = None,
    eval_stats_queue: Optional[mp.Queue] = None,
    player: Player = "p1",
    enable_ffw: bool = False,  # disable by default for emulator stability, TODO debug EXI inputs
    debug: bool = False,
) -> None:
    try:
        mp.set_start_method("spawn")
    except RuntimeError:
        pass
    device: torch.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    train_config: TrainConfig = load_config_from_artifact_dir(artifact_dir)
    preprocessor = Preprocessor(data_config=train_config.data, embedding_config=train_config.embedding)

    # Create events to signal when cpu and gpu workers are ready
    model_input_ready_flags: List[EventType] = [mp.Event() for _ in range(n_workers)]
    model_output_ready_flags: List[EventType] = [mp.Event() for _ in range(n_workers)]
    # Create events to signal when emulator episodes end
    stop_events: List[EventType] = [mp.Event() for _ in range(n_workers)]

    # Share and pin buffers in CPU memory for transferring model inputs and outputs
    # TODO: figure out padding for start of episode
    mock_framedata_L = mock_framedata_as_tensordict(preprocessor.trajectory_sampling_len)
    # Store only a single time step to minimize copying
    mock_model_inputs_ = preprocessor.preprocess_inputs(mock_framedata_L, player)[-1]
    # batch_size == n_workers
    shared_batched_model_input_B: TensorDict = torch.stack(
        [mock_model_inputs_ for _ in range(n_workers)], dim=0  # type: ignore
    )
    shared_batched_model_input_B = share_and_pin_memory(shared_batched_model_input_B)
    shared_batched_model_output_B: TensorDict = torch.stack(
        [mock_preds_as_tensordict(train_config.embedding) for _ in range(n_workers)], dim=0  # type: ignore
    )
    shared_batched_model_output_B = share_and_pin_memory(shared_batched_model_output_B)

    gpu_process: mp.Process = mp.Process(
        target=gpu_worker,
        kwargs={
            "shared_batched_model_input_B": shared_batched_model_input_B,
            "shared_batched_model_output_B": shared_batched_model_output_B,
            "model_input_ready_flags": model_input_ready_flags,
            "model_output_ready_flags": model_output_ready_flags,
            "seq_len": preprocessor.seq_len,
            "stop_events": stop_events,
            "artifact_dir": artifact_dir,
            "device": device,
            "checkpoint_idx": checkpoint_idx,
        },
    )
    gpu_process.start()

    cpu_processes: List[mp.Process] = []
    ports = find_open_udp_ports(n_workers)
    episode_stats_queue: mp.Queue = mp.Queue()
    replay_dir = get_replay_dir(artifact_dir, step=checkpoint_idx)
    logger.info(f"Replays will be saved to {replay_dir}")
    for i in range(n_workers):
        p: mp.Process = mp.Process(
            target=cpu_worker,
            kwargs={
                "shared_batched_model_input": shared_batched_model_input_B,
                "shared_batched_model_output": shared_batched_model_output_B,
                "rank": i,
                "port": ports[i],
                "player": player,
                "replay_dir": replay_dir,
                "preprocessor": preprocessor,
                "model_input_ready_flag": model_input_ready_flags[i],
                "model_output_ready_flag": model_output_ready_flags[i],
                "stop_event": stop_events[i],
                "episode_stats_queue": episode_stats_queue,
                "enable_ffw": enable_ffw,
                "debug": debug,
            },
        )
        cpu_processes.append(p)
        p.start()

    gpu_process.join()

    for p in cpu_processes:
        p.join()

    episode_stats: List[EpisodeStats] = []
    while not episode_stats_queue.empty():
        episode_stats.append(episode_stats_queue.get())
    total_stats = sum(episode_stats, EpisodeStats(episodes=0))
    logger.info(f"Closed loop evaluation stats: {total_stats}")
    logger.info("Closed loop evaluation complete")

    if eval_stats_queue is not None:
        eval_stats_queue.put(total_stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Melee in emulator")
    parser.add_argument("--model_dir", type=str, help="Path to model directory")
    parser.add_argument("--n_workers", type=int, help="Number of CPU workers")
    parser.add_argument("--enable_ffw", action="store_true", help="Enable fast forward mode")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()
    run_closed_loop_evaluation(
        artifact_dir=Path(args.model_dir),
        n_workers=args.n_workers,
        enable_ffw=args.enable_ffw,
        debug=args.debug,
    )
