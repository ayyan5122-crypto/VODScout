from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from downloader import format_timestamp, sanitize_filename
from settings import TRANSCRIPTS_DIR


LOGGER = logging.getLogger(__name__)
ProgressCallback = Callable[[float, str], None]


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    index: int
    start: float
    end: float
    text: str


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    video_path: Path
    text_path: Path
    json_path: Path
    model_name: str
    language: str
    duration_seconds: float
    segments: list[TranscriptSegment]


class FasterWhisperTranscriber:
    def __init__(self, transcripts_dir: Path = TRANSCRIPTS_DIR) -> None:
        self.transcripts_dir = transcripts_dir
        self.transcripts_dir.mkdir(parents=True, exist_ok=True)

    def transcribe(
        self,
        video_path: Path,
        model_name: str,
        progress_callback: ProgressCallback | None = None,
    ) -> TranscriptionResult:
        source = Path(video_path)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Video file was not found: {source}")

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "Faster-Whisper is not installed. Install dependencies from requirements.txt."
            ) from exc

        LOGGER.info("Loading Faster-Whisper model: %s", model_name)
        if progress_callback is not None:
            progress_callback(0.02, f"Loading Whisper model: {model_name}")

        try:
            model = WhisperModel(model_name, device="auto", compute_type="default")
            segment_iterator, info = model.transcribe(
                str(source),
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
        except Exception as exc:
            raise RuntimeError(f"Transcription could not start: {exc}") from exc

        duration = float(getattr(info, "duration", 0.0) or 0.0)
        language = str(getattr(info, "language", "unknown") or "unknown")
        LOGGER.info(
            "Transcribing %.1f seconds of media; detected language: %s",
            duration,
            language,
        )

        segments: list[TranscriptSegment] = []
        try:
            for index, segment in enumerate(segment_iterator, start=1):
                text = str(segment.text).strip()
                transcript_segment = TranscriptSegment(
                    index=index,
                    start=float(segment.start),
                    end=float(segment.end),
                    text=text,
                )
                segments.append(transcript_segment)

                if progress_callback is not None:
                    if duration > 0:
                        fraction = min(max(transcript_segment.end / duration, 0.03), 0.98)
                        message = (
                            "Transcribing "
                            f"{format_timestamp(transcript_segment.end)} / {format_timestamp(duration)}"
                        )
                    else:
                        fraction = min(0.98, 0.03 + len(segments) * 0.01)
                        message = f"Transcribed {len(segments)} segments"
                    progress_callback(fraction, message)
        except Exception as exc:
            raise RuntimeError(f"Transcription failed: {exc}") from exc

        text_path, json_path = self._write_transcript(
            source,
            model_name,
            language,
            duration,
            segments,
        )

        if progress_callback is not None:
            progress_callback(1.0, f"Transcript saved: {text_path.name}")
        LOGGER.info("Transcript saved to %s", text_path)

        return TranscriptionResult(
            video_path=source,
            text_path=text_path,
            json_path=json_path,
            model_name=model_name,
            language=language,
            duration_seconds=duration,
            segments=segments,
        )

    def _write_transcript(
        self,
        source: Path,
        model_name: str,
        language: str,
        duration: float,
        segments: list[TranscriptSegment],
    ) -> tuple[Path, Path]:
        base_name = sanitize_filename(f"{source.stem}_transcript")
        text_path = self._unique_path(self.transcripts_dir / f"{base_name}.txt")
        json_path = text_path.with_suffix(".json")

        with text_path.open("w", encoding="utf-8") as file:
            file.write("VOD Scout Transcript\n")
            file.write(f"Source: {source.name}\n")
            file.write(f"Model: {model_name}\n")
            file.write(f"Language: {language}\n")
            file.write(f"Duration: {format_timestamp(duration)}\n\n")
            if segments:
                for segment in segments:
                    file.write(
                        f"[{format_timestamp(segment.start)} --> "
                        f"{format_timestamp(segment.end)}] {segment.text}\n"
                    )
            else:
                file.write("No speech was detected in this media.\n")

        payload = {
            "source": str(source),
            "model": model_name,
            "language": language,
            "duration_seconds": duration,
            "segments": [asdict(segment) for segment in segments],
        }
        with json_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
            file.write("\n")

        return text_path, json_path

    def _unique_path(self, path: Path) -> Path:
        if not path.exists() and not path.with_suffix(".json").exists():
            return path

        for index in range(1, 10_000):
            candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
            if not candidate.exists() and not candidate.with_suffix(".json").exists():
                return candidate

        raise RuntimeError(f"Could not create a unique transcript name for {path.name}.")
