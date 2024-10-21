from pathlib import Path
from typing import Sequence
from typing import Tuple

import torch
from tensordict import TensorDict
from torch.utils.data import DataLoader

from hal.training.config import TrainConfig
from hal.training.streaming_dataset import HALStreamingDataset


def collate_tensordicts(batch: Sequence[TensorDict]) -> TensorDict:
    # Custom collate function for TensorDict because PyTorch type routing doesn't know about it yet
    # Assuming all items in the batch have the same keys
    keys = batch[0].keys()
    return TensorDict({key: torch.stack([item[key] for item in batch]) for key in keys})


def get_dataloaders(config: TrainConfig) -> Tuple[DataLoader, DataLoader]:
    batch_size = config.local_batch_size
    train_dir = Path(config.data.data_dir) / "train"
    val_dir = Path(config.data.data_dir) / "val"
    train_dataset = HALStreamingDataset(
        local=str(train_dir),
        remote=None,
        batch_size=batch_size,
        shuffle=True,
        data_config=config.data,
        embed_config=config.embedding,
        stats_path=config.data.stats_path,
    )
    val_dataset = HALStreamingDataset(
        local=str(val_dir),
        remote=None,
        batch_size=batch_size,
        shuffle=False,
        data_config=config.data,
        embed_config=config.embedding,
        stats_path=config.data.stats_path,
    )

    train_loader = DataLoader(train_dataset, batch_size=batch_size, collate_fn=collate_tensordicts)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, collate_fn=collate_tensordicts)

    return train_loader, val_loader
