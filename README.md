# UN Web TV Downloader

A Python script that fetches download links from [media.un.org](https://media.un.org) / [webtv.un.org](https://webtv.un.org).

## Requirements

- Python 3.8+
- [ffmpeg](https://ffmpeg.org/) — only needed for `--format wav` or `--format mp3`

  If ffmpeg is not installed and you request wav or mp3 output, the script will exit with:
  ```
  Error: ffmpeg is required for wav/mp3 output but was not found.
  Install it with: brew install ffmpeg
  ```

## Usage

```
./un-web-tv-downloader.py [-l LANGUAGE] [-s SIZE] [-f FORMAT] <url>
```

| Flag | Short | Description |
|---|---|---|
| `--lang` | `-l` | Filter by language track (e.g. `English`, `French`, `Spanish`) |
| `--size` | `-s` | Filter by video resolution (e.g. `480p`, `720p`, `1080p`) |
| `--format` | `-f` | Output format: `mp4` (default), `wav`, or `mp3` |

## Examples

### List all available tracks

```console
$ ./un-web-tv-downloader.py https://media.un.org/en/asset/k12/k12mobnmfr
Name: 1978th Meeting, 85th Session, Committee on the Elimination of Discrimination against Women (CEDAW)
Created at: 2023-05-12 20:45:19
Updated at: 2023-05-22 21:46:20
Duration: 2:23:44.704000
Download URL (Original): https://cdnapisec.kaltura.com/...
Download URL (English): https://cdnapisec.kaltura.com/...
Download URL (French): https://cdnapisec.kaltura.com/...
Download URL (Spanish): https://cdnapisec.kaltura.com/...
Download URL (480p): https://cdnapisec.kaltura.com/...
Download URL (720p): https://cdnapisec.kaltura.com/...
Download URL (1080p): https://cdnapisec.kaltura.com/...
```

### Filter to narrow down choices

Specify one flag to see only matching tracks:

```console
$ ./un-web-tv-downloader.py -l English https://media.un.org/en/asset/k12/k12mobnmfr
Download URL (English): https://cdnapisec.kaltura.com/...

$ ./un-web-tv-downloader.py -s 480p https://media.un.org/en/asset/k12/k12mobnmfr
Download URL (480p): https://cdnapisec.kaltura.com/...
```

### Download a specific track

Specify both `--lang` and `--size` to download directly. The file is saved in the current directory with a name derived from the URL:

```console
$ ./un-web-tv-downloader.py -l English -s 480p https://media.un.org/en/asset/k12/k12mobnmfr
Name: 1978th Meeting ...
Duration: 2:23:44.704000
Downloading: k12-k12mobnmfr-English-480p.mp4
Saved: k12-k12mobnmfr-English-480p.mp4
```

### Download for speech-to-text (Whisper)

Use `--format wav` to convert the audio track to a 16 kHz mono PCM WAV — the format expected by Whisper and whisper.cpp. Requires `ffmpeg`.

```console
$ ./un-web-tv-downloader.py -l English -s 480p -f wav https://media.un.org/en/asset/k12/k12mobnmfr
Downloading: k12-k12mobnmfr-English-480p.wav
Converting to wav...
Saved: k12-k12mobnmfr-English-480p.wav
```

> **Note:** whisper.cpp cannot read `.mp4` files directly. Always use `-f wav` (or `-f mp3`) when targeting whisper.cpp.

## LICENSE

[GPLv3](LICENSE)
