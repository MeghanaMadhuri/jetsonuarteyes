#!/usr/bin/env python3
"""Download a faster-whisper CTranslate2 model for Nina ASR (Jetson-sized)."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# Repo IDs published by Systran for faster-whisper (small, Orin-friendly).
MODEL_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
}

# Legacy install script pulled this huge model into the wrong tree — safe to delete.
_STALE_CACHE_DIRS = (
    "models/models--openai--whisper-large-v3-turbo",
    "models/whisper-turbo",
    "models/_fw_cache",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _hf_token() -> str | None:
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_HUB_TOKEN"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw
    return None


def _remove_stale_caches(root: Path) -> None:
    for rel in _STALE_CACHE_DIRS:
        path = root / rel
        if path.exists():
            print(f"Removing stale/incomplete cache: {path}")
            shutil.rmtree(path, ignore_errors=True)


def _verify_model_dir(model_dir: Path) -> bool:
    required = ("model.bin", "config.json")
    for name in required:
        if not (model_dir / name).is_file():
            print(f"Missing {model_dir / name}")
            return False
    return True


def download_with_huggingface_hub(repo_id: str, model_dir: Path) -> bool:
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("huggingface_hub not installed (pip install huggingface_hub)")
        return False

    token = _hf_token()
    if not token:
        print(
            "Note: set HF_TOKEN for faster Hugging Face downloads "
            "(optional; tiny model is ~75 MB)."
        )

    model_dir.parent.mkdir(parents=True, exist_ok=True)
    if model_dir.exists() and not _verify_model_dir(model_dir):
        print(f"Removing incomplete model dir: {model_dir}")
        shutil.rmtree(model_dir, ignore_errors=True)

    print(f"Downloading {repo_id} -> {model_dir} ...")
    snapshot_download(
        repo_id,
        local_dir=str(model_dir),
        local_dir_use_symlinks=False,
        token=token,
    )
    return _verify_model_dir(model_dir)


def download_with_faster_whisper(size: str, model_dir: Path, root: Path) -> bool:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("faster_whisper not installed")
        return False

    cache_root = root / "models" / "_fw_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    print(f"Downloading faster-whisper size={size!r} via faster_whisper ...")
    WhisperModel(size, download_root=str(cache_root), device="cpu", compute_type="int8")

    found: Path | None = None
    for candidate in cache_root.rglob("model.bin"):
        if candidate.is_file():
            found = candidate.parent
            break
    if found is None:
        print("Could not locate model.bin after faster_whisper download")
        return False

    if model_dir.exists():
        shutil.rmtree(model_dir, ignore_errors=True)
    shutil.copytree(found, model_dir)
    return _verify_model_dir(model_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--size",
        choices=sorted(MODEL_REPOS),
        default=os.environ.get("WHISPER_MODEL_SIZE", "tiny"),
        help="Model size (default: tiny — recommended on Orin Nano)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory (default: models/whisper-<size>)",
    )
    args = parser.parse_args()

    root = _repo_root()
    os.chdir(root)

    model_dir = args.out or (root / "models" / f"whisper-{args.size}")
    model_dir = model_dir.resolve()

    print("Nina Whisper model downloader")
    print("=" * 40)

    if _verify_model_dir(model_dir):
        print(f"Model already present: {model_dir}")
        print(f"Set ASR_MODEL_PATH={model_dir}")
        return 0

    _remove_stale_caches(root)

    repo_id = MODEL_REPOS[args.size]
    ok = download_with_huggingface_hub(repo_id, model_dir)
    if not ok:
        print("Retrying with faster_whisper downloader ...")
        ok = download_with_faster_whisper(args.size, model_dir, root)

    if not ok:
        print("\nDownload failed.")
        print("Try:")
        print(f"  cd {root}")
        print("  export HF_TOKEN=<your Hugging Face token>   # optional")
        print(f"  python3 scripts/download_whisper_model.py --size {args.size}")
        print("Or install hub client: pip install huggingface_hub")
        return 1

    print("\nModel ready.")
    print(f"  Path: {model_dir}")
    print(f"  ASR_MODEL_PATH={model_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
