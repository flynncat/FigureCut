import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from cutout_engine import Cancelled, Person, PersonCutout, edge_alpha, merge_people, refine_mask, save_person


class CutoutTests(unittest.TestCase):
    def test_alpha_preserves_interior_and_limits_transition(self):
        mask = np.zeros((40, 40), np.uint8)
        mask[8:32, 8:32] = 1
        alpha = edge_alpha(mask)
        np.testing.assert_array_equal(alpha[9:31, 9:31], 255)
        band = cv2.dilate(mask, np.ones((3, 3), np.uint8)) != cv2.erode(mask, np.ones((3, 3), np.uint8))
        self.assertFalse(np.any(((alpha > 0) & (alpha < 255)) & ~band))
        self.assertEqual(set(np.unique(edge_alpha(mask, hard=True))), {0, 255})

    def test_hole_repair_keeps_real_gaps_and_outer_contour(self):
        support = np.zeros((60, 60), np.uint8)
        support[10:50, 10:50] = 1
        support[30:40, 30:40] = 0
        mask = support.copy()
        mask[15:20, 15:20] = 0
        result = refine_mask(mask, support, (False,) * 4)
        np.testing.assert_array_equal(result, support)

    def test_clipped_clothing_hole_can_be_repaired(self):
        support = np.zeros((60, 60), np.uint8)
        support[10:50, :40] = 1
        mask = support.copy()
        mask[20:30, :20] = 0
        result = refine_mask(mask, support, (True, False, False, False))
        np.testing.assert_array_equal(result, support)

    def test_clothing_core_is_preserved_when_sam_leaves_an_open_gap(self):
        support = np.zeros((60, 60), np.uint8)
        support[10:50, 10:50] = 1
        mask = support.copy()
        mask[10:40, 20:35] = 0
        result = refine_mask(mask, support, (False,) * 4)
        self.assertTrue(result[15:35, 20:25].all())
        self.assertFalse(result[support == 0].any())

    def test_duplicate_masks_merge_but_neighbours_stay_separate(self):
        a = np.zeros((40, 40), np.uint8)
        a[5:35, 5:15] = 1
        b = np.zeros_like(a)
        b[5:35, 20:30] = 1
        people = [Person(np.array([0, 0, 40, 40]), .9, a),
                  Person(np.array([0, 0, 40, 40]), .8, b),
                  Person(np.array([0, 0, 40, 40]), .7, a.copy())]
        self.assertEqual(len(merge_people(people)), 2)

    def test_png_keeps_exact_rgb_and_canvas_placement_without_overwriting(self):
        rgb = np.random.default_rng(7).integers(0, 256, (30, 20, 3), dtype=np.uint8)
        alpha = np.zeros((30, 20), np.uint8)
        alpha[3:26, 3:17] = 255
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            first = save_person(rgb, alpha, folder, "sample", 1, False, (7, 8), (50, 60))
            second = save_person(rgb, alpha, folder, "sample", 1, False, (7, 8), (50, 60))
            self.assertNotEqual(first, second)
            with Image.open(first) as image:
                self.assertEqual(image.mode, "RGBA")
                np.testing.assert_array_equal(np.array(image)[:, :, :3], rgb)
            full = save_person(rgb, alpha, folder, "sample", 2, True, (7, 8), (50, 60))
            with Image.open(full) as image:
                self.assertEqual(image.size, (50, 60))
                np.testing.assert_array_equal(np.array(image)[8:38, 7:27, :3], rgb)
                np.testing.assert_array_equal(np.array(image)[8:38, 7:27, 3], alpha)

    def test_one_person_failure_does_not_abort_remaining_people(self):
        engine = PersonCutout.__new__(PersonCutout)
        person = Person(np.array([0, 0, 20, 30]), .9)
        rgb = np.full((30, 20, 3), 50, np.uint8)
        mask = np.ones((30, 20), np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "sample.png"
            Image.fromarray(rgb).save(source)
            with patch.object(engine, "detect", return_value=[person, person]), patch.object(
                engine, "segment", side_effect=[ValueError("bad mask"), (rgb, mask, (0, 0))]
            ):
                self.assertEqual(engine.process(source, folder), (1, 1))
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(Cancelled):
                engine.process(source, folder, cancel=cancel)


if __name__ == "__main__":
    unittest.main()
