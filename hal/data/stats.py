import json
from pathlib import Path
from typing import Dict
from typing import Union

import attr


@attr.s(auto_attribs=True, frozen=True)
class FeatureStats:
    """Contains mean, std, median, min, and max for each feature."""

    mean: float
    std: float
    min: float
    max: float
    # median: float


def load_dataset_stats(path: Union[str, Path]) -> Dict[str, FeatureStats]:
    """Load the dataset statistics from a JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    dataset_stats = {}
    for k, v in data.items():
        # Explode in case of missing fields
        feature_stats = FeatureStats(mean=v["mean"], std=v["std"], min=v["min"], max=v["max"])
        dataset_stats[k] = feature_stats
    return dataset_stats
