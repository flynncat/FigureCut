from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import os
import shutil
import sys
import time
import traceback
import urllib.request
from pathlib import Path


MODELS = {
    "yolo11l-seg.pt": "cabe90049795dfc9a370b7934d6dec7f6b9e44a20e573b0ff81b7e205512c872",
    "sam2.1_b.pt": "f1a9cf2dd69d84bb463b5ad98246d03e2d47a130a9295db0ec967e6cd95e2e47",
}
MODEL_BASE = "https://github.com/ultralytics/assets/releases/download/v8.4.0/"
APP_DIR = Path(__file__).resolve().parent


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def valid_model(path: Path, expected: str) -> bool:
    return path.is_file() and digest(path) == expected


def package_problems(requirements: list[Path]) -> list[str]:
    from packaging.requirements import Requirement

    problems = []
    for path in requirements:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            requirement = Requirement(line)
            if requirement.marker and not requirement.marker.evaluate():
                continue
            try:
                version = importlib.metadata.version(requirement.name)
            except importlib.metadata.PackageNotFoundError:
                problems.append(f"Missing: {requirement}")
            else:
                if version not in requirement.specifier:
                    problems.append(f"Version mismatch: {requirement}; installed {version}")
    return problems


def check_packages() -> int:
    try:
        problems = package_problems([APP_DIR / "requirements.txt", APP_DIR / "requirements-runtime.txt"])
    except ImportError:
        problems = ["Missing dependency metadata support (packaging)."]
    if problems:
        print("\n".join(problems), flush=True)
        return 10
    try:
        import cv2
        import numpy as np
        import torch
        import torchvision
        from PIL import Image
        from ultralytics import SAM, YOLO

        boxes = torch.tensor([[0., 0., 4., 4.], [1., 1., 3., 3.]])
        selected = torchvision.ops.nms(boxes, torch.tensor([.9, .8]), .5)
        assert selected.numel() > 0
        assert torch.ones((2, 2)).matmul(torch.ones((2, 2))).sum().item() == 8
        cv2.cvtColor(np.zeros((2, 2, 3), np.uint8), cv2.COLOR_RGB2GRAY)
        assert Image.new("RGBA", (2, 2)).mode == "RGBA"
        assert SAM and YOLO
    except Exception:
        traceback.print_exc()
        return 20
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
    except Exception:
        traceback.print_exc()
        return 40
    print("Python packages, CPU inference, torchvision NMS and Tcl/Tk: OK", flush=True)
    return 0


def download_model(name: str, target: Path, expected: str) -> None:
    partial = target.with_suffix(".download")
    for attempt in range(1, 4):
        print(f"Downloading {name}, attempt {attempt}/3 ...", flush=True)
        try:
            request = urllib.request.Request(MODEL_BASE + name, headers={"User-Agent": "PersonCutout/1.0"})
            with urllib.request.urlopen(request, timeout=60) as source, partial.open("wb") as output:
                total = int(source.headers.get("Content-Length", 0))
                copied, last_report = 0, time.monotonic()
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
                    copied += len(chunk)
                    if time.monotonic() - last_report >= 2:
                        print(f"  {copied // (1024 * 1024)} / {total // (1024 * 1024)} MB", flush=True)
                        last_report = time.monotonic()
            if digest(partial) != expected:
                raise ValueError(f"SHA-256 mismatch: {name}")
            partial.replace(target)
            return
        except Exception:
            partial.unlink(missing_ok=True)
            if attempt == 3:
                raise
            time.sleep(attempt)


def prepare_models(folder: Path, download: bool) -> int:
    for name, expected in MODELS.items():
        target = folder / name
        if valid_model(target, expected):
            print(f"Model SHA-256 OK: {name}", flush=True)
            continue
        if not download:
            print(f"Model missing or damaged: {target}", flush=True)
            return 30
        folder.mkdir(parents=True, exist_ok=True)
        bundled = APP_DIR / "models" / name
        if valid_model(bundled, expected):
            print(f"Using bundled model: {name}", flush=True)
            temporary = target.with_suffix(".copying")
            shutil.copyfile(bundled, temporary)
            temporary.replace(target)
        else:
            download_model(name, target, expected)
    from ultralytics import SAM, YOLO

    YOLO(folder / "yolo11l-seg.pt")
    SAM(folder / "sam2.1_b.pt")
    print("Model loading: OK", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packages-only", action="store_true")
    parser.add_argument("--download-models", action="store_true")
    parser.add_argument("--models", type=Path)
    args = parser.parse_args()
    status = check_packages()
    if status or args.packages_only:
        return status
    folder = args.models or Path(os.environ.get("CUTOUT_MODEL_DIR", APP_DIR / "models"))
    try:
        return prepare_models(folder, args.download_models)
    except Exception:
        traceback.print_exc()
        return 30


if __name__ == "__main__":
    raise SystemExit(main())
