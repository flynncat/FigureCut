import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cutout_tool import CutoutApp


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = CutoutApp()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.app.events = queue.Queue()
        self.app.cancel_event.clear()
        self.app.worker = None

    def test_blank_output_is_rejected(self):
        self.app.files = [Path("sample.jpg")]
        self.app.output_dir.set("   ")
        with patch("cutout_tool.messagebox.showinfo") as info:
            self.app._start()
            info.assert_called_once()
        self.assertIsNone(self.app.worker)

    def test_batch_continues_after_file_failure_and_counts_people(self):
        self.app.engine = Mock()
        self.app.engine.process.side_effect = [ValueError("invalid image"), (3, 0)]
        with tempfile.TemporaryDirectory() as directory:
            self.app._process_files([Path("bad.jpg"), Path("good.jpg")],
                                    Path(directory), "精准边缘", False, .2)
            events = list(self.app.events.queue)
            done = [payload for event, payload in events if event == "done"]
            self.assertEqual(done[0][:4], (2, 2, 3, 1))
            self.assertEqual(self.app.engine.process.call_count, 2)
            self.app._drain_events()
            self.assertEqual(self.app.progress_var.get(), 100)
            self.assertEqual(self.app.total_var.get(), "2 / 2")
            self.assertIn("3 个人物", self.app.status_var.get())
            self.assertIn("失败 1 项", self.app.status_var.get())

    def test_cancel_before_batch_does_not_process_files(self):
        self.app.engine = Mock()
        self.app.cancel_event.set()
        self.app._process_files([Path("sample.jpg")], Path("."), "硬边", False, .2)
        self.app.engine.process.assert_not_called()
        events = list(self.app.events.queue)
        done = [payload for event, payload in events if event == "done"][0]
        self.assertEqual(done[:4], (0, 1, 0, 0))
        self.assertTrue(done[-1])


if __name__ == "__main__":
    unittest.main()
