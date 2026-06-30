#!/usr/bin/env python3
"""
normalize_captions.py

Generic caption cleanup pipeline for Whisper.cpp TXT/VTT output.

Purpose:
- Convert Whisper-style TXT or rough VTT into valid WebVTT.
- Improve caption readability and accessibility.
- Apply conservative dictionary-based terminology corrections.
- Apply explicit reviewable replacements from YAML/JSON dictionaries.
- Produce notes and warnings so edits are auditable.

This is intentionally generic for UN videos. It does not require an event agenda.
Use layered dictionaries for stable terminology only, for example:

  dictionaries/un-core.yml
  dictionaries/un-agencies.yml
  dictionaries/un-digital.yml
  dictionaries/geography.yml
  dictionaries/unosw-terms.yml

Example:

  python normalize_captions.py transcripts/*.txt \
    --dictionary dictionaries/un-core.yml \
    --dictionary dictionaries/un-agencies.yml \
    --dictionary dictionaries/un-digital.yml \
    --dictionary dictionaries/geography.yml \
    --dictionary dictionaries/unosw-terms.yml \
    --out-dir captions

Input supported:
- Whisper.cpp TXT lines like:
  [00:00:00.000 --> 00:00:03.500] Hello.
- WebVTT files beginning with WEBVTT

Dictionary YAML supported:

  acronyms:
    - UNDP
    - UNICEF

  organizations:
    - United Nations
    - Office for Digital and Emerging Technologies

  replacements:
    - from: '\\bOspo\\b'
      to: 'OSPO'
      flags: ignorecase
      label: OSPO casing

JSON dictionaries may use the same structure:

  {
    "acronyms": ["UNDP", "UNICEF"],
    "replacements": [
      {
        "from": "\\bOspo\\b",
        "to": "OSPO",
        "flags": "ignorecase",
        "label": "OSPO casing"
      }
    ]
  }

No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import textwrap
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Cue:
    start_ms: int
    end_ms: int
    text: str


@dataclass
class ReplacementRule:
    pattern: re.Pattern[str]
    replacement: str
    label: str


@dataclass
class CleanupStats:
    replacements: Counter[str] = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)
    validation: list[str] = field(default_factory=list)


def ts_to_ms(ts: str) -> int:
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, rest = parts
    elif len(parts) == 2:
        h = "0"
        m, rest = parts
    else:
        raise ValueError(f"Bad timestamp: {ts!r}")

    if "." in rest:
        s, ms = rest.split(".", 1)
    else:
        s, ms = rest, "0"

    ms = (ms + "000")[:3]
    return (int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms)


def ms_to_ts(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, rem = divmod(rem, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d}.{rem:03d}"


def parse_input(path: Path) -> list[Cue]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if raw.lstrip().startswith("WEBVTT"):
        return parse_vtt(raw)
    return parse_whisper_txt(raw)


def parse_whisper_txt(raw: str) -> list[Cue]:
    cues: list[Cue] = []
    pattern = re.compile(
        r"^\[(\d\d:\d\d:\d\d[\.,]\d{3})\s+-->\s+(\d\d:\d\d:\d\d[\.,]\d{3})\]\s*(.*)$"
    )

    for line in raw.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue

        start, end, text = match.groups()
        try:
            cues.append(Cue(ts_to_ms(start), ts_to_ms(end), text.strip()))
        except ValueError:
            continue

    return cues


def parse_vtt(raw: str) -> list[Cue]:
    cues: list[Cue] = []
    lines = raw.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        if not line or line == "WEBVTT":
            i += 1
            continue

        if line.startswith(("NOTE", "STYLE", "REGION")):
            i += 1
            while i < len(lines) and lines[i].strip():
                i += 1
            continue

        # Optional cue identifier.
        if "-->" not in line and i + 1 < len(lines) and "-->" in lines[i + 1]:
            i += 1
            line = lines[i].strip()

        if "-->" not in line:
            i += 1
            continue

        try:
            start_part, end_part = line.split("-->", 1)
            start = start_part.strip().split()[0]
            end = end_part.strip().split()[0]

            i += 1
            body: list[str] = []

            while i < len(lines) and lines[i].strip():
                body.append(lines[i].strip())
                i += 1

            text = " ".join(body).strip()
            if text:
                cues.append(Cue(ts_to_ms(start), ts_to_ms(end), text))
        except Exception:
            i += 1

    return cues


def normalize_yaml_scalar(value: str) -> str:
    value = value.strip()
    if not value:
        return ""

    # Remove inline comments only when there is whitespace before #.
    value = re.sub(r"\s+#.*$", "", value).strip()

    if (
        (value.startswith('"') and value.endswith('"'))
        or (value.startswith("'") and value.endswith("'"))
    ):
        value = value[1:-1]

    return value.strip()


def parse_simple_yaml(raw: str) -> dict[str, list[Any]]:
    """
    Minimal YAML parser for this script's dictionary format.

    Supported:
      key:
        - value
        - value

      replacements:
        - from: '\\bfoo\\b'
          to: 'bar'
          flags: ignorecase
          label: optional label

    This intentionally does not try to implement full YAML.
    """

    out: dict[str, list[Any]] = {}
    current_key: str | None = None
    current_replacement: dict[str, str] | None = None

    def flush_replacement() -> None:
        nonlocal current_replacement
        if current_key == "replacements" and current_replacement:
            out.setdefault("replacements", []).append(current_replacement)
        current_replacement = None

    for raw_line in raw.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        stripped = raw_line.strip()

        if not raw_line.startswith((" ", "\t")) and stripped.endswith(":"):
            flush_replacement()
            current_key = stripped[:-1].strip()
            out.setdefault(current_key, [])
            continue

        if current_key is None:
            continue

        if stripped.startswith("- "):
            item = stripped[2:].strip()

            if current_key == "replacements":
                flush_replacement()
                current_replacement = {}

                if ":" in item:
                    key, value = item.split(":", 1)
                    current_replacement[key.strip()] = normalize_yaml_scalar(value)
                elif item:
                    # Unsupported replacement shape. Keep it visible but inert.
                    current_replacement["from"] = normalize_yaml_scalar(item)
                continue

            value = normalize_yaml_scalar(item)
            if value:
                out.setdefault(current_key, []).append(value)
            continue

        if current_key == "replacements" and current_replacement is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current_replacement[key.strip()] = normalize_yaml_scalar(value)

    flush_replacement()
    return out


def load_dictionary(path: Path) -> dict[str, list[Any]]:
    """
    Load simple YAML or JSON dictionaries.

    YAML format supported:

      organizations:
        - United Nations
        - UNDP

      replacements:
        - from: '\\bOspo\\b'
          to: 'OSPO'
          flags: ignorecase
          label: OSPO casing

    JSON format supported:

      {
        "organizations": ["United Nations", "UNDP"],
        "replacements": [
          {"from": "\\bOspo\\b", "to": "OSPO", "flags": "ignorecase"}
        ]
      }
    """
    raw = path.read_text(encoding="utf-8", errors="replace")

    if path.suffix.lower() == ".json":
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}

        out: dict[str, list[Any]] = {}
        for key, value in data.items():
            if isinstance(value, list):
                out[str(key)] = value
        return out

    return parse_simple_yaml(raw)


def merge_dictionaries(paths: list[Path]) -> dict[str, list[Any]]:
    merged_terms: dict[str, set[str]] = {}
    replacements: list[dict[str, Any]] = []

    for path in paths:
        data = load_dictionary(path)

        for key, values in data.items():
            if key == "replacements":
                for item in values:
                    if isinstance(item, dict):
                        replacements.append(item)
                continue

            for value in values:
                if isinstance(value, str) and value.strip():
                    merged_terms.setdefault(key, set()).add(value.strip())

    merged: dict[str, list[Any]] = {
        key: sorted(values) for key, values in merged_terms.items()
    }

    if replacements:
        merged["replacements"] = replacements

    return merged


def strip_accents(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )


def compile_flags(value: Any) -> int:
    if not value:
        return 0

    flags = 0
    parts = str(value).lower().replace(",", " ").replace("|", " ").split()

    for part in parts:
        if part in {"i", "ignorecase", "case-insensitive", "case_insensitive"}:
            flags |= re.IGNORECASE
        elif part in {"m", "multiline"}:
            flags |= re.MULTILINE

    return flags


def build_dictionary_replacements(dictionary: dict[str, list[Any]]) -> list[ReplacementRule]:
    """
    Build conservative replacements.

    This corrects:
    - explicit dictionary replacement rules
    - accentless forms to accented dictionary entries
    - acronym punctuation variants, e.g. U.N.D.P. -> UNDP
    - exact lowercase variants for all-caps acronyms

    It does not do fuzzy matching. Fuzzy matching creates false positives.
    """
    rules: list[ReplacementRule] = []

    for item in dictionary.get("replacements", []):
        if not isinstance(item, dict):
            continue

        pattern = item.get("from")
        replacement = item.get("to")

        if not pattern or replacement is None:
            continue

        try:
            compiled = re.compile(str(pattern), compile_flags(item.get("flags")))
        except re.error as exc:
            print(f"Invalid replacement regex {pattern!r}: {exc}", file=sys.stderr)
            continue

        label = str(item.get("label") or replacement)
        rules.append(ReplacementRule(compiled, str(replacement), label))

    for group, values in dictionary.items():
        if group == "replacements":
            continue

        for value in values:
            if not isinstance(value, str):
                continue

            term = value.strip()
            if not term:
                continue

            accentless = strip_accents(term)
            if accentless != term:
                rules.append(
                    ReplacementRule(
                        re.compile(r"\b" + re.escape(accentless) + r"\b"),
                        term,
                        term,
                    )
                )

            # Acronym correction, e.g. U.N.D.P. -> UNDP and undp -> UNDP.
            if re.fullmatch(r"[A-Z0-9]{2,}(?:-[A-Z0-9]+)?", term):
                dotted = r"\.".join(re.escape(ch) for ch in term)
                rules.append(
                    ReplacementRule(
                        re.compile(r"\b" + dotted + r"\.?\b", re.IGNORECASE),
                        term,
                        term,
                    )
                )
                rules.append(
                    ReplacementRule(
                        re.compile(r"\b" + re.escape(term.lower()) + r"\b"),
                        term,
                        term,
                    )
                )

    return rules


DEFAULT_REPLACEMENTS: list[ReplacementRule] = [
    ReplacementRule(re.compile(pattern), replacement, replacement)
    for pattern, replacement in [
        # Generic UN and captioning fixes. Keep this list narrow.
        (r"\bU\.N\.\b", "UN"),
        (r"\bUnited Nations Development Program\b", "United Nations Development Programme"),
        (r"\bUN Development Program\b", "UN Development Programme"),
        (r"\bS D Gs\b", "SDGs"),
        (r"\bA I\b", "AI"),
        (r"\bI C T\b", "ICT"),
        (r"\bO S P O\b", "OSPO"),
        (r"\bD P I\b", "DPI"),
        (r"\bD P G\b", "DPG"),
        (r"\bDPI's\b", "DPIs"),
        (r"\bDPG's\b", "DPGs"),
        (r"\be KYC\b", "eKYC"),
        (r"\bE K Y C\b", "eKYC"),
    ]
]


SUSPICIOUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[a-z][A-Z]"), "Possible missing space or camelCase term"),
    (re.compile(r",\S"), "Comma missing following space"),
    (re.compile(r";\S"), "Semicolon missing following space"),
    (re.compile(r":\S"), "Colon missing following space"),
    (re.compile(r"\b[A-Z]{2,}'s\b"), "Possessive acronym may need review"),
    (re.compile(r"\bOspo\b", re.IGNORECASE), "Possible OSPO casing issue"),
    (re.compile(r"\bunicev\b", re.IGNORECASE), "Possible UNICEF typo"),
]


def apply_rules(text: str, rules: list[ReplacementRule], stats: CleanupStats) -> str:
    for rule in rules:
        text, count = rule.pattern.subn(rule.replacement, text)
        if count:
            stats.replacements[rule.label] += count
    return text


def warn_suspicious_text(text: str, stats: CleanupStats) -> None:
    for pattern, label in SUSPICIOUS_PATTERNS:
        if pattern.search(text):
            stats.warnings.append(f"{label}: {text}")


def clean_text(
    text: str,
    dictionary_replacements: list[ReplacementRule],
    stats: CleanupStats,
    strip_fillers: bool = False,
) -> str:
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()

    if strip_fillers:
        # Conservative. Do not remove words like "well" or "so".
        text = re.sub(r"\b(?:um|uh|erm)\b[, ]*", "", text, flags=re.IGNORECASE)

    text = apply_rules(text, DEFAULT_REPLACEMENTS, stats)
    text = apply_rules(text, dictionary_replacements, stats)

    # Whitespace and punctuation cleanup.
    text = text.replace(" ,", ",").replace(" .", ".").replace(" ?", "?").replace(" !", "!")
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)

    # Missing space after punctuation.
    text = re.sub(r",(?=\S)", ", ", text)
    text = re.sub(r";(?=\S)", "; ", text)
    text = re.sub(r":(?=\S)", ": ", text)
    text = re.sub(r"\?(?=\S)", "? ", text)
    text = re.sub(r"!(?=\S)", "! ", text)

    # Sentence boundary missing space.
    text = re.sub(r"([.!?])([A-Z])", r"\1 \2", text)

    # Normalize apostrophe spacing.
    text = re.sub(r"\s+'\s*", "'", text)

    text = re.sub(r"\s+", " ", text).strip()

    warn_suspicious_text(text, stats)

    return text


def split_long_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    sentence_parts = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []

    for part in sentence_parts:
        if not part:
            continue

        if len(part) <= max_chars:
            chunks.append(part)
            continue

        tokens = re.split(
            r"(\s+(?:and|but|because|so|while|which|that|with|for|from|into|about|through|where|when|who|as)\s+|,\s+|;\s+|:\s+)",
            part,
            flags=re.IGNORECASE,
        )
        buffer = ""

        for token in tokens:
            if not token:
                continue

            candidate = (buffer + token).strip()
            if not buffer or len(candidate) <= max_chars:
                buffer = candidate
            else:
                chunks.append(buffer.strip(" ,;:"))
                buffer = token.strip()

        if buffer:
            chunks.append(buffer.strip(" ,;:"))

    final: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            final.append(chunk)
        else:
            final.extend(
                textwrap.wrap(
                    chunk,
                    width=max_chars,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )

    return [chunk for chunk in final if chunk]


def normalize_cues(
    cues: list[Cue],
    dictionary_replacements: list[ReplacementRule],
    max_caption_chars: int,
    min_duration_ms: int,
    merge_gap_ms: int,
    strip_fillers: bool,
    stats: CleanupStats,
) -> list[Cue]:
    cleaned: list[Cue] = []

    for cue in cues:
        if cue.end_ms <= cue.start_ms:
            continue

        text = clean_text(cue.text, dictionary_replacements, stats, strip_fillers)
        if text:
            cleaned.append(Cue(cue.start_ms, cue.end_ms, text))

    # Merge tiny adjacent cues where safe.
    merged: list[Cue] = []
    for cue in cleaned:
        if (
            merged
            and cue.start_ms - merged[-1].end_ms <= merge_gap_ms
            and (
                merged[-1].end_ms - merged[-1].start_ms < min_duration_ms
                or len(merged[-1].text) < 25
            )
            and len(merged[-1].text) + 1 + len(cue.text) <= max_caption_chars
        ):
            merged[-1].end_ms = cue.end_ms
            merged[-1].text = f"{merged[-1].text} {cue.text}"
        else:
            merged.append(cue)

    # Split long cues and distribute timing proportionally.
    split: list[Cue] = []

    for cue in merged:
        parts = split_long_text(cue.text, max_caption_chars)

        if len(parts) == 1:
            split.append(cue)
            continue

        duration = cue.end_ms - cue.start_ms
        total_chars = sum(max(1, len(part)) for part in parts)
        cursor = cue.start_ms

        for idx, part in enumerate(parts):
            if idx == len(parts) - 1:
                end = cue.end_ms
            else:
                end = cursor + max(min_duration_ms, round(duration * len(part) / total_chars))
                remaining = len(parts) - idx - 1
                end = min(end, cue.end_ms - remaining * 500)

            end = max(cursor + 500, end)
            split.append(Cue(cursor, end, part))
            cursor = end

    # Repair overlaps and preserve monotonic order.
    repaired: list[Cue] = []
    last_end = 0

    for cue in split:
        start = max(cue.start_ms, last_end)
        end = max(cue.end_ms, start + 500)
        repaired.append(Cue(start, end, cue.text))
        last_end = end

    return repaired


def wrap_caption(text: str, line_width: int, max_lines: int = 2) -> str:
    lines = textwrap.wrap(
        text,
        width=line_width,
        break_long_words=False,
        break_on_hyphens=False,
    )

    if len(lines) <= max_lines:
        return "\n".join(lines)

    # Rebalance into two lines. This avoids three-line captions in most players.
    words = text.split()
    best: tuple[int, str, str] | None = None

    for i in range(1, len(words)):
        first = " ".join(words[:i])
        second = " ".join(words[i:])
        overflow = max(0, len(first) - line_width) + max(0, len(second) - line_width)
        score = abs(len(first) - len(second)) + overflow * 3

        if best is None or score < best[0]:
            best = (score, first, second)

    if best:
        return f"{best[1]}\n{best[2]}"

    return text


def validate_cues(
    cues: list[Cue],
    stats: CleanupStats,
    max_caption_chars: int,
    line_width: int,
) -> None:
    last_end = 0

    for idx, cue in enumerate(cues, start=1):
        if cue.start_ms < last_end:
            stats.validation.append(f"Cue {idx}: timestamp overlap")

        if cue.end_ms <= cue.start_ms:
            stats.validation.append(f"Cue {idx}: invalid duration")

        if not cue.text.strip():
            stats.validation.append(f"Cue {idx}: empty text")

        if len(cue.text) > max_caption_chars:
            stats.validation.append(
                f"Cue {idx}: text exceeds {max_caption_chars} characters"
            )

        wrapped = wrap_caption(cue.text, line_width)
        for line in wrapped.splitlines():
            # Allow slight overflow because two-line balancing is sometimes better
            # than creating a third line.
            if len(line) > line_width + 8:
                stats.validation.append(f"Cue {idx}: long wrapped line: {line}")

        last_end = cue.end_ms


def write_vtt(path: Path, cues: list[Cue], source_name: str, line_width: int) -> None:
    lines = [
        "WEBVTT",
        "",
        "NOTE",
        "Generated by normalize_captions.py.",
        "This file has been cleaned for informal caption readability.",
        f"Source: {source_name}",
        "",
    ]

    for idx, cue in enumerate(cues, start=1):
        lines.append(str(idx))
        lines.append(f"{ms_to_ts(cue.start_ms)} --> {ms_to_ts(cue.end_ms)}")
        lines.append(wrap_caption(cue.text, line_width))
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def write_notes(
    path: Path,
    source: Path,
    output: Path,
    input_count: int,
    output_count: int,
    dictionary: dict[str, list[Any]],
    stats: CleanupStats,
    agenda_used: bool = False,
) -> None:
    lines = [
        "Caption cleanup notes",
        "",
        f"Source file: {source.name}",
        f"Output file: {output.name}",
        f"Input cue count: {input_count}",
        f"Output cue count: {output_count}",
        "",
        "What changed:",
        "- Converted Whisper TXT or rough VTT into valid WebVTT.",
        "- Added cue numbers.",
        "- Merged very short adjacent cues where safe.",
        "- Split long cues into shorter cues.",
        "- Wrapped captions for readability.",
        "- Applied conservative dictionary-based corrections.",
        "- Applied explicit replacement rules where provided.",
        "- Added validation and warning notes.",
        "",
        "What was not changed:",
        "- No audio re-transcription was performed.",
        (
            "- Event agenda support was not used."
            if not agenda_used
            else "- Event agenda support was used only for high-confidence metadata."
        ),
        "- No uncertain speaker names were invented.",
        "- No substantive rewriting was performed.",
        "",
        "Dictionary groups loaded:",
    ]

    visible_groups = {
        key: values
        for key, values in dictionary.items()
        if key != "replacements"
    }

    if not visible_groups:
        lines.append("- none")
    else:
        for key, values in sorted(visible_groups.items()):
            lines.append(f"- {key}: {len(values)} entries")

    replacement_rules = dictionary.get("replacements", [])
    lines.append(f"- replacements: {len(replacement_rules)} entries")

    lines.extend(["", "Applied replacements:"])

    if stats.replacements:
        for key, count in stats.replacements.most_common():
            lines.append(f"- {key}: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "Validation:"])

    if stats.validation:
        for item in stats.validation[:100]:
            lines.append(f"- {item}")
        if len(stats.validation) > 100:
            lines.append(f"- ... {len(stats.validation) - 100} more")
    else:
        lines.append("- no validation issues found")

    lines.extend(
        [
            "",
            "Warnings:",
            f"- {len(stats.warnings)} warning(s) written to the warnings file.",
        ]
    )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_warnings(path: Path, stats: CleanupStats) -> None:
    lines = ["Caption cleanup warnings", ""]

    if not stats.warnings:
        lines.append("No warnings found.")
    else:
        for item in stats.warnings[:500]:
            lines.append(f"- {item}")

        if len(stats.warnings) > 500:
            lines.append(f"- ... {len(stats.warnings) - 500} more")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cues_to_markdown(
    path: Path,
    cues: list[Cue],
    source_name: str,
    title: str | None = None,
) -> None:
    title = title or Path(source_name).stem

    paragraphs: list[str] = []
    buffer: list[str] = []
    last_end = 0

    for cue in cues:
        gap = cue.start_ms - last_end
        text = cue.text.strip()

        if not text:
            continue

        # Stage directions.
        if re.fullmatch(r"\[?(applause|laughter|music|inaudible|silence)\]?", text, re.IGNORECASE):
            if buffer:
                paragraphs.append(" ".join(buffer).strip())
                buffer = []
            paragraphs.append(f"> *{text.strip('[]').capitalize()}.*")
            last_end = cue.end_ms
            continue

        # Start a new paragraph after long pauses.
        if buffer and gap > 1800:
            paragraphs.append(" ".join(buffer).strip())
            buffer = []

        buffer.append(text)
        last_end = cue.end_ms

    if buffer:
        paragraphs.append(" ".join(buffer).strip())

    # Light paragraph cleanup.
    cleaned: list[str] = []
    for para in paragraphs:
        para = re.sub(r"\s+", " ", para).strip()
        para = re.sub(r"([.!?])\s+", r"\1 ", para)
        if para:
            cleaned.append(para)

    lines = [
        "---",
        f"title: {json.dumps(title)}",
        f"source: {json.dumps(source_name)}",
        "generated_by: normalize_captions.py",
        "format: simplified transcript",
        "---",
        "",
        f"# {title}",
        "",
        "> This is a simplified Markdown transcript generated from cleaned captions.",
        "> Timestamps were removed. Speaker attribution is not inferred unless added in a later review pass.",
        "",
        "## Transcript",
        "",
    ]

    for para in cleaned:
        lines.append(para)
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalize Whisper.cpp TXT/VTT captions into cleaner WebVTT."
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="Input .txt or .vtt files.")
    parser.add_argument(
        "--dictionary",
        action="append",
        type=Path,
        default=[],
        help="YAML or JSON dictionary. Can be supplied multiple times.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("captions"),
        help="Output directory.",
    )
    parser.add_argument("--max-caption-chars", type=int, default=84)
    parser.add_argument("--line-width", type=int, default=42)
    parser.add_argument("--min-duration-ms", type=int, default=900)
    parser.add_argument("--merge-gap-ms", type=int, default=250)
    parser.add_argument("--strip-fillers", action="store_true")
    parser.add_argument(
        "--suffix",
        default=".accessible.vtt",
        help="Suffix appended to input stem for output VTT.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and normalize, but do not write output files.",
    )

    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    missing = [path for path in args.dictionary if not path.exists()]
    if missing:
        for path in missing:
            print(f"Missing dictionary: {path}", file=sys.stderr)
        return 2

    dictionary = merge_dictionaries(args.dictionary)
    dictionary_replacements = build_dictionary_replacements(dictionary)

    exit_code = 0

    for input_path in args.inputs:
        if not input_path.exists():
            print(f"Missing input: {input_path}", file=sys.stderr)
            exit_code = 1
            continue

        cues = parse_input(input_path)
        if not cues:
            print(f"No cues found: {input_path}", file=sys.stderr)
            exit_code = 1
            continue

        stats = CleanupStats()

        normalized = normalize_cues(
            cues,
            dictionary_replacements=dictionary_replacements,
            max_caption_chars=args.max_caption_chars,
            min_duration_ms=args.min_duration_ms,
            merge_gap_ms=args.merge_gap_ms,
            strip_fillers=args.strip_fillers,
            stats=stats,
        )

        validate_cues(
            normalized,
            stats,
            max_caption_chars=args.max_caption_chars,
            line_width=args.line_width,
        )

        output_path = args.out_dir / f"{input_path.stem}{args.suffix}"
        notes_path = args.out_dir / f"{input_path.stem}.notes.txt"
        warnings_path = args.out_dir / f"{input_path.stem}.warnings.txt"
        markdown_path = args.out_dir / f"{input_path.stem}.transcript.md"

        if args.dry_run:
            print(f"Dry run: {input_path}")
            print(f"Input cues: {len(cues)}")
            print(f"Output cues: {len(normalized)}")
            print(f"Replacement groups: {len(dictionary_replacements)}")
            print(f"Warnings: {len(stats.warnings)}")
            print(f"Validation issues: {len(stats.validation)}")
            continue

        write_vtt(output_path, normalized, input_path.name, args.line_width)
        write_notes(
            notes_path,
            input_path,
            output_path,
            len(cues),
            len(normalized),
            dictionary,
            stats,
        )
        write_warnings(warnings_path, stats)
        cues_to_markdown(markdown_path, normalized, input_path.name)

        print(f"Wrote {output_path}")
        print(f"Wrote {notes_path}")
        print(f"Wrote {warnings_path}")
        print(f"Wrote {markdown_path}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
