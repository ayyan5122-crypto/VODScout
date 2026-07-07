from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from settings import DOWNLOADS_DIR


def _get_resource_path(relative: str) -> Path:
    """Return an absolute path to a bundled resource.

    * PyInstaller one-file build: resources are extracted to ``sys._MEIPASS``
      at runtime, so we resolve against that directory.
    * Normal Python / VS Code: we resolve against the directory that contains
      this source file (i.e. the project root when the standard layout is used).
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).parent
    return base / relative


LOGGER = logging.getLogger(__name__)
ProgressCallback = Callable[[float, str], None]


@dataclass(frozen=True, slots=True)
class MediaSection:
    start_seconds: float | None = None
    end_seconds: float | None = None

    @property
    def is_active(self) -> bool:
        return self.start_seconds is not None or self.end_seconds is not None

    def validate(self) -> None:
        if self.start_seconds is not None and self.start_seconds < 0:
            raise ValueError("Start timestamp cannot be negative.")
        if self.end_seconds is not None and self.end_seconds <= 0:
            raise ValueError("End timestamp must be greater than zero.")
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds <= self.start_seconds
        ):
            raise ValueError("End timestamp must be after the start timestamp.")

    def label(self) -> str:
        self.validate()
        if not self.is_active:
            return "full media"
        start = format_timestamp(self.start_seconds or 0)
        end = format_timestamp(self.end_seconds) if self.end_seconds is not None else "end"
        return f"{start} to {end}"


class YTDLPLogger:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def debug(self, message: str) -> None:
        if message.startswith("[debug] "):
            self._logger.debug(message)
        else:
            self._logger.info(message)

    def warning(self, message: str) -> None:
        self._logger.warning(message)

    def error(self, message: str) -> None:
        self._logger.error(message)


class VideoDownloader:
    def __init__(
        self,
        downloads_dir: Path = DOWNLOADS_DIR,
        ffmpeg_executable: str = "ffmpeg",
    ) -> None:
        self.downloads_dir = downloads_dir
        self.ffmpeg_executable = ffmpeg_executable
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

    def download_url(
        self,
        url: str,
        section: MediaSection,
        progress_callback: ProgressCallback | None = None,
    ) -> Path:
        clean_url = url.strip()
        if not clean_url:
            raise ValueError("A URL is required for download.")

        section.validate()
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        LOGGER.info("Starting URL download: %s", clean_url)
        if section.is_active:
            LOGGER.info("Requested URL section: %s", section.label())
            

        if "twitch.tv/videos/" in clean_url and section.is_active:
            return self._download_with_twitch_downloader(clean_url, section, progress_callback)

        try:
            from yt_dlp import YoutubeDL
            from yt_dlp.utils import download_range_func
        except ImportError as exc:
            raise RuntimeError(
                "yt-dlp is not installed. Install dependencies from requirements.txt."
            ) from exc

        ydl_options: dict[str, Any] = {
            "format": "bv*+ba/best",
            "merge_output_format": "mp4",
            "outtmpl": str(self.downloads_dir / "%(title).180B [%(id)s].%(ext)s"),
            "noplaylist": True,
            "logger": YTDLPLogger(LOGGER),
            "progress_hooks": [self._make_yt_dlp_progress_hook(progress_callback)],
            "quiet": False,
            "no_warnings": False,
            "retries": 3,
            "fragment_retries": 3,
        }

        if section.is_active:
            ranges = [(section.start_seconds, section.end_seconds)]
            ydl_options["download_ranges"] = download_range_func(None, ranges)
            ydl_options["force_keyframes_at_cuts"] = True

        try:
            with YoutubeDL(ydl_options) as ydl:
                info = ydl.extract_info(clean_url, download=True)
                downloaded_path = self._resolve_downloaded_path(ydl, info)
        except Exception as exc:
            raise RuntimeError(f"Download failed: {exc}") from exc

        return downloaded_path

    def _download_with_twitch_downloader(
        self,
        url: str,
        section: MediaSection,
        progress_callback: ProgressCallback | None = None,
    ) -> Path:
        LOGGER.info("Starting TwitchDownloaderCLI download...")
        if progress_callback is not None:
            progress_callback(0.0, "Starting TwitchDownloaderCLI download...")

        match = re.search(r"twitch\.tv/videos/(\d+)", url)
        video_id = match.group(1) if match else "unknown"
        
        output_name = f"twitch_vod_{video_id}_{section_suffix(section)}.mp4"
        output_path = self.downloads_dir / output_name

        twitch_cli = str(_get_resource_path("tools/TwitchDownloaderCLI.exe"))
        cmd = [
            twitch_cli,
            "videodownload",
            "--id", url,
            "-o", str(output_path),
        ]

        LOGGER.warning("=== COMMAND BEFORE TIMESTAMPS ===")
        LOGGER.warning(cmd)

        if section.start_seconds is not None:
            start = format_timestamp(section.start_seconds)
            LOGGER.warning(f"START = {start}")
            cmd.extend(["--beginning", start])

        if section.end_seconds is not None:
            end = format_timestamp(section.end_seconds)
            LOGGER.warning(f"END = {end}")
            cmd.extend(["--ending", end])

        cmd.extend(["--trim-mode", "Exact", "--collision", "Overwrite"])
        
        LOGGER.warning("FINAL COMMAND:")
        LOGGER.warning(" ".join(cmd))
        # Prevent Windows from opening a black console window for the child process.
        # CREATE_NO_WINDOW is a Windows-only creation flag; guard it so the code
        # remains safe on Linux / macOS (e.g. during development or CI).
        _creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            completed = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_creation_flags,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"TwitchDownloaderCLI executable not found at: {twitch_cli}"
            ) from exc
        except subprocess.CalledProcessError as exc:
            error_output = (exc.stderr or exc.stdout or "").strip()
            raise RuntimeError(f"TwitchDownloaderCLI failed: {error_output}") from exc

        if completed.stderr:
            LOGGER.debug("TwitchDownloaderCLI output: %s", completed.stderr.strip())

        if not output_path.exists():
            raise FileNotFoundError("TwitchDownloaderCLI completed, but output file was not found.")

        LOGGER.info("Finished TwitchDownloaderCLI download.")
        if progress_callback is not None:
            progress_callback(1.0, "Finished TwitchDownloaderCLI download.")

        return output_path

    def _make_yt_dlp_progress_hook(
        self,
        progress_callback: ProgressCallback | None,
    ) -> Callable[[dict[str, Any]], None]:
        def progress_hook(status: dict[str, Any]) -> None:
            if progress_callback is None:
                return

            state = status.get("status")

            if state == "downloading":
                downloaded = float(status.get("downloaded_bytes") or 0)
                total = float(
                    status.get("total_bytes")
                    or status.get("total_bytes_estimate")
                    or 0
                )

                fraction = downloaded / total if total > 0 else 0.0

                message = "Downloading"

                if status.get("_percent_str"):
                    message = f"Downloading {str(status['_percent_str']).strip()}"

                progress_callback(max(0.0, min(fraction, 0.98)), message)

            elif state == "finished":
                progress_callback(1.0, "Download finished; finalizing media")

        return progress_hook

    def _resolve_downloaded_path(self, ydl: Any, info: dict[str, Any] | None) -> Path:
        candidates: list[Path] = []

        def collect_from_info(info_dict: dict[str, Any] | None) -> None:
            if not info_dict:
                return
            for requested in info_dict.get("requested_downloads") or []:
                for key in ("filepath", "filename"):
                    value = requested.get(key)
                    if value:
                        candidates.append(Path(value))
            for key in ("filepath", "filename"):
                value = info_dict.get(key)
                if value:
                    candidates.append(Path(value))
            try:
                candidates.append(Path(ydl.prepare_filename(info_dict)))
            except Exception:
                LOGGER.debug("yt-dlp prepare_filename did not return a candidate.", exc_info=True)

        if info and info.get("_type") == "playlist":
            for entry in info.get("entries") or []:
                collect_from_info(entry)
        else:
            collect_from_info(info)

        expanded_candidates: list[Path] = []
        for candidate in candidates:
            expanded_candidates.append(candidate)
            if candidate.suffix.lower() != ".mp4":
                expanded_candidates.append(candidate.with_suffix(".mp4"))

        for candidate in expanded_candidates:
            if candidate.exists() and candidate.is_file():
                return candidate

        media_files = [
            path
            for path in self.downloads_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4a"}
        ]
        if media_files:
            return max(media_files, key=lambda path: path.stat().st_mtime)

        raise FileNotFoundError("Download completed, but the output media file could not be found.")

    def _copy_with_progress(
        self,
        source: Path,
        destination: Path,
        progress_callback: ProgressCallback | None,
    ) -> None:
        total_size = source.stat().st_size
        copied_size = 0
        chunk_size = 1024 * 1024

        with source.open("rb") as src, destination.open("wb") as dst:
            while True:
                chunk = src.read(chunk_size)
                if not chunk:
                    break
                dst.write(chunk)
                copied_size += len(chunk)
                if progress_callback is not None and total_size > 0:
                    fraction = min(copied_size / total_size, 1.0)
                    progress_callback(fraction, f"Copying local video {fraction:.0%}")

        shutil.copystat(source, destination)
        if progress_callback is not None:
            progress_callback(1.0, f"Local video ready: {destination.name}")

    def _extract_section(self, source: Path, destination: Path, section: MediaSection) -> None:
        LOGGER.info("Extracting local section %s", section.label())
        copy_args = self._build_ffmpeg_args(source, destination, section, stream_copy=True)
        try:
            self._run_ffmpeg(copy_args)
            return
        except RuntimeError:
            LOGGER.warning("Fast section extraction failed; retrying with encoding.")

        encode_args = self._build_ffmpeg_args(source, destination, section, stream_copy=False)
        self._run_ffmpeg(encode_args)

    def _build_ffmpeg_args(
        self,
        source: Path,
        destination: Path,
        section: MediaSection,
        stream_copy: bool,
    ) -> list[str]:
        args = [self.ffmpeg_executable, "-y"]
        if section.start_seconds is not None:
            args.extend(["-ss", format_timestamp(section.start_seconds)])

        args.extend(["-i", str(source)])

        if section.end_seconds is not None:
            if section.start_seconds is not None:
                duration = section.end_seconds - section.start_seconds
                args.extend(["-t", f"{duration:.3f}"])
            else:
                args.extend(["-to", format_timestamp(section.end_seconds)])

        args.extend(["-map", "0:v:0?", "-map", "0:a:0?", "-sn", "-dn"])
        if stream_copy:
            args.extend(["-c", "copy", "-avoid_negative_ts", "make_zero"])
        else:
            args.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "23",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "160k",
                    "-movflags",
                    "+faststart",
                ]
            )
        args.append(str(destination))
        return args

    def _run_ffmpeg(self, args: list[str]) -> None:
        try:
            completed = subprocess.run(
                args,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise RuntimeError("FFmpeg was not found. Install FFmpeg and add it to PATH.") from exc
        except subprocess.CalledProcessError as exc:
            error_output = (exc.stderr or exc.stdout or "").strip()
            raise RuntimeError(f"FFmpeg failed: {error_output}") from exc

        if completed.stderr:
            LOGGER.debug("FFmpeg output: %s", completed.stderr.strip())

    def _unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path

        for index in range(1, 10_000):
            candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
            if not candidate.exists():
                return candidate

        raise RuntimeError(f"Could not create a unique file name for {path.name}.")


def parse_timestamp(value: str) -> float | None:
    clean_value = value.strip()
    if not clean_value:
        return None

    if re.fullmatch(r"\d+(\.\d+)?", clean_value):
        return float(clean_value)

    parts = clean_value.split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"Invalid timestamp: {value}")

    try:
        numeric_parts = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp: {value}") from exc

    if any(part < 0 for part in numeric_parts):
        raise ValueError("Timestamps cannot contain negative values.")
    if len(parts) > 1 and any(part >= 60 for part in numeric_parts[1:]):
        raise ValueError("Minutes and seconds must be less than 60.")

    total_seconds = 0.0
    for part in numeric_parts:
        total_seconds = total_seconds * 60 + part
    return total_seconds


def build_media_section(start_value: str, end_value: str) -> MediaSection:
    section = MediaSection(
        start_seconds=parse_timestamp(start_value),
        end_seconds=parse_timestamp(end_value),
    )
    section.validate()
    return section


def format_timestamp(seconds: float | None) -> str:
    if seconds is None:
        return "00:00:00.000"

    safe_seconds = max(0.0, float(seconds))
    hours = int(safe_seconds // 3600)
    minutes = int((safe_seconds % 3600) // 60)
    remaining_seconds = safe_seconds - (hours * 3600) - (minutes * 60)
    return f"{hours:02d}:{minutes:02d}:{remaining_seconds:06.3f}"


def section_suffix(section: MediaSection) -> str:
    start = _filename_timestamp(section.start_seconds or 0)
    end = _filename_timestamp(section.end_seconds) if section.end_seconds is not None else "end"
    return f"{start}_to_{end}"


def sanitize_filename(value: str) -> str:
    clean_value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value)
    clean_value = re.sub(r"\s+", " ", clean_value).strip(" .")
    return clean_value or "video"


def _filename_timestamp(seconds: float | None) -> str:
    if seconds is None:
        return "end"
    whole_seconds = int(max(0.0, seconds))
    hours = whole_seconds // 3600
    minutes = (whole_seconds % 3600) // 60
    remaining_seconds = whole_seconds % 60
    return f"{hours:02d}-{minutes:02d}-{remaining_seconds:02d}"