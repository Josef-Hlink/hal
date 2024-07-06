# %%
import time
from typing import Dict

import numpy as np
import pyarrow as pa
from pyarrow import parquet as pq

from hal.data.stats import FeatureStats
from hal.data.stats import load_dataset_stats

np.set_printoptions(threshold=np.inf)


INPUT_FEATURES_TO_EMBED = ("stage", "character", "action")
INPUT_FEATURES_TO_NORMALIZE = ("percent", "stock", "facing", "action_frame", "invulnerable", "jumps_left", "on_ground")
INPUT_FEATURES_TO_INVERT_AND_NORMALIZE = ("shield_strength",)
INPUT_FEATURES_TO_STANDARDIZE = (
    "position_x",
    "position_y",
    "hitlag_left",
    "hitstun_left",
    "speed_air_x_self",
    "speed_y_self",
    "speed_x_attack",
    "speed_y_attack",
    "speed_ground_x_self",
)

TARGET_FEATURES_TO_ONE_HOT_ENCODE = ("button_a", "button_b", "button_x", "button_z", "button_l")


def pyarrow_table_to_np_dict(table: pa.Table) -> Dict[str, np.ndarray]:
    """Convert pyarrow table to dictionary of numpy arrays."""
    return {name: col.to_numpy() for name, col in zip(table.column_names, table.columns)}


def normalize(array: np.ndarray, stats: FeatureStats) -> np.ndarray:
    """Normalize feature [0, 1]."""
    return (array - stats.min) / (stats.max - stats.min)


def invert_and_normalize(array: np.ndarray, stats: FeatureStats) -> np.ndarray:
    """Invert and normalize feature to [0, 1]."""
    return (stats.max - array) / (stats.max - stats.min)


def standardize(array: np.ndarray, stats: FeatureStats) -> np.ndarray:
    """Standardize feature to mean 0 and std 1."""
    return (array - stats.mean) / stats.std


def union(array_1: np.ndarray, array_2: np.ndarray) -> np.ndarray:
    """Perform logical OR of two features."""
    return array_1 | array_2


def convert_target_button_to_one_hot(array: np.ndarray) -> np.ndarray:
    """
    Vectorized version to clean up overlapping button presses by keeping the latest one.
    """
    T, D = array.shape
    one_hot = np.zeros((T, D + 1))

    # Find all button presses
    button_presses = np.argwhere(array == 1)

    # Group button presses by time step
    unique_times, inverse_indices = np.unique(button_presses[:, 0], return_inverse=True)

    # Count number of button presses at each time step
    press_counts = np.bincount(inverse_indices)

    # Handle cases with no button presses
    no_press_mask = np.ones(T, dtype=bool)
    no_press_mask[unique_times] = False
    one_hot[no_press_mask, -1] = 1

    # Handle cases with single button press
    single_press_mask = press_counts == 1
    single_press_times = unique_times[single_press_mask]
    single_press_buttons = button_presses[np.isin(button_presses[:, 0], single_press_times), 1]
    one_hot[single_press_times, single_press_buttons] = 1

    # Handle cases with multiple button presses
    multi_press_mask = press_counts > 1
    multi_press_times = unique_times[multi_press_mask]

    if len(multi_press_times) > 0:
        # Find the first new button press for each multi-press time step
        multi_press_buttons = [button_presses[button_presses[:, 0] == t, 1] for t in multi_press_times]
        held_buttons = np.zeros(D, dtype=bool)
        for t, buttons in zip(multi_press_times, multi_press_buttons):
            new_buttons = buttons[~held_buttons[buttons]]
            if len(new_buttons) > 0:
                one_hot[t, new_buttons[0]] = 1
            held_buttons[buttons] = True

    return one_hot


def sparse_one_hot(array: np.ndarray) -> np.ndarray:
    """One hot encode array, but only return first frame for each button press.

    Args:
        array: (T, D) array of button presses, where D = (# number of buttons + 1).

    Returns:
        One hot encoded array.
    """
    # Use cumsum to count consecutive non-zero elements
    rows, cols = array.shape
    streak_starts = np.diff(np.vstack([np.zeros(cols), array]), axis=0) == 1

    # Default to last column if no 1s
    rows_without_ones = ~np.any(streak_starts, axis=1)
    streak_starts[rows_without_ones, -1] = 1

    # Multiply by the original mask to keep zeros in place
    return array * streak_starts


feature_processors = {
    INPUT_FEATURES_TO_EMBED: lambda x: x,
    INPUT_FEATURES_TO_NORMALIZE: normalize,
    INPUT_FEATURES_TO_INVERT_AND_NORMALIZE: invert_and_normalize,
    INPUT_FEATURES_TO_STANDARDIZE: standardize,
}


START, END = 2000, 2100


def preprocess_features_v0(sample: Dict[str, np.ndarray], stats: Dict[str, FeatureStats]) -> Dict[str, np.ndarray]:
    """Preprocess features."""
    preprocessed = {}

    # Stack buttons and encode one_hot
    for player in ("p1", "p2"):
        button_a = (sample[f"{player}_button_a"]).astype(np.bool_)
        button_b = (sample[f"{player}_button_b"]).astype(np.bool_)
        button_z = sample[f"{player}_button_z"]
        jump = union(sample[f"{player}_button_x"], sample[f"{player}_button_y"])
        shoulder = union(sample[f"{player}_button_l"], sample[f"{player}_button_r"])

        stacked_buttons = np.stack((button_a, button_b, button_z, jump, shoulder), axis=1)
        if player == "p1":
            print(stacked_buttons[START:END])
        preprocessed[f"{player}_buttons"] = vectorized_convert_target_button_to_one_hot(stacked_buttons)

    # for feature_list, preprocessing_func in feature_processors.items():
    #     for feature in feature_list:
    #         process_feature(feature, preprocessing_func)

    return preprocessed


input_path = "/opt/projects/hal2/data/dev/val.parquet"
stats_path = "/opt/projects/hal2/data/dev/stats.json"

table: pa.Table = pq.read_table(input_path, memory_map=True)
stats = load_dataset_stats(stats_path)

table_slice = table

t0 = time.perf_counter()
preprocessed = preprocess_features_v0(pyarrow_table_to_np_dict(table_slice), stats)
t1 = time.perf_counter()
print(f"Time to preprocess features: {t1 - t0} seconds")
preprocessed["p1_buttons"][START:END]
