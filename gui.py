from __future__ import annotations

import logging
import os
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass, replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import customtkinter as ctk

from chunker import ClaudePromptGenerator, Highlight, HighlightRepository, TranscriptChunker
from clipper import ClipGenerator
from downloader import MediaSection, VideoDownloader, build_media_section
from highlight_studio import (
    LENGTH_FILTERS,
    SORT_MODES,
    display_duration,
    duplicate_indices,
    estimate_selected_clips,
    export_csv,
    export_json,
    export_markdown,
    filter_highlights,
    has_length_warning,
    highlight_duration_seconds,
    merge_highlights,
    sort_highlights,
)
from settings import (
    AppSettings,
    SettingsManager,
    VALID_CHUNK_SIZES,
    VALID_CLIP_LENGTHS,
    VALID_CONTENT_PROFILES,
    VALID_THEMES,
    VALID_WHISPER_MODELS,
)
from transcriber import FasterWhisperTranscriber


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkflowRequest:
    url: str
    local_video: Path | None
    section: MediaSection
    whisper_model: str
    clip_length: int
    chunk_size: int


class QueueLogHandler(logging.Handler):
    def __init__(self, event_queue: queue.Queue[tuple[str, Any]]) -> None:
        super().__init__()
        self.event_queue = event_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            self.event_queue.put(("log", message))
        except Exception:
            self.handleError(record)


class VODScoutApp(ctk.CTk):
    def __init__(self, settings_manager: SettingsManager | None = None) -> None:
        self.settings_manager = settings_manager or SettingsManager()
        self.settings = self.settings_manager.load()

        ctk.set_appearance_mode(self.settings.theme)
        ctk.set_default_color_theme("blue")

        super().__init__()
        self.title("VOD Scout")
        self.geometry(self.settings.window_size)
        self.minsize(900, 640)

        self.event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.is_running = False

        self.downloader = VideoDownloader()
        self.clip_generator = ClipGenerator()
        self.transcriber = FasterWhisperTranscriber()
        self.chunker = TranscriptChunker()
        self.prompt_generator = ClaudePromptGenerator()
        self.highlight_repository = HighlightRepository()
        self.highlights: list[Highlight] = []
        self.highlight_by_iid: dict[str, int] = {}
        self.current_highlight_index: int | None = None
        self.ignored_duplicate_pairs: set[frozenset[int]] = set()

        self.url_var = tk.StringVar(value=self.settings.last_url)
        self.file_var = tk.StringVar(value=self.settings.last_selected_file)
        self.start_var = tk.StringVar(value="")
        self.end_var = tk.StringVar(value="")
        self.model_var = tk.StringVar(value=self.settings.whisper_model)
        self.clip_length_var = tk.StringVar(value=str(self.settings.clip_length))
        self.chunk_size_var = tk.StringVar(value=str(self.settings.chunk_size))
        self.theme_var = tk.StringVar(value=self.settings.theme)
        self.content_profile_var = tk.StringVar(value=self.settings.content_profile)
        self.status_var = tk.StringVar(value="Ready")
        self.search_var = tk.StringVar(value="")
        self.sort_var = tk.StringVar(value="Virality")
        self.selected_only_var = tk.BooleanVar(value=False)
        self.min_virality_var = tk.DoubleVar(value=0)
        self.min_confidence_var = tk.DoubleVar(value=0)
        self.virality_filter_label_var = tk.StringVar(value="Virality >= 0")
        self.confidence_filter_label_var = tk.StringVar(value="Confidence >= 0")
        self.length_filter_var = tk.StringVar(value="Any")
        self.estimation_var = tk.StringVar(value="Estimated clips: 0 | Runtime: 00:00:00.000")
        self.duplicate_status_var = tk.StringVar(value="No highlight selected")
        self.edit_title_var = tk.StringVar(value="")
        self.edit_start_var = tk.StringVar(value="")
        self.edit_end_var = tk.StringVar(value="")
        self.edit_virality_var = tk.StringVar(value="")
        self.edit_confidence_var = tk.StringVar(value="")

        self._configure_logging_handler()
        self._build_layout()
        self._bind_settings_events()
        self._load_saved_highlights()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_events)

    def _configure_logging_handler(self) -> None:
        self.gui_log_handler = QueueLogHandler(self.event_queue)
        self.gui_log_handler.setLevel(logging.INFO)
        self.gui_log_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
        )
        logging.getLogger().addHandler(self.gui_log_handler)

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        main = ctk.CTkFrame(self, corner_radius=0)
        main.grid(row=0, column=0, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(5, weight=1)

        title = ctk.CTkLabel(main, text="VOD Scout", font=ctk.CTkFont(size=26, weight="bold"))
        title.grid(row=0, column=0, padx=24, pady=(20, 6), sticky="w")

        source_frame = ctk.CTkFrame(main)
        source_frame.grid(row=1, column=0, padx=24, pady=8, sticky="ew")
        source_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(source_frame, text="Twitch/YouTube URL").grid(
            row=0, column=0, padx=(16, 10), pady=(16, 8), sticky="w"
        )
        self.url_entry = ctk.CTkEntry(source_frame, textvariable=self.url_var)
        self.url_entry.grid(row=0, column=1, columnspan=2, padx=(0, 16), pady=(16, 8), sticky="ew")

        ctk.CTkLabel(source_frame, text="Local Video").grid(
            row=1, column=0, padx=(16, 10), pady=(8, 16), sticky="w"
        )
        self.file_entry = ctk.CTkEntry(source_frame, textvariable=self.file_var)
        self.file_entry.grid(row=1, column=1, padx=(0, 8), pady=(8, 16), sticky="ew")
        self.browse_button = ctk.CTkButton(
            source_frame,
            text="Browse",
            width=110,
            command=self._choose_local_file,
        )
        self.browse_button.grid(row=1, column=2, padx=(0, 16), pady=(8, 16), sticky="e")

        timing_frame = ctk.CTkFrame(main)
        timing_frame.grid(row=2, column=0, padx=24, pady=8, sticky="ew")
        for column in range(8):
            timing_frame.grid_columnconfigure(column, weight=1 if column in {1, 3, 5, 7} else 0)

        ctk.CTkLabel(timing_frame, text="Start Timestamp").grid(
            row=0, column=0, padx=(16, 8), pady=16, sticky="w"
        )
        self.start_entry = ctk.CTkEntry(timing_frame, textvariable=self.start_var, width=130)
        self.start_entry.grid(row=0, column=1, padx=(0, 14), pady=16, sticky="ew")

        ctk.CTkLabel(timing_frame, text="End Timestamp").grid(
            row=0, column=2, padx=(0, 8), pady=16, sticky="w"
        )
        self.end_entry = ctk.CTkEntry(timing_frame, textvariable=self.end_var, width=130)
        self.end_entry.grid(row=0, column=3, padx=(0, 14), pady=16, sticky="ew")

        ctk.CTkLabel(timing_frame, text="Theme").grid(
            row=0, column=4, padx=(0, 8), pady=16, sticky="w"
        )
        self.theme_menu = ctk.CTkOptionMenu(
            timing_frame,
            variable=self.theme_var,
            values=list(VALID_THEMES),
            command=self._change_theme,
            width=120,
        )
        self.theme_menu.grid(row=0, column=5, padx=(0, 14), pady=16, sticky="ew")

        ctk.CTkLabel(timing_frame, text="Content Profile").grid(
            row=0, column=6, padx=(0, 8), pady=16, sticky="w"
        )
        self.profile_menu = ctk.CTkOptionMenu(
            timing_frame,
            variable=self.content_profile_var,
            values=list(VALID_CONTENT_PROFILES),
            command=lambda _: self._save_current_settings(),
            width=130,
        )
        self.profile_menu.grid(row=0, column=7, padx=(0, 16), pady=16, sticky="ew")

        controls_frame = ctk.CTkFrame(main)
        controls_frame.grid(row=3, column=0, padx=24, pady=8, sticky="ew")
        for column in range(6):
            controls_frame.grid_columnconfigure(column, weight=1 if column in {1, 3, 5} else 0)

        ctk.CTkLabel(controls_frame, text="Whisper Model").grid(
            row=0, column=0, padx=(16, 8), pady=16, sticky="w"
        )
        self.model_menu = ctk.CTkOptionMenu(
            controls_frame,
            variable=self.model_var,
            values=list(VALID_WHISPER_MODELS),
            command=lambda _: self._save_current_settings(),
            width=150,
        )
        self.model_menu.grid(row=0, column=1, padx=(0, 14), pady=16, sticky="ew")

        ctk.CTkLabel(controls_frame, text="Clip Length").grid(
            row=0, column=2, padx=(0, 8), pady=16, sticky="w"
        )
        self.clip_menu = ctk.CTkOptionMenu(
            controls_frame,
            variable=self.clip_length_var,
            values=[str(value) for value in VALID_CLIP_LENGTHS],
            command=lambda _: self._save_current_settings(),
            width=110,
        )
        self.clip_menu.grid(row=0, column=3, padx=(0, 14), pady=16, sticky="ew")

        ctk.CTkLabel(controls_frame, text="Transcript Chunk Size").grid(
            row=0, column=4, padx=(0, 8), pady=16, sticky="w"
        )
        self.chunk_menu = ctk.CTkOptionMenu(
            controls_frame,
            variable=self.chunk_size_var,
            values=[str(value) for value in VALID_CHUNK_SIZES],
            command=lambda _: self._save_current_settings(),
            width=110,
        )
        self.chunk_menu.grid(row=0, column=5, padx=(0, 16), pady=16, sticky="ew")

        action_frame = ctk.CTkFrame(main)
        action_frame.grid(row=4, column=0, padx=24, pady=8, sticky="ew")
        action_frame.grid_columnconfigure(0, weight=0)
        action_frame.grid_columnconfigure(1, weight=0)
        action_frame.grid_columnconfigure(2, weight=0)
        action_frame.grid_columnconfigure(3, weight=1)

        self.start_button = ctk.CTkButton(
            action_frame,
            text="Download & Transcribe",
            height=40,
            command=self._start_workflow,
        )
        self.start_button.grid(row=0, column=0, padx=16, pady=16, sticky="w")

        self.generate_prompts_button = ctk.CTkButton(
            action_frame,
            text="Generate Claude Prompts",
            height=40,
            command=self._start_prompt_generation,
        )
        self.generate_prompts_button.grid(row=0, column=1, padx=(0, 12), pady=16, sticky="w")

        self.import_response_button = ctk.CTkButton(
            action_frame,
            text="Import Claude Response",
            height=40,
            command=self._choose_claude_response,
        )
        self.import_response_button.grid(row=0, column=2, padx=(0, 16), pady=16, sticky="w")

        self.generate_clips_button = ctk.CTkButton(
            action_frame,
            text="Generate Clips",
            height=40,
            command=self._start_clip_generation,
            state="disabled",
        )
        self.generate_clips_button.grid(row=0, column=3, padx=(0, 16), pady=16, sticky="w")

        progress_status_frame = ctk.CTkFrame(action_frame, fg_color="transparent")
        progress_status_frame.grid(row=0, column=4, padx=(0, 16), pady=16, sticky="ew")
        progress_status_frame.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(progress_status_frame)
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        self.progress_bar.set(0)

        self.status_label = ctk.CTkLabel(progress_status_frame, textvariable=self.status_var)
        self.status_label.grid(row=1, column=0, pady=(8, 0), sticky="w")

        self.lower_tabs = ctk.CTkTabview(main)
        self.lower_tabs.grid(row=5, column=0, padx=24, pady=(8, 24), sticky="nsew")
        self.lower_tabs.grid_columnconfigure(0, weight=1)
        self.lower_tabs.grid_rowconfigure(0, weight=1)

        log_tab = self.lower_tabs.add("Log")
        highlights_tab = self.lower_tabs.add("Highlights")
        log_tab.grid_columnconfigure(0, weight=1)
        log_tab.grid_rowconfigure(1, weight=1)
        highlights_tab.grid_columnconfigure(0, weight=1)
        highlights_tab.grid_rowconfigure(0, weight=1)

        ctk.CTkLabel(log_tab, text="Log", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, padx=16, pady=(14, 6), sticky="w"
        )
        self.log_box = ctk.CTkTextbox(log_tab, wrap="word")
        self.log_box.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="nsew")
        self.log_box.configure(state="disabled")

        self._build_highlight_dashboard(highlights_tab)

    def _build_highlight_dashboard(self, parent: ctk.CTkFrame) -> None:
        style = ttk.Style(self)
        style.configure("VODScout.Treeview", rowheight=28)
        style.configure("VODScout.Treeview.Heading", font=("Segoe UI", 10, "bold"))

        table_frame = ctk.CTkFrame(parent)
        table_frame.grid(row=0, column=0, padx=16, pady=(14, 8), sticky="nsew")
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)

        columns = ("title", "start", "end", "virality", "confidence", "reason")
        self.highlight_tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            style="VODScout.Treeview",
        )
        self.highlight_tree.heading("title", text="Title")
        self.highlight_tree.heading("start", text="Start")
        self.highlight_tree.heading("end", text="End")
        self.highlight_tree.heading("virality", text="Virality")
        self.highlight_tree.heading("confidence", text="Confidence")
        self.highlight_tree.heading("reason", text="Reason")
        self.highlight_tree.column("title", width=220, minwidth=140, anchor="w")
        self.highlight_tree.column("start", width=90, minwidth=80, anchor="center")
        self.highlight_tree.column("end", width=90, minwidth=80, anchor="center")
        self.highlight_tree.column("virality", width=80, minwidth=70, anchor="center")
        self.highlight_tree.column("confidence", width=90, minwidth=80, anchor="center")
        self.highlight_tree.column("reason", width=420, minwidth=220, anchor="w")
        self.highlight_tree.grid(row=0, column=0, sticky="nsew")
        self.highlight_tree.bind("<<TreeviewSelect>>", self._on_highlight_selected)

        highlight_scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.highlight_tree.yview,
        )
        highlight_scrollbar.grid(row=0, column=1, sticky="ns")
        self.highlight_tree.configure(yscrollcommand=highlight_scrollbar.set)

        self.highlight_detail_box = ctk.CTkTextbox(parent, height=120, wrap="word")
        self.highlight_detail_box.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="ew")
        self.highlight_detail_box.configure(state="disabled")

    def _bind_settings_events(self) -> None:
        self._window_size_save_after_id: str | None = None
        self.url_entry.bind("<FocusOut>", lambda _event: (self._save_current_settings(), self._update_generate_clips_button_state()))
        self.file_entry.bind("<FocusOut>", lambda _event: (self._save_current_settings(), self._update_generate_clips_button_state()))
        self.bind("<Configure>", self._schedule_window_size_save)

    def _load_saved_highlights(self) -> None:
        if not self.highlight_repository.json_path.exists():
            return

        try:
            self.highlights = self._sort_highlights(
                self.highlight_repository.import_from_file(self.highlight_repository.json_path)
            )
        except Exception as exc:
            LOGGER.warning("Saved highlights could not be loaded: %s", exc)
            return

        self._refresh_highlight_dashboard()
        LOGGER.info("Loaded %s saved highlight candidate(s).", len(self.highlights))

    def _choose_local_file(self) -> None:
        initial_dir = str(Path(self.file_var.get()).parent) if self.file_var.get() else str(Path.home())
        file_path = filedialog.askopenfilename(
            title="Choose a video file",
            initialdir=initial_dir,
            filetypes=[
                ("Video files", "*.mp4 *.mkv *.mov *.webm *.avi *.m4v"),
                ("Audio files", "*.mp3 *.wav *.m4a *.aac *.flac"),
                ("All files", "*.*"),
            ],
        )
        if file_path:
            self.file_var.set(file_path)
            self._save_current_settings()
            self._update_generate_clips_button_state()

    def _start_prompt_generation(self) -> None:
        if self.is_running:
            return

        self._save_current_settings()
        self._set_running_state(True)
        self.progress_bar.set(0)
        self.status_var.set("Generating Claude prompts")
        self._append_log("Generating Claude prompts.")

        self.worker_thread = threading.Thread(
            target=self._run_prompt_generation,
            args=(self.content_profile_var.get(),),
            daemon=True,
            name="vod-scout-prompt-generator",
        )
        self.worker_thread.start()

    def _run_prompt_generation(self, content_profile: str) -> None:
        try:
            LOGGER.info("Generating Claude prompts for %s profile.", content_profile)
            prompt_paths = self.prompt_generator.generate_prompts(content_profile)
        except Exception as exc:
            LOGGER.exception("Claude prompt generation failed.")
            self.event_queue.put(("failed", str(exc)))
            return

        self.event_queue.put(("prompt_complete", prompt_paths))

    def _choose_claude_response(self) -> None:
        if self.is_running:
            return

        response_path = filedialog.askopenfilename(
            title="Import Claude Response",
            initialdir=str(self.highlight_repository.reports_dir),
            filetypes=[
                ("Claude response", "*.json *.txt"),
                ("JSON files", "*.json"),
                ("Text files", "*.txt"),
            ],
        )
        if not response_path:
            return

        self._set_running_state(True)
        self.progress_bar.set(0)
        self.status_var.set("Importing Claude response")
        self._append_log(f"Importing Claude response: {response_path}")

        self.worker_thread = threading.Thread(
            target=self._run_highlight_import,
            args=(Path(response_path),),
            daemon=True,
            name="vod-scout-highlight-importer",
        )
        self.worker_thread.start()

    def _run_highlight_import(self, response_path: Path) -> None:
        try:
            highlights = self.highlight_repository.import_from_file(response_path)
            json_path, csv_path = self.highlight_repository.save(highlights)
        except Exception as exc:
            LOGGER.exception("Claude response import failed.")
            self.event_queue.put(("failed", str(exc)))
            return

        self.event_queue.put(
            (
                "highlights_complete",
                {
                    "highlights": highlights,
                    "json": json_path,
                    "csv": csv_path,
                },
            )
        )

    def _change_theme(self, theme: str) -> None:
        ctk.set_appearance_mode(theme)
        self._save_current_settings()

    def _schedule_window_size_save(self, event: tk.Event[Any]) -> None:
        if event.widget is not self:
            return
        if self._window_size_save_after_id is not None:
            self.after_cancel(self._window_size_save_after_id)
        self._window_size_save_after_id = self.after(600, self._save_current_settings)

    def _start_workflow(self) -> None:
        if self.is_running:
            return

        try:
            request = self._build_workflow_request()
        except Exception as exc:
            messagebox.showerror("VOD Scout", str(exc))
            return

        self._save_current_settings()
        self._set_running_state(True)
        self.progress_bar.set(0)
        self.status_var.set("Starting")
        self._append_log("Starting Day 1 workflow.")

        self.worker_thread = threading.Thread(
            target=self._run_workflow,
            args=(request,),
            daemon=True,
            name="vod-scout-worker",
        )
        self.worker_thread.start()

    def _build_workflow_request(self) -> WorkflowRequest:
        url = self.url_var.get().strip()
        local_text = self.file_var.get().strip()
        local_video = Path(local_text) if local_text else None

        if not url and local_video is None:
            raise ValueError("Enter a Twitch/YouTube URL or choose a local video.")

        if url and local_video is not None:
            LOGGER.info("URL was provided; the local video selection will be ignored for this run.")
            local_video = None

        if local_video is not None and not local_video.exists():
            raise FileNotFoundError(f"Selected local video does not exist: {local_video}")

        section = build_media_section(self.start_var.get(), self.end_var.get())
        return WorkflowRequest(
            url=url,
            local_video=local_video,
            section=section,
            whisper_model=self.model_var.get(),
            clip_length=int(self.clip_length_var.get()),
            chunk_size=int(self.chunk_size_var.get()),
        )

    def _run_workflow(self, request: WorkflowRequest) -> None:
        try:
            LOGGER.info(
                "Workflow settings: model=%s, chunk=%s minutes, clip length=%s seconds",
                request.whisper_model,
                request.chunk_size,
                request.clip_length,
            )

            if request.url:
                video_path = self.downloader.download_url(
                    request.url,
                    request.section,
                    self._phase_progress(0.0, 0.30),
                )
            elif request.local_video is not None:
                video_path = self.downloader.prepare_local_video(
                    request.local_video,
                    request.section,
                    self._phase_progress(0.0, 0.30),
                )
            else:
                raise ValueError("No source media was provided.")

            transcription = self.transcriber.transcribe(
                video_path,
                request.whisper_model,
                self._phase_progress(0.30, 0.55),
            )
            chunks = self.chunker.split_and_save(
                transcription,
                request.chunk_size,
                self._phase_progress(0.85, 0.15),
            )
        except Exception as exc:
            LOGGER.exception("Workflow failed.")
            self.event_queue.put(("failed", str(exc)))
            return

        self.event_queue.put(
            (
                "complete",
                {
                    "video": video_path,
                    "transcript": transcription.text_path,
                    "json": transcription.json_path,
                    "chunks": [chunk.path for chunk in chunks],
                },
            )
        )

    def _phase_progress(self, base: float, span: float) -> Callable[[float, str], None]:
        def callback(fraction: float, message: str) -> None:
            clean_fraction = max(0.0, min(float(fraction), 1.0))
            overall = max(0.0, min(base + clean_fraction * span, 1.0))
            self.event_queue.put(("progress", (overall, message)))

        return callback

    def _poll_events(self) -> None:
        while True:
            try:
                event_type, payload = self.event_queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "log":
                self._append_log(str(payload))
            elif event_type == "progress":
                fraction, message = payload
                self.progress_bar.set(float(fraction))
                self.status_var.set(str(message))
            elif event_type == "failed":
                self._set_running_state(False)
                self.progress_bar.set(0)
                self.status_var.set("Failed")
                messagebox.showerror("VOD Scout", str(payload))
            elif event_type == "complete":
                self._handle_complete(payload)
            elif event_type == "clips_complete":
                self._handle_clips_complete(payload)
            elif event_type == "prompt_complete":
                self._handle_prompt_complete(payload)
            elif event_type == "highlights_complete":
                self._handle_highlights_complete(payload)

        self.after(100, self._poll_events)

    def _handle_clips_complete(self, payload: dict[str, Any]) -> None:
        self._set_running_state(False)
        self.progress_bar.set(1)
        generated_paths = payload["paths"]
        elapsed_time = payload["elapsed"]
        count = len(generated_paths)
        
        self.status_var.set(f"Complete: {count} clip(s) generated")
        LOGGER.info("Clip generation complete. Elapsed time: %.2f seconds", elapsed_time)
        self._append_log(f"Clip generation complete. Elapsed time: {elapsed_time:.2f} seconds")
        for path in generated_paths:
            self._append_log(f"Clip: {path.name}")
        self._update_generate_clips_button_state()

        msg = f"Generated {count} clip(s) successfully.\nElapsed time: {elapsed_time:.2f} seconds."
        messagebox.showinfo("Generation Complete", msg)
        self._open_clips_folder()

    def _handle_complete(self, payload: dict[str, Any]) -> None:
        self._set_running_state(False)
        self.progress_bar.set(1)
        chunk_count = len(payload.get("chunks", []))
        self.status_var.set(f"Complete: {chunk_count} chunk file(s) saved")
        LOGGER.info("Workflow complete.")
        self.file_var.set(str(payload["video"]))
        self._append_log(f"Video: {payload['video']}")
        self._append_log(f"Transcript: {payload['transcript']}")
        self._append_log(f"Transcript JSON: {payload['json']}")
        for chunk_path in payload.get("chunks", []):
            self._append_log(f"Chunk: {chunk_path}")
        self._update_generate_clips_button_state()

    def _handle_prompt_complete(self, prompt_paths: list[Path]) -> None:
        self._set_running_state(False)
        self.progress_bar.set(1)
        self.status_var.set(f"Complete: {len(prompt_paths)} Claude prompt file(s) saved")
        LOGGER.info("Claude prompt generation complete.")
        for prompt_path in prompt_paths:
            self._append_log(f"Claude prompt: {prompt_path}")
        self._update_generate_clips_button_state()

    def _handle_highlights_complete(self, payload: dict[str, Any]) -> None:
        self._set_running_state(False)
        self.progress_bar.set(1)
        self.highlights = self._sort_highlights(payload["highlights"])
        self._refresh_highlight_dashboard()
        self.lower_tabs.set("Highlights")
        self._update_generate_clips_button_state()

        count = len(self.highlights)
        self.status_var.set(f"Complete: {count} highlight candidate(s) imported")
        LOGGER.info("Highlight import complete.")
        self._append_log(f"Highlights JSON: {payload['json']}")
        self._append_log(f"Highlights CSV: {payload['csv']}")

    def _start_clip_generation(self) -> None:
        if self.is_running:
            return

        source_text = self.file_var.get().strip()
        if not source_text:
            messagebox.showerror("VOD Scout", "Please provide a valid local video path.")
            return

        source_path = Path(source_text)
        if not source_path.exists():
            messagebox.showerror("VOD Scout", "Local video file not found.")
            return

        self._set_running_state(True)
        self.progress_bar.set(0)
        self.status_var.set("Generating clips")
        self._append_log("Starting clip generation.")
        self.start_time = time.time()

        self.worker_thread = threading.Thread(
            target=self._run_clip_generation,
            args=(self.highlights, source_path),
            daemon=True,
            name="vod-scout-clipper",
        )
        self.worker_thread.start()

    def _run_clip_generation(self, highlights: list[Highlight], source_path: Path) -> None:
        try:
            generated_paths = self.clip_generator.generate_selected(
                highlights,
                source_path,
                self._clip_progress_callback(),
            )
            elapsed_time = time.time() - self.start_time
            self.event_queue.put(("clips_complete", {"paths": generated_paths, "elapsed": elapsed_time}))
        except Exception as exc:
            LOGGER.exception("Clip generation failed.")
            self.event_queue.put(("failed", str(exc)))

    def _clip_progress_callback(self) -> Callable[[float, str], None]:
        def callback(fraction: float, message: str) -> None:
            self.event_queue.put(("progress", (fraction, message)))
        return callback

    def _refresh_highlight_dashboard(self) -> None:
        for item_id in self.highlight_tree.get_children():
            self.highlight_tree.delete(item_id)

        self.highlight_by_iid = {}
        for index, highlight in enumerate(self.highlights):
            item_id = f"highlight_{index}"
            self.highlight_by_iid[item_id] = highlight
            self.highlight_tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    highlight.title,
                    highlight.start,
                    highlight.end,
                    self._format_score(highlight.virality),
                    self._format_score(highlight.confidence),
                    highlight.reason,
                ),
            )

        if self.highlights:
            first_item_id = "highlight_0"
            self.highlight_tree.selection_set(first_item_id)
            self.highlight_tree.focus(first_item_id)
            self.highlight_tree.see(first_item_id)
            self._show_highlight_details(self.highlights[0])
        else:
            self._clear_highlight_details()

    def _on_highlight_selected(self, _event: tk.Event[Any]) -> None:
        selected_items = self.highlight_tree.selection()
        if not selected_items:
            self._clear_highlight_details()
            return

        highlight = self.highlight_by_iid.get(selected_items[0])
        if highlight is not None:
            self._show_highlight_details(highlight)

    def _show_highlight_details(self, highlight: Highlight) -> None:
        detail_text = (
            f"Title: {highlight.title}\n"
            f"Start: {highlight.start}\n"
            f"End: {highlight.end}\n"
            f"Virality: {self._format_score(highlight.virality)}\n"
            f"Confidence: {self._format_score(highlight.confidence)}\n\n"
            f"Reason:\n{highlight.reason}"
        )
        self.highlight_detail_box.configure(state="normal")
        self.highlight_detail_box.delete("1.0", "end")
        self.highlight_detail_box.insert("1.0", detail_text)
        self.highlight_detail_box.configure(state="disabled")

    def _clear_highlight_details(self) -> None:
        self.highlight_detail_box.configure(state="normal")
        self.highlight_detail_box.delete("1.0", "end")
        self.highlight_detail_box.configure(state="disabled")

    def _sort_highlights(self, highlights: list[Highlight]) -> list[Highlight]:
        return sorted(highlights, key=lambda highlight: highlight.virality, reverse=True)

    def _format_score(self, value: float) -> str:
        return str(int(value)) if value.is_integer() else f"{value:.2f}"

    def _set_running_state(self, running: bool) -> None:
        self.is_running = running
        state = "disabled" if running else "normal"
        self.start_button.configure(state=state)
        self.generate_prompts_button.configure(state=state)
        self.import_response_button.configure(state=state)
        self.generate_clips_button.configure(state=state)
        self.browse_button.configure(state=state)
        self.url_entry.configure(state=state)
        self.file_entry.configure(state=state)
        self.start_entry.configure(state=state)
        self.end_entry.configure(state=state)
        self.model_menu.configure(state=state)
        self.clip_menu.configure(state=state)
        self.chunk_menu.configure(state=state)
        self.theme_menu.configure(state=state)
        self.profile_menu.configure(state=state)

    def _update_generate_clips_button_state(self) -> None:
        source_available = bool(self.url_var.get().strip() or self.file_var.get().strip())
        highlights_available = len(self.highlights) > 0
        state = "normal" if source_available and highlights_available else "disabled"
        self.generate_clips_button.configure(state=state)

    def _append_log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _save_current_settings(self) -> None:
        size = self.geometry().split("+", 1)[0]
        settings = AppSettings(
            last_url=self.url_var.get().strip(),
            last_selected_file=self.file_var.get().strip(),
            window_size=size,
            theme=self.theme_var.get(),
            content_profile=self.content_profile_var.get(),
            clip_length=int(self.clip_length_var.get()),
            whisper_model=self.model_var.get(),
            chunk_size=int(self.chunk_size_var.get()),
        )
        self.settings = settings
        try:
            self.settings_manager.save(settings)
        except OSError as exc:
            LOGGER.warning("Could not save settings: %s", exc)

    def _open_clips_folder(self) -> None:
        if os.name == "nt":
            os.startfile(self.clip_generator.clips_dir)
        else:
            import subprocess
            subprocess.Popen(["xdg-open", self.clip_generator.clips_dir])

    def _on_close(self) -> None:
        """Cleanly close the application."""
        try:
            logging.getLogger().removeHandler(self.gui_log_handler)
        except Exception:
            pass

        self.destroy()