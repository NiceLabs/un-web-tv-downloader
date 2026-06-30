#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

RE_ENTRY_ID = re.compile(r"https?://(?:webtv\.|media\.)un\.org/[a-z]{2}/asset/k[a-z\d]+/k([a-z\d]+)")
RE_SLUG = re.compile(r"/asset/(k[a-z\d]+/k[a-z\d]+)")

def get_metadata(entry_id: str):
    partner_id = 2503451
    data = {
        "apiVersion": "3.3.0",
        "format": 1,
        "partnerId": partner_id,
        "1": {"service": "session", "action": "startWidgetSession", "widgetId": "_" + str(partner_id)},
        "2": {"service": "baseEntry", "action": "list", "ks": "{1:result:ks}",
              "filter": {"redirectFromEntryId": entry_id}},
        "3": {"service": "baseEntry", "action": "getPlaybackContext", "ks": "{1:result:ks}",
              "entryId": "{2:result:objects:0:id}",
              "contextDataParams": {"objectType": "KalturaContextDataParams", "flavorTags": "all"}}
    }
    request = Request(
        method="POST",
        url="https://cdnapisec.kaltura.com/api_v3/service/multirequest",
        headers={"content-type": "application/json"},
        data=json.dumps(data).encode()
    )
    with urlopen(request) as response:
        payload = json.load(response)
    meta = payload[1]["objects"][0]
    playback = payload[2]
    urls = {"Original": meta["downloadUrl"]}
    for assets in playback["flavorAssets"]:
        tags = assets["tags"].split(",")
        name = assets["language"] if "audio_only" in tags else "%dp" % assets["height"]
        download_url = meta["downloadUrl"][:-1] + str(assets["flavorParamsId"])
        urls.update({name: download_url})
    return {
        "name": meta["name"],
        "description": meta["description"],
        "created_at": datetime.fromtimestamp(meta["createdAt"]),
        "updated_at": datetime.fromtimestamp(meta["updatedAt"]),
        "duration": timedelta(milliseconds=meta["msDuration"]),
        "urls": urls
    }


def extract_entry_id(url: str):
    matched = RE_ENTRY_ID.search(url)
    if not matched:
        return
    (entry_id,) = matched.groups()
    return entry_id[0] + "_" + entry_id[1:]


def build_filename(media_url: str, label: str, fmt: str = "mp4") -> str:
    """Return a clean filename like k12-k12mobnmfr-English.mp4."""
    matched = RE_SLUG.search(media_url)
    slug = matched.group(1).replace("/", "-") if matched else "download"
    return "%s-%s.%s" % (slug, label, fmt)


def download_file(download_url: str, filename: str):
    print("Downloading:", filename)
    req = Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req) as response, open(filename, "wb") as out:
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            out.write(chunk)
    print("Saved:", filename)


def download_and_convert(download_url: str, filename: str, fmt: str):
    """Download and optionally convert to wav or mp3 via ffmpeg."""
    if fmt == "mp4":
        download_file(download_url, filename)
        return
    if shutil.which("ffmpeg") is None:
        print(
            "Error: ffmpeg is required for wav/mp3 output but was not found.\n"
            "Install it with: brew install ffmpeg",
            file=sys.stderr,
        )
        sys.exit(1)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp4")
    os.close(tmp_fd)
    try:
        download_file(download_url, tmp_path)
        print("Converting to %s..." % fmt)
        if fmt == "wav":
            # 16 kHz mono PCM — the format expected by Whisper / whisper.cpp
            ffmpeg_args = ["-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le"]
        else:
            ffmpeg_args = []
        subprocess.run(
            ["ffmpeg", "-i", tmp_path] + ffmpeg_args + ["-y", filename],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        print("Saved:", filename)
    finally:
        os.unlink(tmp_path)


def main(media_url: str, lang: str = None, size: str = None, fmt: str = "mp4"):
    metadata = get_metadata(extract_entry_id(media_url))
    print("Name:", metadata["name"])
    print("Created at:", metadata["created_at"])
    print("Updated at:", metadata["updated_at"])
    print("Duration:", metadata["duration"])

    urls = metadata["urls"]

    if lang or size:
        filters = set()
        if lang:
            filters.add(lang.lower())
        if size:
            filters.add(size.lower())

        matched = {k: v for k, v in urls.items() if k.lower() in filters}

        if not matched:
            available = ", ".join(urls.keys())
            print(
                "Error: no match for the given filter(s). Available: " + available,
                file=sys.stderr,
            )
            sys.exit(1)

        if lang and size:
            # Both specified: download the language (audio) track, named with both params.
            lang_entry = next(
                ((k, v) for k, v in matched.items() if k.lower() == lang.lower()), None
            )
            if lang_entry is None:
                lang_entry = next(iter(matched.items()))
            name, url = lang_entry
            download_and_convert(url, build_filename(media_url, "%s-%s" % (name, size), fmt), fmt)
        else:
            for name, url in matched.items():
                print("Download URL (%s):" % name, url)
    else:
        for name, url in urls.items():
            print("Download URL (%s):" % name, url)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Fetch download URLs from UN Web TV / media.un.org"
    )
    parser.add_argument("url", help="UN media page URL")
    parser.add_argument(
        "-l", "--lang",
        metavar="LANGUAGE",
        help="Filter by language track (e.g. English, French, Spanish)",
    )
    parser.add_argument(
        "-s", "--size",
        metavar="SIZE",
        help="Filter by video resolution (e.g. 480p, 720p, 1080p)",
    )
    parser.add_argument(
        "-f", "--format",
        metavar="FORMAT",
        default="mp4",
        choices=["mp4", "wav", "mp3"],
        help="Output format: mp4 (default), wav, or mp3; wav/mp3 require ffmpeg",
    )
    args = parser.parse_args()
    main(args.url, lang=args.lang, size=args.size, fmt=args.format)
