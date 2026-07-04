from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from downloader import format_timestamp, sanitize_filename
from settings import PROMPTS_DIR, REPORTS_DIR, VALID_CONTENT_PROFILES
from transcriber import TranscriptSegment, TranscriptionResult


LOGGER = logging.getLogger(__name__)
ProgressCallback = Callable[[float, str], None]


@dataclass(frozen=True, slots=True)
class TranscriptChunk:
    index: int
    start: float
    end: float
    segments: list[TranscriptSegment]
    path: Path


@dataclass(frozen=True, slots=True)
class Highlight:
    title: str
    start: str
    end: str
    reason: str
    virality: float
    confidence: float
    selected: bool = True
    favorite: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, str | float | bool]:
        return {
            "title": self.title,
            "start": self.start,
            "end": self.end,
            "reason": self.reason,
            "virality": self.virality,
            "confidence": self.confidence,
            "selected": self.selected,
            "favorite": self.favorite,
            "notes": self.notes,
        }


PROFILE_PROMPT_GUIDANCE = {
    "Horror": (
        "- Jump scares\n"
        "- Loud screams\n"
        "- Panic\n"
        "- Nervous laughter\n"
        "- Unexpected monster encounters\n"
        "- Funny fails\n"
        "- Plot twists\n"
        "- Strong emotional reactions"
    ),
    "FPS": (
        "- Clutch plays\n"
        "- High-pressure gunfights\n"
        "- Multi-kills and comeback moments\n"
        "- Sharp aim or fast reactions\n"
        "- Funny deaths or fails\n"
        "- Clear team comms and emotional reactions"
    ),
    "Variety": (
        "- Funny banter\n"
        "- Surprising discoveries\n"
        "- Streamer reactions\n"
        "- Chat-worthy fails\n"
        "- Unusual decisions with clear payoff\n"
        "- Emotional or story-driven moments"
    ),
    "Other": (
        "- Strong emotional reactions\n"
        "- Clear tension and payoff\n"
        "- Surprising turns\n"
        "- Funny mistakes\n"
        "- Moments a new viewer can understand quickly\n"
        "- Short story arcs with a natural setup and reaction"
    ),
}


class TranscriptChunker:
    def __init__(self, prompts_dir: Path = PROMPTS_DIR) -> None:
        self.prompts_dir = prompts_dir
        self.prompts_dir.mkdir(parents=True, exist_ok=True)

    def split_and_save(
        self,
        transcription: TranscriptionResult,
        chunk_minutes: int,
        progress_callback: ProgressCallback | None = None,
    ) -> list[TranscriptChunk]:
        if chunk_minutes <= 0:
            raise ValueError("Chunk size must be greater than zero.")

        chunk_seconds = chunk_minutes * 60
        base_name = sanitize_filename(transcription.text_path.stem)
        LOGGER.info("Splitting transcript into %s-minute chunks.", chunk_minutes)

        grouped_segments = self._group_segments(transcription.segments, chunk_seconds)
        if not grouped_segments:
            path = self._unique_path(self.prompts_dir / f"{base_name}_chunk_001.txt")
            self._write_empty_chunk(path, transcription)
            chunk = TranscriptChunk(index=1, start=0.0, end=0.0, segments=[], path=path)
            if progress_callback is not None:
                progress_callback(1.0, f"Saved chunk: {path.name}")
            LOGGER.info("Empty transcript chunk saved to %s", path)
            return [chunk]

        chunks: list[TranscriptChunk] = []
        total_groups = len(grouped_segments)
        for output_index, (group_index, segments) in enumerate(grouped_segments.items(), start=1):
            start = group_index * chunk_seconds
            end = max(start + chunk_seconds, max(segment.end for segment in segments))
            path = self._unique_path(
                self.prompts_dir / f"{base_name}_chunk_{output_index:03d}.txt"
            )
            chunk = TranscriptChunk(
                index=output_index,
                start=start,
                end=end,
                segments=segments,
                path=path,
            )
            self._write_chunk(path, transcription, chunk, chunk_minutes)
            chunks.append(chunk)

            if progress_callback is not None:
                fraction = output_index / total_groups
                progress_callback(fraction, f"Saved chunk {output_index} of {total_groups}")

        LOGGER.info("Saved %s transcript chunks to %s", len(chunks), self.prompts_dir)
        return chunks

    def _group_segments(
        self,
        segments: list[TranscriptSegment],
        chunk_seconds: int,
    ) -> dict[int, list[TranscriptSegment]]:
        groups: dict[int, list[TranscriptSegment]] = {}
        for segment in segments:
            group_index = int(max(segment.start, 0.0) // chunk_seconds)
            groups.setdefault(group_index, []).append(segment)
        return dict(sorted(groups.items()))

    def _write_chunk(
        self,
        path: Path,
        transcription: TranscriptionResult,
        chunk: TranscriptChunk,
        chunk_minutes: int,
    ) -> None:
        with path.open("w", encoding="utf-8") as file:
            file.write("VOD Scout Transcript Chunk\n")
            file.write(f"Source: {transcription.video_path.name}\n")
            file.write(f"Transcript: {transcription.text_path.name}\n")
            file.write(f"Chunk: {chunk.index}\n")
            file.write(f"Chunk Size: {chunk_minutes} minutes\n")
            file.write(
                f"Time Range: {format_timestamp(chunk.start)} --> {format_timestamp(chunk.end)}\n\n"
            )
            for segment in chunk.segments:
                file.write(
                    f"[{format_timestamp(segment.start)} --> "
                    f"{format_timestamp(segment.end)}] {segment.text}\n"
                )

    def _write_empty_chunk(self, path: Path, transcription: TranscriptionResult) -> None:
        with path.open("w", encoding="utf-8") as file:
            file.write("VOD Scout Transcript Chunk\n")
            file.write(f"Source: {transcription.video_path.name}\n")
            file.write(f"Transcript: {transcription.text_path.name}\n")
            file.write("Chunk: 1\n")
            file.write("Time Range: 00:00:00.000 --> 00:00:00.000\n\n")
            file.write("No speech was detected in this media.\n")

    def _unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path

        for index in range(1, 10_000):
            candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
            if not candidate.exists():
                return candidate

        raise RuntimeError(f"Could not create a unique chunk name for {path.name}.")


class ClaudePromptGenerator:
    def __init__(self, prompts_dir: Path = PROMPTS_DIR) -> None:
        self.prompts_dir = prompts_dir
        self.prompts_dir.mkdir(parents=True, exist_ok=True)

    def generate_prompts(self, content_profile: str) -> list[Path]:
        profile = self._clean_profile(content_profile)
        chunk_paths = self._find_transcript_chunks()
        if not chunk_paths:
            raise ValueError(
                "No transcript chunk files were found in prompts/. "
                "Run Download & Transcribe before generating Claude prompts."
            )

        chunk_texts = [
            (chunk_path, self._read_chunk_text(chunk_path))
            for chunk_path in chunk_paths
        ]

        self._remove_existing_prompt_files()
        prompt_paths: list[Path] = []
        for index, (chunk_path, chunk_text) in enumerate(chunk_texts, start=1):
            prompt_text = self._build_prompt(chunk_path.name, chunk_text, profile)
            prompt_path = self.prompts_dir / f"prompt_{index:03d}.txt"
            try:
                prompt_path.write_text(prompt_text, encoding="utf-8")
            except OSError as exc:
                raise RuntimeError(f"Could not save Claude prompt {prompt_path.name}: {exc}") from exc
            prompt_paths.append(prompt_path)
            LOGGER.info("Claude prompt saved: %s", prompt_path)

        LOGGER.info("Generated %s Claude prompt file(s).", len(prompt_paths))
        return prompt_paths

    def _clean_profile(self, content_profile: str) -> str:
        return content_profile if content_profile in VALID_CONTENT_PROFILES else "Horror"

    def _find_transcript_chunks(self) -> list[Path]:
        return sorted(
            path
            for path in self.prompts_dir.glob("*.txt")
            if "_chunk_" in path.stem.lower() and not path.name.lower().startswith("prompt_")
        )

    def _remove_existing_prompt_files(self) -> None:
        for prompt_path in self.prompts_dir.glob("prompt_*.txt"):
            try:
                prompt_path.unlink()
                LOGGER.info("Removed stale Claude prompt: %s", prompt_path.name)
            except OSError as exc:
                raise RuntimeError(f"Could not replace old prompt file {prompt_path.name}: {exc}") from exc

    def _read_chunk_text(self, chunk_path: Path) -> str:
        try:
            text = chunk_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{chunk_path.name} is not a readable UTF-8 transcript chunk.") from exc
        except OSError as exc:
            raise RuntimeError(f"Could not read transcript chunk {chunk_path.name}: {exc}") from exc

        clean_text = text.strip()
        if not clean_text:
            raise ValueError(f"{chunk_path.name} is empty.")
        if "No speech was detected in this media." in clean_text:
            raise ValueError(f"{chunk_path.name} does not contain transcribed speech.")
        return clean_text

    def _build_prompt(self, chunk_name: str, chunk_text: str, profile: str) -> str:
        profile_guidance = PROFILE_PROMPT_GUIDANCE[profile]
        return (
            "You are an expert YouTube Shorts editor.\n\n"
            "Find ONLY moments that maximize viewer retention.\n"
            "Each clip should naturally begin BEFORE the exciting event.\n"
            "Each clip should naturally end AFTER the reaction.\n\n"
            "Preferred clip length:\n"
            "60-90 seconds.\n\n"
            "Never invent highlights.\n"
            "Ignore boring gameplay.\n"
            "Return ONLY valid JSON.\n\n"
            f"Content profile: {profile}\n"
            "Profile priorities:\n"
            f"{profile_guidance}\n\n"
            "JSON format:\n"
            "[\n"
            "{\n"
            "\"title\":\"\",\n"
            "\"start\":\"\",\n"
            "\"end\":\"\",\n"
            "\"reason\":\"\",\n"
            "\"virality\":0,\n"
            "\"confidence\":0\n"
            "}\n"
            "]\n\n"
            f"Transcript chunk file: {chunk_name}\n\n"
            "Transcript chunk:\n"
            f"{chunk_text}\n"
        )


class HighlightRepository:
    def __init__(self, reports_dir: Path = REPORTS_DIR) -> None:
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.json_path = self.reports_dir / "highlights.json"
        self.csv_path = self.reports_dir / "highlights.csv"

    def import_from_file(self, response_path: Path) -> list[Highlight]:
        source = Path(response_path)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Claude response file was not found: {source}")
        if source.suffix.lower() not in {".txt", ".json"}:
            raise ValueError("Choose a Claude response saved as a .txt or .json file.")

        LOGGER.info("Importing Claude response: %s", source)
        try:
            response_text = source.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("The selected Claude response is not readable UTF-8 text.") from exc
        except OSError as exc:
            raise RuntimeError(f"Could not read the selected Claude response: {exc}") from exc

        if not response_text.strip():
            raise ValueError("The selected Claude response file is empty.")

        try:
            raw_highlights = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "The Claude response is not valid JSON. "
                f"Check line {exc.lineno}, column {exc.colno}."
            ) from exc

        highlights = self._parse_highlights(raw_highlights)
        LOGGER.info("Imported %s highlight candidate(s).", len(highlights))
        return highlights

    def save(self, highlights: list[Highlight]) -> tuple[Path, Path]:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        try:
            with self.json_path.open("w", encoding="utf-8") as file:
                json.dump([highlight.to_dict() for highlight in highlights], file, indent=2)
                file.write("\n")
            with self.csv_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=(
                        "title",
                        "start",
                        "end",
                        "reason",
                        "virality",
                        "confidence",
                        "selected",
                        "favorite",
                        "notes",
                    ),
                )
                writer.writeheader()
                for highlight in highlights:
                    writer.writerow(highlight.to_dict())
        except OSError as exc:
            raise RuntimeError(f"Could not save highlight reports: {exc}") from exc

        LOGGER.info("Highlights saved to %s and %s", self.json_path, self.csv_path)
        return self.json_path, self.csv_path

    def _parse_highlights(self, raw_highlights: Any) -> list[Highlight]:
        if not isinstance(raw_highlights, list):
            raise ValueError("Claude response must be a JSON array of highlight objects.")

        highlights: list[Highlight] = []
        for index, raw_highlight in enumerate(raw_highlights, start=1):
            if not isinstance(raw_highlight, dict):
                raise ValueError(f"Highlight {index} must be a JSON object.")

            missing_fields = [
                field
                for field in ("title", "start", "end", "reason", "virality", "confidence")
                if field not in raw_highlight or raw_highlight[field] is None
            ]
            if missing_fields:
                missing_text = ", ".join(missing_fields)
                raise ValueError(f"Highlight {index} is missing required field(s): {missing_text}.")

            title = self._clean_required_text(raw_highlight["title"], "title", index)
            start = self._clean_required_text(raw_highlight["start"], "start", index)
            end = self._clean_required_text(raw_highlight["end"], "end", index)
            reason = self._clean_required_text(raw_highlight["reason"], "reason", index)
            virality = self._clean_score(raw_highlight["virality"], "virality", index)
            confidence = self._clean_score(raw_highlight["confidence"], "confidence", index)
            selected = self._clean_bool(raw_highlight.get("selected", True))
            favorite = self._clean_bool(raw_highlight.get("favorite", False))
            notes = str(raw_highlight.get("notes", "") or "").strip()

            highlights.append(
                Highlight(
                    title=title,
                    start=start,
                    end=end,
                    reason=reason,
                    virality=virality,
                    confidence=confidence,
                    selected=selected,
                    favorite=favorite,
                    notes=notes,
                )
            )

        return highlights

    def _clean_required_text(self, value: Any, field_name: str, highlight_index: int) -> str:
        clean_value = str(value).strip()
        if not clean_value:
            raise ValueError(f"Highlight {highlight_index} has an empty {field_name} field.")
        return clean_value

    def _clean_score(self, value: Any, field_name: str, highlight_index: int) -> float:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Highlight {highlight_index} field {field_name} must be a number."
            ) from exc

    def _clean_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "selected", "favorite"}
        return bool(value)
