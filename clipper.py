from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from chunker import Highlight
from downloader import (
    VideoDownloader,
    parse_timestamp,
    sanitize_filename,
    format_timestamp,
    MediaSection,
)
from settings import CLIPS_DIR

LOGGER = logging.getLogger(__name__)

# Reusing type hint from downloader.py
ProgressCallback = Callable[[float, str], None]


class ClipGenerator:
    """
    Generates clips from video files based on provided highlights.
    """

    def __init__(self, clips_dir: Path = CLIPS_DIR) -> None:
        self.downloader = VideoDownloader()
        self.clips_dir = clips_dir
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.padding_before = 8.0
        self.padding_after = 6.0

    def generate_clip(
        self,
        highlight: Highlight,
        source_path: Path,
        progress_callback: ProgressCallback | None = None,
    ) -> Path:
        """
        Generates a single clip from the source video based on the highlight.
        """
        if progress_callback:
            progress_callback(0.0, f"Generating clip: {highlight.title}")

        start_seconds = parse_timestamp(highlight.start) or 0.0
        end_seconds = parse_timestamp(highlight.end) or start_seconds + 1.0

        # Apply padding and clamp start time
        start = max(0.0, start_seconds - self.padding_before)
        end = end_seconds + self.padding_after

        section = MediaSection(start_seconds=start, end_seconds=end)
        
        output_filename = f"{sanitize_filename(highlight.title)}.mp4"
        output_path = self.clips_dir / output_filename
        
        # Ensure unique filename if it exists
        if output_path.exists():
            for i in range(1, 1000):
                output_path = self.clips_dir / f"{sanitize_filename(highlight.title)}_{i}.mp4"
                if not output_path.exists():
                    break
        
        LOGGER.info("Generating clip: %s (%s)", highlight.title, section.label())
        
        # Reuse existing extraction logic from VideoDownloader
        # We need to access the private _extract_section method which is available
        # on the downloader instance.
        self.downloader._extract_section(source_path, output_path, section)

        if progress_callback:
            progress_callback(1.0, f"Generated clip: {highlight.title}")

        return output_path

    def generate_selected(
        self,
        highlights: list[Highlight],
        source_path: Path,
        progress_callback: ProgressCallback | None = None,
    ) -> list[Path]:
        """
        Generates clips for all selected highlights in the list.
        """
        selected_highlights = [h for h in highlights if h.selected]
        generated_paths: list[Path] = []
        total = len(selected_highlights)

        for index, highlight in enumerate(selected_highlights):
            try:
                if progress_callback:
                    progress_callback(index / total, f"Processing {highlight.title}")
                
                path = self.generate_clip(highlight, source_path, None)
                generated_paths.append(path)
            except Exception as exc:
                LOGGER.error("Failed to generate clip for %s: %s", highlight.title, exc)
                # Continue batch generation if one clip fails

        if progress_callback:
            progress_callback(1.0, f"Generated {len(generated_paths)} clips")

        return generated_paths
