from __future__ import annotations

import os
import queue
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from cutout_engine import Cancelled, PersonCutout


SUPPORTED = {".jpg", ".jpeg", ".png", ".webp"}


class CutoutApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("人物自动抠图 · 批处理工具")
        self.geometry("1000x860")
        self.minsize(920, 800)
        self.configure(bg="#f4f6f3")

        self.files: list[Path] = []
        self.output_dir = tk.StringVar()
        self.quality = tk.StringVar(value="精准边缘（1 像素抗锯齿）")
        self.full_size = tk.BooleanVar(value=False)
        self.confidence = tk.StringVar(value="0.20")
        self.person_var = tk.StringVar(value="人物 0 / 0")
        self.person_progress_var = tk.DoubleVar(value=0)
        self.engine: PersonCutout | None = None
        self.status_var = tk.StringVar(value="选择图片后开始")
        self.current_var = tk.StringVar(value="等待任务")
        self.total_var = tk.StringVar(value="0 / 0")
        self.progress_var = tk.DoubleVar(value=0)
        self.cancel_event = threading.Event()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None

        self._build_style()
        self._build_ui()
        self.after(120, self._drain_events)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#f4f6f3")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f4f6f3", foreground="#1b2521", font=("Microsoft YaHei UI", 10))
        style.configure("Muted.TLabel", background="#ffffff", foreground="#7a8880", font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background="#f4f6f3", foreground="#14201c", font=("Microsoft YaHei UI", 21, "bold"))
        style.configure("Section.TLabel", background="#ffffff", foreground="#14201c", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Accent.TButton", background="#17352b", foreground="#ffffff", padding=(14, 9), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#295b49"), ("disabled", "#b2bdb7")])
        style.configure("Secondary.TButton", background="#eef4f0", foreground="#285c49", padding=(10, 7))
        style.map("Secondary.TButton", background=[("active", "#dcebe3")])
        style.configure("Danger.TButton", background="#fff0ec", foreground="#bc593f", padding=(10, 7))
        style.map("Danger.TButton", background=[("active", "#ffe0d8")])
        style.configure("TEntry", fieldbackground="#fbfcfb", padding=7)
        style.configure("TCombobox", fieldbackground="#fbfcfb", padding=6)
        style.configure("Horizontal.TProgressbar", troughcolor="#e4ebe6", background="#57b88e", bordercolor="#e4ebe6", lightcolor="#57b88e", darkcolor="#57b88e")

    def _card(self, parent: tk.Widget) -> ttk.Frame:
        return ttk.Frame(parent, style="Card.TFrame", padding=14)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=(26, 22, 26, 20))
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 18))
        ttk.Label(header, text="人物自动抠图", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="批量处理 · 每个人物单独保存为透明 PNG", foreground="#718078").pack(anchor="w", pady=(4, 0))

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(1, weight=1)

        files_card = self._card(body)
        files_card.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 10))
        files_card.rowconfigure(2, weight=1)
        files_card.columnconfigure(0, weight=1)
        ttk.Label(files_card, text="1 选择待处理图片", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(files_card, text="可一次选择多张 JPG、PNG 或 WEBP。列表顺序就是处理顺序。", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 12))
        list_frame = ttk.Frame(files_card, style="Card.TFrame")
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        self.file_list = tk.Listbox(list_frame, selectmode=tk.EXTENDED, activestyle="none", bg="#fbfcfb", fg="#24332d", relief="flat", highlightthickness=1, highlightbackground="#dbe5de", selectbackground="#d9f1e5", selectforeground="#1c4937", font=("Microsoft YaHei UI", 10))
        self.file_list.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.file_list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.file_list.configure(yscrollcommand=scroll.set)
        file_actions = ttk.Frame(files_card, style="Card.TFrame")
        file_actions.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(file_actions, text="添加图片（可多选）", style="Secondary.TButton", command=self._choose_files).pack(side="left")
        ttk.Button(file_actions, text="移除选中", style="Danger.TButton", command=self._remove_selected).pack(side="left", padx=(8, 0))
        ttk.Button(file_actions, text="清空", style="Secondary.TButton", command=self._clear_files).pack(side="left", padx=(8, 0))

        settings_card = self._card(body)
        settings_card.grid(row=0, column=1, sticky="nsew", pady=(0, 10))
        settings_card.columnconfigure(0, weight=1)
        ttk.Label(settings_card, text="2 设置输出", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(settings_card, text="输出：原文件名-人物_01.png、人物_02.png…", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 13))
        ttk.Label(settings_card, text="输出文件夹").grid(row=2, column=0, sticky="w")
        output_row = ttk.Frame(settings_card, style="Card.TFrame")
        output_row.grid(row=3, column=0, sticky="ew", pady=(5, 13))
        output_row.columnconfigure(0, weight=1)
        ttk.Entry(output_row, textvariable=self.output_dir).grid(row=0, column=0, sticky="ew")
        ttk.Button(output_row, text="选择…", style="Secondary.TButton", command=self._choose_output).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(settings_card, text="抠图质量").grid(row=4, column=0, sticky="w")
        quality = ttk.Combobox(settings_card, textvariable=self.quality, state="readonly", values=("精准边缘（1 像素抗锯齿）", "硬边（完全不透明主体）"))
        quality.grid(row=5, column=0, sticky="ew", pady=(5, 9))
        ttk.Checkbutton(settings_card, text="保留原图画布尺寸", variable=self.full_size).grid(row=6, column=0, sticky="w")
        threshold_row = ttk.Frame(settings_card, style="Card.TFrame")
        threshold_row.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(threshold_row, text="识别置信度", style="Muted.TLabel").pack(side="left")
        ttk.Spinbox(threshold_row, from_=0.05, to=0.90, increment=0.05, width=6, textvariable=self.confidence).pack(side="right")

        progress_card = self._card(body)
        progress_card.grid(row=1, column=1, sticky="nsew")
        progress_card.columnconfigure(0, weight=1)
        ttk.Label(progress_card, text="3 处理进度", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        status_row = ttk.Frame(progress_card, style="Card.TFrame")
        status_row.grid(row=1, column=0, sticky="ew", pady=(10, 6))
        status_row.columnconfigure(0, weight=1)
        ttk.Label(status_row, textvariable=self.current_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(status_row, textvariable=self.total_var, style="Muted.TLabel").grid(row=0, column=1, sticky="e")
        self.progress = ttk.Progressbar(progress_card, variable=self.progress_var, maximum=100, mode="determinate", style="Horizontal.TProgressbar")
        self.progress.grid(row=2, column=0, sticky="ew")
        ttk.Label(progress_card, textvariable=self.status_var, style="Muted.TLabel", wraplength=390).grid(row=3, column=0, sticky="w", pady=(9, 11))
        button_row = ttk.Frame(progress_card, style="Card.TFrame")
        ttk.Label(progress_card, textvariable=self.person_var, style="Muted.TLabel").grid(row=4, column=0, sticky="w")
        self.person_progress = ttk.Progressbar(progress_card, variable=self.person_progress_var, maximum=100)
        self.person_progress.grid(row=5, column=0, sticky="ew", pady=(5, 12))
        button_row.grid(row=6, column=0, sticky="ew")
        self.start_button = ttk.Button(button_row, text="开始批量抠图", style="Accent.TButton", command=self._start)
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(button_row, text="停止", style="Danger.TButton", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=(8, 0))
        ttk.Button(button_row, text="打开输出文件夹", style="Secondary.TButton", command=self._open_output).pack(side="right")

        log_card = self._card(outer)
        log_card.pack(side="bottom", fill="x", before=body, pady=(14, 0))
        log_card.columnconfigure(0, weight=1)
        ttk.Label(log_card, text="处理日志", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.log = tk.Text(log_card, height=4, state="disabled", bg="#fbfcfb", fg="#506059", relief="flat", highlightthickness=1, highlightbackground="#dbe5de", font=("Consolas", 9), padx=9, pady=7)
        self.log.grid(row=1, column=0, sticky="ew", pady=(8, 0))

    def _choose_files(self) -> None:
        chosen = filedialog.askopenfilenames(title="选择要抠图的图片", filetypes=[("图片", "*.jpg *.jpeg *.png *.webp"), ("所有文件", "*.*")])
        existing = {str(path) for path in self.files}
        for item in chosen:
            path = Path(item)
            if path.suffix.lower() in SUPPORTED and str(path) not in existing:
                self.files.append(path)
                existing.add(str(path))
        self._refresh_files()
        if self.files and not self.output_dir.get():
            self.output_dir.set(str(self.files[0].parent / "透明PNG输出"))

    def _choose_output(self) -> None:
        chosen = filedialog.askdirectory(title="选择 PNG 输出文件夹")
        if chosen:
            self.output_dir.set(chosen)

    def _refresh_files(self) -> None:
        self.file_list.delete(0, tk.END)
        for path in self.files:
            self.file_list.insert(tk.END, f"{path.name}    ({path.parent})")
        self.total_var.set(f"0 / {len(self.files)}")

    def _remove_selected(self) -> None:
        selected = set(self.file_list.curselection())
        self.files = [path for i, path in enumerate(self.files) if i not in selected]
        self._refresh_files()

    def _clear_files(self) -> None:
        self.files.clear()
        self._refresh_files()

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.files:
            messagebox.showinfo("还没有图片", "请先添加一张或多张图片。")
            return
        output_text = self.output_dir.get().strip()
        if not output_text:
            messagebox.showinfo("还没有输出目录", "请选择 PNG 输出文件夹。")
            return
        output = Path(output_text)
        try:
            confidence = float(self.confidence.get())
            if not 0.05 <= confidence <= 0.90:
                raise ValueError()
        except ValueError:
            messagebox.showinfo("识别置信度无效", "请输入 0.05 到 0.90 之间的数字。")
            return
        try:
            output.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("输出目录不可用", str(exc))
            return
        self.cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress.configure(mode="indeterminate")
        self.progress.start(14)
        self.progress_var.set(0)
        self.total_var.set(f"0 / {len(self.files)}")
        self.person_var.set("人物 0 / 0")
        self.person_progress_var.set(0)
        self.current_var.set("准备模型…")
        self.status_var.set("首次运行可能需要下载模型，之后会直接使用本地缓存。")
        files = list(self.files)
        self.worker = threading.Thread(target=self._process_files, args=(files, output, self.quality.get(), self.full_size.get(), confidence), daemon=True)
        self.worker.start()

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.status_var.set("正在停止，当前模型推理结束后退出…")

    def _process_files(self, files: list[Path], output: Path, quality: str,
                       full_size: bool, confidence: float) -> None:
        try:
            def log(text):
                self.events.put(("log", text))
            if self.engine is None:
                self.engine = PersonCutout(log)
            self.events.put(("model_ready", None))
            total = len(files)
            completed, exported, failed = 0, 0, 0
            for index, path in enumerate(files, 1):
                if self.cancel_event.is_set():
                    break
                self.events.put(("progress", (index - 1, total, path.name, f"正在处理：{path.name}")))
                def person_progress(done, count, text):
                    self.events.put(("person", (done, count, text)))
                try:
                    count, errors = self.engine.process(
                        path, output, hard="硬边" in quality, full_size=full_size,
                        confidence=confidence, cancel=self.cancel_event,
                        progress=person_progress, log=log,
                    )
                    exported += count
                    failed += errors
                except Cancelled:
                    break
                except Exception as exc:
                    failed += 1
                    log(f"{path.name} 处理失败：{exc}")
                completed = index
                self.events.put(("progress", (index, total, path.name, f"已处理 {index}/{total} 张，共导出 {exported} 个人物")))
            self.events.put(("done", (completed, total, exported, failed, output, self.cancel_event.is_set())))
        except Exception as exc:
            self.events.put(("error", f"处理失败：{exc}\n{traceback.format_exc()}"))

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "log":
                    self._append_log(str(payload))
                elif event == "progress":
                    if self.progress.cget("mode") != "determinate":
                        self.progress.stop()
                        self.progress.configure(mode="determinate")
                    done, total, name, status = payload
                    self.total_var.set(f"{done} / {total}")
                    self.current_var.set(str(name))
                    self.status_var.set(str(status))
                    self.progress_var.set(done / total * 100 if total else 0)
                elif event == "person":
                    done, total, text = payload
                    self.person_var.set(f"人物 {done} / {total}" if total else "正在识别人物…")
                    self.status_var.set(text)
                    self.person_progress.stop()
                    if total:
                        self.person_progress.configure(mode="determinate")
                        self.person_progress_var.set(done / total * 100)
                    else:
                        self.person_progress.configure(mode="indeterminate")
                        self.person_progress.start(14)
                elif event == "done":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.person_progress.stop()
                    count, total, exported, failed, output, cancelled = payload
                    self.start_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    if cancelled:
                        self.status_var.set("任务已停止。已完成的文件仍保留在输出目录。")
                        self._append_log("任务已由用户停止。")
                    else:
                        self.progress_var.set(100)
                        self.total_var.set(f"{count} / {total}")
                        summary = f"已导出 {exported} 个人物，失败 {failed} 项。"
                        self.status_var.set(summary + f" 输出目录：{output}")
                        self._append_log(summary)
                elif event == "model_ready":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.status_var.set("模型已就绪，开始按文件处理…")
                elif event == "error":
                    self.person_progress.stop()
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.start_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    self.status_var.set("处理失败，请查看日志。")
                    self._append_log(str(payload))
                    messagebox.showerror("抠图失败", "处理过程中出现错误，详细信息已写入日志。")
        except queue.Empty:
            pass
        self.after(120, self._drain_events)

    def _open_output(self) -> None:
        output_text = self.output_dir.get().strip()
        if not output_text:
            messagebox.showinfo("目录未选择", "请先选择输出文件夹。")
            return
        folder = Path(output_text)
        if folder.exists():
            os.startfile(folder)
        else:
            messagebox.showinfo("目录不存在", "请先选择或处理一次输出目录。")


if __name__ == "__main__":
    app = CutoutApp()
    app.mainloop()
