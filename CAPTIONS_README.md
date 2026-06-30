# Generic caption normalization tools

This is a generic post-processing step for Whisper.cpp captions.

It is meant for informal publishing, archives, and searchable transcripts. It does not replace human caption review.

## Step 1: Generate transcript with Whisper.cpp

Example:

```bash
whisper-cli \
  -m models/ggml-large-v3.bin \
  -f audio/session.wav \
  -otxt -ovtt \
  -of transcripts/session
```

This usually creates:

```text
transcripts/session.txt
transcripts/session.vtt
```

## Step 2: Normalize captions

```bash
python normalize_captions.py transcripts/session.txt \
  --dictionary dictionaries/un-core.yml \
  --dictionary dictionaries/un-agencies.yml \
  --dictionary dictionaries/un-digital.yml \
  --dictionary dictionaries/geography.yml \
  --out-dir captions
```

## Batch process

```bash
python normalize_captions.py transcripts/*.txt \
  --dictionary dictionaries/un-core.yml \
  --dictionary dictionaries/un-agencies.yml \
  --dictionary dictionaries/un-digital.yml \
  --dictionary dictionaries/geography.yml \
  --out-dir captions
```

## Why use dictionaries?

Whisper often mishears or miscapitalizes:

- UN agencies
- acronyms
- country names
- accented names
- digital governance terms
- open source terms

A dictionary reduces repeated errors without asking an LLM to rewrite the transcript.

## What the script does

- Converts Whisper TXT or rough VTT to valid WebVTT.
- Adds cue numbers.
- Merges very short adjacent captions where safe.
- Splits long captions.
- Wraps captions to roughly two lines.
- Applies conservative dictionary corrections.
- Writes a notes file for review.

## What the script does not do

- It does not re-transcribe audio.
- It does not use event agendas.
- It does not infer speaker names.
- It does not invent missing words.
- It does not do fuzzy matching.

For event-specific corrections, add a separate event dictionary such as:

```text
dictionaries/event-2025-open-source-week.yml
```

Keep generic dictionaries non-event-specific.
