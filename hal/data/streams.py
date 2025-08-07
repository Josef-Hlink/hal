import os
from typing import Dict

from streaming.base.stream import Stream

from hal.local_paths import REPO_DIR

AWS_BUCKET = os.getenv("AWS_BUCKET")
# Only require AWS_BUCKET if we're actually using streams
# This allows local data usage without AWS configuration


class StreamRegistry:
    STREAMS: Dict[str, Stream] = {}

    @classmethod
    def register(cls, name: str, streams: Stream) -> None:
        if name in cls.STREAMS:
            raise ValueError(f"Stream {name} already registered")
        cls.STREAMS[name] = streams

    @classmethod
    def get(cls, name: str) -> Stream:
        if name in cls.STREAMS:
            return cls.STREAMS[name]
        raise ValueError(f"Stream {name} not registered")


### Ranked

# Only create streams if AWS_BUCKET is configured
if AWS_BUCKET is not None:
    SampleFoxStream = Stream(
        remote=f"s3://{AWS_BUCKET}/sample-fox",
        local=f"{REPO_DIR}/data/sample-fox",
        proportion=1.0,
        keep_zip=True,
    )

    FullStream = Stream(
        remote=f"s3://{AWS_BUCKET}/full",
        local=f"{REPO_DIR}/data/full",
        proportion=1.0,
        keep_zip=True,
    )

    StreamRegistry.register("sample-fox", SampleFoxStream)
    StreamRegistry.register("full", FullStream)
