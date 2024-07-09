# %%
import numpy as np

from hal.data.constants import STICK_XY_CLUSTER_CENTERS_V0


def one_hot_2d(arr: np.ndarray) -> np.ndarray:
    """
    One-hot encode 2D array of raw button presses.

    Keeps temporally newest button press, and tie-breaks by choosing left-most button (i.e. priority is given in order of `melee.enums.Button`).

    Args:
        arr (np.ndarray): Input array of shape (T, D) where T is the number of time steps
                          and D is the (number of buttons + 1).

    Returns:
        np.ndarray: One-hot encoded array of the same shape (T, D).
    """
    T, D = arr.shape
    row_sums = arr.sum(axis=1)
    multi_pressed = np.argwhere(row_sums > 1).flatten()
    first_multi_pressed = multi_pressed[0]
    prev_buttons = set(np.where(arr[first_multi_pressed - 1] == 1)[0]) if first_multi_pressed > 0 else set()

    for i in multi_pressed:
        curr_press = arr[i]
        curr_buttons = set(np.where(curr_press == 1)[0])

        if curr_buttons == prev_buttons:
            arr[i] = arr[i - 1]
            continue
        elif curr_buttons > prev_buttons:
            new_button_idx = min(curr_buttons - prev_buttons)
            arr[i] = np.zeros(D)
            arr[i, new_button_idx] = 1
            prev_buttons = curr_buttons
        else:
            new_button_idx = min(curr_buttons)
            arr[i] = np.zeros(D)
            arr[i, new_button_idx] = 1
            prev_buttons = curr_buttons

    # Handle rows with no presses
    no_press = np.argwhere(row_sums == 0).flatten()
    arr[no_press, -1] = 1

    return arr


def get_closest_stick_xy_cluster_v0(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """
    Calculate the closest point in STICK_XY_CLUSTER_CENTERS_V0 for given x and y values.

    Args:
        x (np.ndarray): (T,) X-coordinates in range [0, 1]
        y (np.ndarray): (T,) Y-coordinates in range [0, 1]

    Returns:
        np.ndarray: (T,) Indices of the closest cluster centers
    """
    point = np.stack((x, y), axis=-1)  # Shape: (T, 2)
    distances = np.sum((STICK_XY_CLUSTER_CENTERS_V0 - point[:, np.newaxis, :]) ** 2, axis=-1)
    return np.argmin(distances, axis=-1)
