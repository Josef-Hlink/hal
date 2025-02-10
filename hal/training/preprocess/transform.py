from typing import Callable
from typing import Dict

import numpy as np
import torch
from tensordict import TensorDict

from hal.constants import Player
from hal.constants import SHOULDER_CLUSTER_CENTERS_V0
from hal.constants import STICK_XY_CLUSTER_CENTERS_V0
from hal.constants import STICK_XY_CLUSTER_CENTERS_V1
from hal.data.stats import FeatureStats

Transformation = Callable[..., torch.Tensor]


def cast_int32(array: torch.Tensor, stats: FeatureStats) -> torch.Tensor:
    """Identity function; cast to int32."""
    return array.to(torch.int32)


def normalize(array: torch.Tensor, stats: FeatureStats) -> torch.Tensor:
    """Normalize feature [-1, 1]."""
    return (2 * (array - stats.min) / (stats.max - stats.min) - 1).to(torch.float32)


def invert_and_normalize(array: torch.Tensor, stats: FeatureStats) -> torch.Tensor:
    """Invert and normalize feature to [-1, 1]."""
    return (2 * (stats.max - array) / (stats.max - stats.min) - 1).to(torch.float32)


def standardize(array: torch.Tensor, stats: FeatureStats) -> torch.Tensor:
    """Standardize feature to mean 0 and std 1."""
    return ((array - stats.mean) / stats.std).to(torch.float32)


def union(array_1: torch.Tensor, array_2: torch.Tensor) -> torch.Tensor:
    """Perform logical OR of two features."""
    return array_1 | array_2


def normalize_and_embed_fourier(array: torch.Tensor, stats: FeatureStats, dim: int = 8) -> torch.Tensor:
    """Normalize then embed values at various frequencies."""
    normalized = normalize(array, stats)
    frequencies = 1024 * torch.linspace(0, -torch.tensor(10000.0).log(), dim // 2).exp()
    emb = normalized.view(-1, 1) * frequencies
    return torch.cat([torch.sin(emb), torch.cos(emb)], dim=-1)


def offset(array: torch.Tensor, stats: FeatureStats) -> torch.Tensor:
    """Hard-coded offset for debugging frame."""
    return array + 123


### CONTROLLER / TARGETS


def encode_buttons_one_hot(buttons_LD: np.ndarray) -> np.ndarray:
    """
    One-hot encode 2D array of multiple button presses per time step.

    Keeps temporally newest button press, and tie-breaks by choosing left-most button (i.e. priority is given in order of `melee.enums.Button`).

    Args:
        buttons_LD (np.ndarray): Input array of shape (L, D) where L is the sequence length
                                 and D is the embedding dimension (number of buttons + 1).

    Returns:
        np.ndarray: One-hot encoded array of the same shape (L, D).
    """
    assert buttons_LD.ndim == 2, "Input array must be 2D"
    _, D = buttons_LD.shape
    row_sums = buttons_LD.sum(axis=1)
    multi_pressed = np.argwhere(row_sums > 1).flatten()
    prev_buttons = set()
    if len(multi_pressed) > 0:
        first_multi_pressed = multi_pressed[0]
        prev_buttons = set(np.where(buttons_LD[first_multi_pressed - 1] == 1)[0]) if first_multi_pressed > 0 else set()

    for i in multi_pressed:
        curr_press = buttons_LD[i]
        curr_buttons = set(np.where(curr_press == 1)[0])

        if curr_buttons == prev_buttons:
            buttons_LD[i] = buttons_LD[i - 1]
            continue
        elif curr_buttons > prev_buttons:
            new_button_idx = min(curr_buttons - prev_buttons)
            buttons_LD[i] = np.zeros(D)
            buttons_LD[i, new_button_idx] = 1
            prev_buttons = curr_buttons
        else:
            new_button_idx = min(curr_buttons)
            buttons_LD[i] = np.zeros(D)
            buttons_LD[i, new_button_idx] = 1
            prev_buttons = curr_buttons

    # Handle rows with no presses
    no_press = np.argwhere(row_sums == 0).flatten()
    buttons_LD[no_press, -1] = 1

    return buttons_LD


def get_closest_1D_cluster(x: np.ndarray, cluster_centers: np.ndarray) -> np.ndarray:
    """
    Calculate the closest point in cluster_centers for given x values.

    Args:
        x (np.ndarray): (L,) Input values
        cluster_centers (np.ndarray): (C,) Cluster center values

    Returns:
        np.ndarray: (L,) Indices of the closest cluster centers
    """
    x_reshaped = x.reshape(-1, 1)  # Shape: (L, 1)
    distances = (cluster_centers - x_reshaped) ** 2  # Shape: (L, C)
    return np.argmin(distances, axis=1)  # Shape: (L,)


def get_closest_2D_cluster(x: np.ndarray, y: np.ndarray, cluster_centers: np.ndarray) -> np.ndarray:
    """
    Calculate the closest point in cluster_centers for given x and y values.

    Args:
        x (np.ndarray): (L,) X-coordinates in range [0, 1]
        y (np.ndarray): (L,) Y-coordinates in range [0, 1]
        cluster_centers (np.ndarray): (C, 2) Cluster centers

    Returns:
        np.ndarray: (L,) Indices of the closest cluster centers
    """
    point = np.stack((x, y), axis=-1)  # Shape: (L, 2)
    distances = np.sum((cluster_centers - point[:, np.newaxis, :]) ** 2, axis=-1)
    return np.argmin(distances, axis=-1)


def one_hot_from_int(arr: np.ndarray, num_values: int) -> np.ndarray:
    """
    One-hot encode array of integers.
    """
    return np.eye(num_values)[arr]


def preprocess_controller_inputs_v0(sample: TensorDict, player: str) -> Dict[str, torch.Tensor]:
    """
    Preprocess controller inputs for the given player.
    Computes discretized analog stick one-hot encodings and one-hot-encodes buttons.

    Returns:
        A dict with keys "main_stick", "c_stick", "buttons".
    """
    # --- Process analog stick inputs ---
    main_stick_x = sample[f"{player}_main_stick_x"]
    main_stick_y = sample[f"{player}_main_stick_y"]
    c_stick_x = sample[f"{player}_c_stick_x"]
    c_stick_y = sample[f"{player}_c_stick_y"]

    main_stick_clusters = get_closest_2D_cluster(main_stick_x, main_stick_y, STICK_XY_CLUSTER_CENTERS_V0)
    one_hot_main_stick = one_hot_from_int(main_stick_clusters, len(STICK_XY_CLUSTER_CENTERS_V0))
    c_stick_clusters = get_closest_2D_cluster(c_stick_x, c_stick_y, STICK_XY_CLUSTER_CENTERS_V0)
    one_hot_c_stick = one_hot_from_int(c_stick_clusters, len(STICK_XY_CLUSTER_CENTERS_V0))

    # --- Process controller buttons ---
    button_a = sample[f"{player}_button_a"].bool()
    button_b = sample[f"{player}_button_b"].bool()
    button_x = sample[f"{player}_button_x"].bool()
    button_y = sample[f"{player}_button_y"].bool()
    button_z = sample[f"{player}_button_z"].bool()
    button_l = sample[f"{player}_button_l"].bool()
    button_r = sample[f"{player}_button_r"].bool()

    jump = button_x | button_y
    shoulder = button_l | button_r
    no_button = ~(button_a | button_b | jump | button_z | shoulder)

    stacked_buttons = torch.stack((button_a, button_b, jump, button_z, shoulder, no_button), dim=-1)
    one_hot_buttons = encode_buttons_one_hot(stacked_buttons.numpy())

    return {
        "main_stick": torch.tensor(one_hot_main_stick, dtype=torch.float32),
        "c_stick": torch.tensor(one_hot_c_stick, dtype=torch.float32),
        "buttons": torch.tensor(one_hot_buttons, dtype=torch.float32),
    }


def preprocess_controller_inputs_v1(sample: TensorDict, player: str) -> Dict[str, torch.Tensor]:
    """
    Preprocess controller inputs for the given player.
    Computes discretized analog stick one-hot encodings and one-hot-encodes buttons.

    Returns:
        A dict with keys "main_stick", "c_stick", "buttons".
    """
    # --- Process analog stick inputs ---
    main_stick_x = sample[f"{player}_main_stick_x"]
    main_stick_y = sample[f"{player}_main_stick_y"]
    c_stick_x = sample[f"{player}_c_stick_x"]
    c_stick_y = sample[f"{player}_c_stick_y"]
    shoulder_l = sample[f"{player}_l_shoulder"]
    shoulder_r = sample[f"{player}_r_shoulder"]
    shoulder = np.max(np.stack([shoulder_l, shoulder_r], axis=-1), axis=-1)

    main_stick_clusters = get_closest_2D_cluster(main_stick_x, main_stick_y, STICK_XY_CLUSTER_CENTERS_V1)
    one_hot_main_stick = one_hot_from_int(main_stick_clusters, len(STICK_XY_CLUSTER_CENTERS_V1))
    c_stick_clusters = get_closest_2D_cluster(c_stick_x, c_stick_y, STICK_XY_CLUSTER_CENTERS_V1)
    one_hot_c_stick = one_hot_from_int(c_stick_clusters, len(STICK_XY_CLUSTER_CENTERS_V1))
    shoulder_clusters = get_closest_1D_cluster(shoulder, SHOULDER_CLUSTER_CENTERS_V0)
    one_hot_shoulder = one_hot_from_int(shoulder_clusters, len(SHOULDER_CLUSTER_CENTERS_V0))

    # --- Process controller buttons ---
    button_a = sample[f"{player}_button_a"].bool()
    button_b = sample[f"{player}_button_b"].bool()
    button_x = sample[f"{player}_button_x"].bool()
    button_y = sample[f"{player}_button_y"].bool()
    button_z = sample[f"{player}_button_z"].bool()
    button_l = sample[f"{player}_button_l"].bool()
    button_r = sample[f"{player}_button_r"].bool()

    jump = button_x | button_y
    shoulder = button_l | button_r
    no_button = ~(button_a | button_b | jump | button_z | shoulder)

    stacked_buttons = torch.stack((button_a, button_b, jump, button_z, shoulder, no_button), dim=-1)
    one_hot_buttons = encode_buttons_one_hot(stacked_buttons.numpy())

    return {
        "main_stick": torch.tensor(one_hot_main_stick, dtype=torch.float32),
        "c_stick": torch.tensor(one_hot_c_stick, dtype=torch.float32),
        "shoulder": torch.tensor(one_hot_shoulder, dtype=torch.float32),
        "buttons": torch.tensor(one_hot_buttons, dtype=torch.float32),
    }


def preprocess_controller_inputs_v0_concat(sample: TensorDict, player: Player) -> torch.Tensor:
    controller_feats = preprocess_controller_inputs_v0(sample, player)
    return torch.cat(list(controller_feats.values()), dim=-1)


def preprocess_controller_inputs_v1_concat(sample: TensorDict, player: Player) -> torch.Tensor:
    controller_feats = preprocess_controller_inputs_v1(sample, player)
    return torch.cat(list(controller_feats.values()), dim=-1)
