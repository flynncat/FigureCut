from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image, ImageOps


MODEL_DIR = Path(os.environ.get("CUTOUT_MODEL_DIR", Path(__file__).resolve().parent / "models"))


class Cancelled(Exception):
    pass


@dataclass
class Person:
    box: np.ndarray
    confidence: float
    support: np.ndarray | None = None


def merge_people(people: list[Person]) -> list[Person]:
    merged: list[Person] = []
    for candidate in sorted(people, key=lambda person: person.confidence, reverse=True):
        for existing in merged:
            intersection = np.maximum(0, np.minimum(candidate.box[2:], existing.box[2:]) -
                                      np.maximum(candidate.box[:2], existing.box[:2])).prod()
            area_a = np.maximum(0, candidate.box[2:] - candidate.box[:2]).prod()
            area_b = np.maximum(0, existing.box[2:] - existing.box[:2]).prod()
            duplicate = intersection / max(1, area_a + area_b - intersection) > 0.50 or (
                intersection / max(1, min(area_a, area_b)) > 0.85 and
                intersection / max(1, max(area_a, area_b)) > 0.35
            )
            if existing.support is not None and candidate.support is not None:
                overlap = int(np.count_nonzero(existing.support & candidate.support))
                total_a, total_b = int(existing.support.sum()), int(candidate.support.sum())
                duplicate = (overlap / max(1, total_a + total_b - overlap) > 0.35 or
                             overlap / max(1, min(total_a, total_b)) > 0.80)
            if duplicate:
                break
        else:
            merged.append(candidate)
    return merged


def edge_alpha(mask: np.ndarray, hard: bool = False) -> np.ndarray:
    """Keep the interior opaque; antialias only the one-pixel contour band."""
    binary = np.where(mask, 255, 0).astype(np.uint8)
    if hard:
        return binary
    padded = cv2.copyMakeBorder(binary, 1, 1, 1, 1, cv2.BORDER_CONSTANT)
    alpha = cv2.GaussianBlur(padded, (3, 3), 0.45)[1:-1, 1:-1]
    alpha[alpha <= 2] = 0
    alpha[alpha >= 253] = 255
    return alpha


def refine_mask(mask: np.ndarray, support: np.ndarray,
                clipped: tuple[bool, bool, bool, bool]) -> np.ndarray:
    """Protect coarse-mask interiors while retaining SAM's fine contour detail."""
    candidate = mask.copy()
    count, labels, stats, _ = cv2.connectedComponentsWithStats(support, 8)
    protected = support.copy()
    if count > 1:
        overlap = np.bincount(labels.ravel(), weights=mask.ravel(), minlength=count)
        main = 1 + int(np.argmax(overlap[1:]))
        for label in range(1, count):
            if label != main and overlap[label] / stats[label, cv2.CC_STAT_AREA] < 0.50:
                protected[labels == label] = 0
    # Only add confidently interior pixels; never erode the detailed SAM foreground.
    core = cv2.erode(protected, np.ones((9, 9), np.uint8))
    core_pixels = int(core.sum())
    if core_pixels and np.count_nonzero(core & mask) / core_pixels < 0.85:
        candidate |= core
    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, 8)
    if count > 1:
        overlap = np.bincount(labels.ravel(), weights=support.ravel(), minlength=count)
        main = 1 + int(np.argmax(overlap[1:]))
        for label in range(1, count):
            if label != main and overlap[label] / stats[label, cv2.CC_STAT_AREA] < 0.10:
                candidate[labels == label] = 0
    padded = np.pad(candidate, 1)
    left, top, right, bottom = clipped
    if left:
        padded[1:-1, 0] = support[:, 0]
    if top:
        padded[0, 1:-1] = support[0, :]
    if right:
        padded[1:-1, -1] = support[:, -1]
    if bottom:
        padded[-1, 1:-1] = support[-1, :]
    count, labels, stats, _ = cv2.connectedComponentsWithStats(1 - padded, 8)
    exterior = set(np.unique(np.r_[labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    supported = np.bincount(labels.ravel(), weights=np.pad(support, 1).ravel(), minlength=count)
    for label in range(1, count):
        if label not in exterior and supported[label] / stats[label, cv2.CC_STAT_AREA] >= 0.75:
            padded[labels == label] = 1
    return padded[1:-1, 1:-1]


def save_person(rgb: np.ndarray, alpha: np.ndarray, folder: Path,
                stem: str, number: int, full_size: bool,
                origin: tuple[int, int], image_size: tuple[int, int]) -> Path:
    rgba = np.dstack((rgb, alpha))
    visible = Image.fromarray(alpha).getbbox()
    if visible is None:
        raise ValueError("人物蒙版为空，未导出。")
    if full_size:
        result = Image.new("RGBA", image_size)
        result.paste(Image.fromarray(rgba), origin)
    else:
        x0, y0, x1, y1 = visible
        visible = (max(0, x0 - 4), max(0, y0 - 4),
                   min(rgb.shape[1], x1 + 4), min(rgb.shape[0], y1 + 4))
        result = Image.fromarray(rgba).crop(visible)
    name = f"{stem}-人物_{number:02d}"
    for suffix in range(100000):
        path = folder / f"{name}{'_' + str(suffix) if suffix else ''}.png"
        try:
            handle = path.open("xb")
        except FileExistsError:
            continue
        try:
            with handle:
                result.save(handle, "PNG")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path
    raise OSError("同名输出文件过多。")


class PersonCutout:
    def __init__(self, notify: Callable[[str], None] = lambda text: None):
        import torch
        from ultralytics import SAM, YOLO

        torch.set_num_threads(min(8, os.cpu_count() or 1))
        self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        notify("加载逐人检测模型（首次使用会下载）…")
        self.detector = YOLO(MODEL_DIR / "yolo11l-seg.pt")
        notify("加载 SAM 2.1 精细分割模型（首次使用会下载）…")
        self.segmenter = SAM(MODEL_DIR / "sam2.1_b.pt")

    def detect(self, rgb: np.ndarray, confidence: float = 0.20) -> list[Person]:
        # Detect on a bounded image, then segment each crop at its own resolution.
        height, width = rgb.shape[:2]
        scale = min(1.0, 2560 / max(height, width))
        small = cv2.resize(rgb, (round(width * scale), round(height * scale)))
        sh, sw = small.shape[:2]
        windows = [(0, 0, sw, sh)]
        if max(height, width) > 3000:
            # Overlapping tiles recover small/occluded people without downsampling them away.
            tw, th = round(sw * 0.60), round(sh * 0.60)
            windows += [(x, y, x + tw, y + th)
                        for y in (0, sh - th) for x in (0, sw - tw)]
        people = []
        for left, top, right, bottom in windows:
            prediction = self.detector.predict(
                small[top:bottom, left:right, ::-1].copy(), classes=[0], conf=confidence,
                iou=0.55, imgsz=1536, retina_masks=True, device=self.device, verbose=False,
            )[0]
            if prediction.masks is None:
                continue
            for box, score, mask in zip(prediction.boxes.xyxy.cpu().numpy(),
                                         prediction.boxes.conf.cpu().numpy(),
                                         prediction.masks.data.cpu().numpy()):
                # Discard new fragments cut by an internal tile border.
                if ((left > 0 and box[0] < 3) or (top > 0 and box[1] < 3) or
                    (right < sw and box[2] > right - left - 3) or
                    (bottom < sh and box[3] > bottom - top - 3)):
                    continue
                support = np.zeros((sh, sw), dtype=np.uint8)
                support[top:bottom, left:right] = mask.astype(np.uint8)
                original_box = (box + np.array([left, top, left, top])) / scale
                people.append(Person(original_box.astype(np.float32), float(score), support))
        people = merge_people(people)
        # Stable numbering follows the horizontal position in the photograph.
        return sorted(people, key=lambda person: (float(person.box[0] + person.box[2]),
                                                  float(person.box[1])))

    def segment(self, rgb: np.ndarray, person: Person,
                neighbours: list[Person] | None = None) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
        height, width = rgb.shape[:2]
        x0, y0, x1, y1 = person.box
        margin = max(32, round(max(x1 - x0, y1 - y0) * 0.12))
        left, top = max(0, int(x0) - margin), max(0, int(y0) - margin)
        right, bottom = min(width, int(np.ceil(x1)) + margin), min(height, int(np.ceil(y1)) + margin)
        crop = rgb[top:bottom, left:right].copy()
        box = (person.box - np.array([left, top, left, top])).tolist()
        support = None
        protected = None
        prompts = {}
        if person.support is not None:
            scale = person.support.shape[1] / width
            support = cv2.warpAffine(person.support,
                                    np.array([[1 / scale, 0, -left], [0, 1 / scale, -top]], np.float32),
                                    (right - left, bottom - top), flags=cv2.INTER_NEAREST)
            protected = support.copy()
            distances = cv2.distanceTransform(support, cv2.DIST_L2, 5)
            points, labels = [], []
            for _ in range(3):
                _, peak, _, location = cv2.minMaxLoc(distances)
                if peak < 4:
                    break
                points.append(list(location))
                labels.append(1)
                cv2.circle(distances, location, max(12, round(min(crop.shape[:2]) * .20)), 0, -1)
            for neighbour in neighbours or []:
                if neighbour is person or neighbour.support is None:
                    continue
                other = cv2.warpAffine(neighbour.support,
                                      np.array([[1 / scale, 0, -left], [0, 1 / scale, -top]], np.float32),
                                      (right - left, bottom - top), flags=cv2.INTER_NEAREST)
                protected[cv2.erode(other, np.ones((9, 9), np.uint8)) != 0] = 0
                other[support != 0] = 0
                distances = cv2.distanceTransform(other, cv2.DIST_L2, 5)
                _, peak, _, location = cv2.minMaxLoc(distances)
                if peak >= 4:
                    points.append(list(location))
                    labels.append(0)
            if points:
                prompts = {"points": [points], "labels": [labels]}
        result = self.segmenter.predict(
            crop[:, :, ::-1].copy(), bboxes=[box],
            device=self.device, imgsz=1024, verbose=False, **prompts,
        )[0]
        if result.masks is None or len(result.masks.data) != 1:
            raise ValueError("无法得到独立人物蒙版，请检查原图中的遮挡情况。")
        mask = result.masks.data[0].cpu().numpy().astype(np.uint8)
        if mask.shape != crop.shape[:2]:
            raise ValueError("分割蒙版尺寸与原图不一致，已停止该人物导出。")
        if int(mask.sum()) < max(16, (x1 - x0) * (y1 - y0) * 0.03):
            raise ValueError("人物蒙版异常偏小，已停止该人物导出。")
        if protected is not None:
            mask = refine_mask(mask, protected, (left == 0, top == 0, right == width, bottom == height))
        return crop, mask, (left, top)

    def process(self, path: Path, folder: Path, *, hard: bool = False,
                full_size: bool = False, confidence: float = 0.20,
                cancel: threading.Event | None = None,
                progress: Callable[[int, int, str], None] = lambda done, total, text: None,
                log: Callable[[str], None] = lambda text: None) -> tuple[int, int]:
        def check_cancel():
            if cancel is not None and cancel.is_set():
                raise Cancelled()

        check_cancel()
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
        rgb = np.array(image)
        progress(0, 0, "正在识别每个人物…")
        people = self.detect(rgb, confidence)
        check_cancel()
        if not people:
            raise ValueError("未检测到人物，未生成 PNG；请降低识别阈值后重试。")
        log(f"{path.name}：识别到 {len(people)} 人，按从左到右的顺序导出。")
        folder.mkdir(parents=True, exist_ok=True)
        exported, failed = 0, 0
        for index, person in enumerate(people, 1):
            check_cancel()
            progress(index - 1, len(people), f"正在精细分割人物 {index}/{len(people)}…")
            x0, y0, x1, y1 = person.box
            if x0 <= 3 or y0 <= 3 or x1 >= image.width - 3 or y1 >= image.height - 3:
                log(f"人物 {index} 位于画面边缘，仅导出可见部分，建议复查轮廓。")
            elif person.confidence < .50:
                log(f"人物 {index} 的识别置信度较低，建议复查结果。")
            try:
                crop, mask, origin = self.segment(rgb, person, people)
                check_cancel()
                alpha = edge_alpha(mask, hard)
                target = save_person(crop, alpha, folder, path.stem, index,
                                     full_size, origin, image.size)
                exported += 1
                log(f"已保存 {target.name}（检测置信度 {person.confidence:.0%}）")
            except Cancelled:
                raise
            except Exception as exc:
                failed += 1
                log(f"人物 {index} 导出失败：{exc}")
            progress(index, len(people), f"已处理人物 {index}/{len(people)}")
        return exported, failed
