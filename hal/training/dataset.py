from pathlib import Path
from typing import Dict
from typing import List
from typing import Tuple

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq
from torch.utils.data import Dataset

from hal.data.constants import IDX_BY_CHARACTER_STR
from hal.data.constants import IDX_BY_STAGE_STR
from hal.data.schema import SCHEMA
from hal.data.stats import load_dataset_stats
from hal.training.config import DataConfig
from hal.training.utils import pyarrow_table_to_np_dict
from hal.training.zoo.preprocess.registry import InputPreprocessRegistry
from hal.training.zoo.preprocess.registry import Player
from hal.training.zoo.preprocess.registry import TargetPreprocessRegistry


class MmappedParquetDataset(Dataset):
    """Memory mapped parquet dataset for DDP training.

    If sequence spans multiple replays, `truncate_replay_end` will truncate to the first replay.
    """

    def __init__(
        self,
        input_path: Path,
        stats_path: Path,
        data_config: DataConfig,
    ) -> None:
        """
        Initialize the dataset.

        Args:
            input_path (Path): Path to the parquet file.
            stats_path (Path): Path to the stats file.
            data_config (DataConfig): Configuration for the dataset.

        Raises:
            ValueError: If input_len or target_len are not positive integers.
            FileNotFoundError: If the input file doesn't exist.
        """
        self.config = data_config
        if self.config.input_len <= 0 or self.config.target_len <= 0:
            raise ValueError("input_len and target_len must be positive integers")
        if not Path(input_path).exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        self.input_path = input_path
        self.stats_by_feature_name = load_dataset_stats(stats_path)
        self.input_len = self.config.input_len
        self.target_len = self.config.target_len
        self.trajectory_len = self.config.input_len + self.config.target_len
        self.truncate_rollouts_to_replay_end = self.config.truncate_rollouts_to_replay_end
        self.include_both_players = self.config.include_both_players
        self.player_perspectives: List[Player] = ["p1", "p2"] if self.include_both_players else ["p1"]
        self.replay_filter = self.config.replay_filter

        self.input_preprocessing_fn = InputPreprocessRegistry.get(self.config.input_preprocessing_fn)
        self.target_preprocessing_fn = TargetPreprocessRegistry.get(self.config.target_preprocessing_fn)

        self.parquet_table = pq.read_table(self.input_path, schema=SCHEMA, memory_map=True)
        self.filtered_indices = self._apply_filter()

    def _apply_filter(self) -> np.ndarray:
        if self.replay_filter is None:
            return np.arange(len(self.parquet_table) - self.trajectory_len)

        filter_conditions = []

        if self.replay_filter.replay_uuid is not None:
            filter_conditions.append(pc.equal(self.parquet_table["replay_uuid"], self.replay_filter.replay_uuid))

        if self.replay_filter.stage is not None:
            stage_idx = IDX_BY_STAGE_STR[self.replay_filter.stage]
            filter_conditions.append(pc.equal(self.parquet_table["stage"], stage_idx))

        if self.replay_filter.character is not None:
            character_idx = IDX_BY_CHARACTER_STR[self.replay_filter.character]
            filter_conditions.append(
                pc.or_(
                    pc.equal(self.parquet_table["p1_character"], character_idx),
                    pc.equal(self.parquet_table["p2_character"], character_idx),
                )
            )

        if filter_conditions:
            combined_filter = pc.and_(*filter_conditions)
            filtered_indices = np.where(combined_filter.to_numpy())[0]
            valid_indices = filtered_indices[filtered_indices < len(self.parquet_table) - self.trajectory_len]
            return valid_indices
        else:
            return np.arange(len(self.parquet_table) - self.trajectory_len)

    def __len__(self) -> int:
        return len(self.filtered_indices) * len(self.player_perspectives)

    def __getitem__(self, index: int) -> Tuple[Dict[str, np.ndarray], Dict[str, np.ndarray]]:
        player_index = index % len(self.player_perspectives)
        actual_index = self.filtered_indices[index // len(self.player_perspectives)]
        table_chunk = self.parquet_table[actual_index : actual_index + self.trajectory_len]

        # Truncate to the first replay
        if self.truncate_rollouts_to_replay_end:
            first_uuid = table_chunk["replay_uuid"][0].as_py()
            mask = pc.equal(table_chunk["replay_uuid"], first_uuid)
            table_chunk = table_chunk.filter(mask)

        features_by_name = pyarrow_table_to_np_dict(table_chunk)
        player = self.player_perspectives[player_index]
        inputs = self.input_preprocessing_fn(features_by_name, self.trajectory_len, player, self.stats_by_feature_name)
        targets = self.target_preprocessing_fn(features_by_name, player)
        return inputs, targets
