#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from huggingface_hub import snapshot_download

LOGGER = logging.getLogger(__name__)


def _download_model(repo_id: str, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    local_dir = target_dir / repo_id
    if local_dir.exists() and any(local_dir.iterdir()):
        LOGGER.info("Model %s already present at %s", repo_id, local_dir)
        return local_dir
    LOGGER.info("Downloading %s to %s", repo_id, local_dir)
    snapshot_download(repo_id=repo_id, local_dir=str(local_dir), local_dir_use_symlinks=False)
    return local_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Download semantic models for goszakupki-bot")
    parser.add_argument("--models-dir", default="models", help="Directory to store downloaded models")
    parser.add_argument(
        "--embedding-model",
        default="BAAI/bge-m3",
        help="Sentence embedding model to download",
    )
    parser.add_argument(
        "--xnli-model",
        default="MoritzLaurer/mDeBERTa-v3-base-xnli",
        help="Zero-shot classification model (if semantic XNLI enabled)",
    )
    parser.add_argument("--skip-xnli", action="store_true", help="Skip downloading the XNLI classifier")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    models_dir = Path(args.models_dir).resolve()
    embedding_dir = _download_model(args.embedding_model, models_dir)
    LOGGER.info("Embedding model ready at %s", embedding_dir)
    if not args.skip_xnli and args.xnli_model:
        xnli_dir = _download_model(args.xnli_model, models_dir)
        LOGGER.info("XNLI model ready at %s", xnli_dir)


if __name__ == "__main__":
    main()
