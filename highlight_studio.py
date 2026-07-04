from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Literal

from chunker import Highlight
from downloader import format_timestamp, parse_timestamp


LOGGER = logging.getLogger(__name__)

SortMode = Literal["Virality", "Confidence", "Start Time", "Clip Length"]
LengthFilter = Literal[
    "Any",
    "Under 45 sec",
    "45-59 sec",
    "60-90 sec",
    "91-120 sec",
    "Over 120 sec",
]

SORT_MODES: tuple[SortMode, ...] = ("Virality", "Confidence", "Start Time", "Clip Length")
LENGTH_FILTERS: tuple[LengthFilter, ...] = (
    "Any",
    "Under 45 sec",
    "45-59 sec",
    "60-90 sec",
    "91-120 sec",
    "Over 120 sec",
)


def parse_highlight_time(value: str) -> float | None:
    clean_value = str(value).strip()
    if not clean_value:
        return None

    try:
        return parse_timestamp(clean_value)
    except ValueError:
        match = re.search(r"\d+(?::\d+){0,2}(?:\.\d+)?", clean_value)
        if match is None:
            return None
        try:
            return parse_timestamp(match.group(0))
        except ValueError:
            return None


def highlight_duration_seconds(highlight: Highlight) -> float:
    start = parse_highlight_time(highlight.start)
    end = parse_highlight_time(highlight.end)
    if start is None or end is None or end <= start:
        return 0.0
    return end - start


def display_duration(seconds: float) -> str:
    if seconds <= 0:
        return "Invalid"
    return format_timestamp(seconds)


def has_length_warning(highlight: Highlight) -> bool:
    duration = highlight_duration_seconds(highlight)
    return duration > 0 and (duration < 45 or duration > 120)


def matches_length_filter(highlight: Highlight, length_filter: LengthFilter) -> bool:
    duration = highlight_duration_seconds(highlight)
    if length_filter == "Any":
        return True
    if duration <= 0:
        return False
    if length_filter == "Under 45 sec":
        return duration < 45
    if length_filter == "45-59 sec":
        return 45 <= duration < 60
    if length_filter == "60-90 sec":
        return 60 <= duration <= 90
    if length_filter == "91-120 sec":
        return 90 < duration <= 120
    if length_filter == "Over 120 sec":
        return duration > 120
    return True


def filter_highlights(
    indexed_highlights: Iterable[tuple[int, Highlight]],
    search_text: str,
    min_virality: float,
    min_confidence: float,
    length_filter: LengthFilter,
    selected_only: bool,
) -> list[tuple[int, Highlight]]:
    clean_search = search_text.strip().lower()
    filtered: list[tuple[int, Highlight]] = []

    for index, highlight in indexed_highlights:
        if selected_only and not highlight.selected:
            continue
        if highlight.virality < min_virality or highlight.confidence < min_confidence:
            continue
        if not matches_length_filter(highlight, length_filter):
            continue
        if clean_search and clean_search not in _search_blob(highlight):
            continue
        filtered.append((index, highlight))

    return filtered


def sort_highlights(
    indexed_highlights: Iterable[tuple[int, Highlight]],
    sort_mode: SortMode,
) -> list[tuple[int, Highlight]]:
    reverse = sort_mode != "Start Time"

    def metric(item: tuple[int, Highlight]) -> float:
        _index, highlight = item
        if sort_mode == "Confidence":
            return highlight.confidence
        if sort_mode == "Start Time":
            return parse_highlight_time(highlight.start) or 0.0
        if sort_mode == "Clip Length":
            return highlight_duration_seconds(highlight)
        return highlight.virality

    favorite_items = [item for item in indexed_highlights if item[1].favorite]
    regular_items = [item for item in indexed_highlights if not item[1].favorite]
    return sorted(favorite_items, key=metric, reverse=reverse) + sorted(
        regular_items,
        key=metric,
        reverse=reverse,
    )


def selected_highlights(highlights: Iterable[Highlight]) -> list[Highlight]:
    return [highlight for highlight in highlights if highlight.selected]


def estimate_selected_clips(highlights: Iterable[Highlight]) -> tuple[int, float]:
    selected = selected_highlights(highlights)
    return len(selected), sum(highlight_duration_seconds(highlight) for highlight in selected)


def overlap_ratio(first: Highlight, second: Highlight) -> float:
    first_start = parse_highlight_time(first.start)
    first_end = parse_highlight_time(first.end)
    second_start = parse_highlight_time(second.start)
    second_end = parse_highlight_time(second.end)
    if None in {first_start, first_end, second_start, second_end}:
        return 0.0
    if first_end <= first_start or second_end <= second_start:
        return 0.0

    overlap = max(0.0, min(first_end, second_end) - max(first_start, second_start))
    shorter_duration = min(first_end - first_start, second_end - second_start)
    if shorter_duration <= 0:
        return 0.0
    return overlap / shorter_duration


def duplicate_indices(
    target_index: int,
    highlights: list[Highlight],
    ignored_pairs: set[frozenset[int]] | None = None,
) -> list[int]:
    if target_index < 0 or target_index >= len(highlights):
        return []

    ignored_pairs = ignored_pairs or set()
    target = highlights[target_index]
    duplicates: list[int] = []
    for index, highlight in enumerate(highlights):
        if index == target_index:
            continue
        if frozenset({target_index, index}) in ignored_pairs:
            continue
        if overlap_ratio(target, highlight) > 0.5:
            duplicates.append(index)
    return duplicates


def merge_highlights(primary: Highlight, duplicate: Highlight) -> Highlight:
    primary_start = parse_highlight_time(primary.start)
    primary_end = parse_highlight_time(primary.end)
    duplicate_start = parse_highlight_time(duplicate.start)
    duplicate_end = parse_highlight_time(duplicate.end)

    merged_start = primary.start
    if primary_start is not None and duplicate_start is not None:
        merged_start = format_timestamp(min(primary_start, duplicate_start))

    merged_end = primary.end
    if primary_end is not None and duplicate_end is not None:
        merged_end = format_timestamp(max(primary_end, duplicate_end))

    reason = _join_unique_text(primary.reason, duplicate.reason)
    notes = _join_unique_text(primary.notes, duplicate.notes)
    title = primary.title if primary.virality >= duplicate.virality else duplicate.title

    return replace(
        primary,
        title=title,
        start=merged_start,
        end=merged_end,
        reason=reason,
        virality=max(primary.virality, duplicate.virality),
        confidence=max(primary.confidence, duplicate.confidence),
        selected=primary.selected or duplicate.selected,
        favorite=primary.favorite or duplicate.favorite,
        notes=notes,
    )


def export_json(highlights: list[Highlight], path: Path) -> Path:
    _ensure_exportable(highlights)
    try:
        with path.open("w", encoding="utf-8") as file:
            json.dump([highlight.to_dict() for highlight in highlights], file, indent=2)
            file.write("\n")
    except OSError as exc:
        raise RuntimeError(f"Could not export JSON: {exc}") from exc
    LOGGER.info("Exported JSON report: %s", path)
    return path


def export_csv(highlights: list[Highlight], path: Path) -> Path:
    _ensure_exportable(highlights)
    try:
        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=(
                    "title",
                    "start",
                    "end",
                    "length",
                    "virality",
                    "confidence",
                    "reason",
                    "selected",
                    "favorite",
                    "notes",
                ),
            )
            writer.writeheader()
            for highlight in highlights:
                row = highlight.to_dict()
                row["length"] = display_duration(highlight_duration_seconds(highlight))
                writer.writerow(row)
    except OSError as exc:
        raise RuntimeError(f"Could not export CSV: {exc}") from exc
    LOGGER.info("Exported CSV report: %s", path)
    return path


def export_markdown(highlights: list[Highlight], path: Path) -> Path:
    _ensure_exportable(highlights)
    try:
        with path.open("w", encoding="utf-8") as file:
            for index, highlight in enumerate(highlights, start=1):
                file.write("# Highlight\n\n")
                file.write(f"Title: {highlight.title}\n\n")
                file.write(f"Timestamp: {highlight.start} - {highlight.end}\n\n")
                file.write(f"Reason: {highlight.reason}\n\n")
                file.write(f"Notes: {highlight.notes}\n")
                if index < len(highlights):
                    file.write("\n---\n\n")
    except OSError as exc:
        raise RuntimeError(f"Could not export Markdown: {exc}") from exc
    LOGGER.info("Exported Markdown report: %s", path)
    return path


def _ensure_exportable(highlights: list[Highlight]) -> None:
    if not highlights:
        raise ValueError("There are no highlights to export.")


def _search_blob(highlight: Highlight) -> str:
    duration = display_duration(highlight_duration_seconds(highlight))
    return " ".join(
        (
            highlight.title,
            highlight.reason,
            highlight.start,
            highlight.end,
            duration,
            highlight.notes,
        )
    ).lower()


def _join_unique_text(first: str, second: str) -> str:
    first_clean = first.strip()
    second_clean = second.strip()
    if not first_clean:
        return second_clean
    if not second_clean or second_clean == first_clean:
        return first_clean
    return f"{first_clean}\n{second_clean}"
