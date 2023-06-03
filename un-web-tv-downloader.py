#!/usr/bin/env python3
import json
import re
import sys
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

RE_ENTRY_ID = re.compile(re.escape("/").join([
    re.escape("https://media.un.org"),
    r"[a-z]{2}",
    "asset",
    r"k[a-z\d]+",
    r"k([a-z\d]+)"
]))


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


def main(media_url: str):
    metadata = get_metadata(extract_entry_id(media_url))
    print("Name:", metadata["name"])
    print("Created at:", metadata["created_at"])
    print("Updated at:", metadata["updated_at"])
    print("Duration:", metadata["duration"])
    for name, url in metadata["urls"].items():
        print("Download URL (%s):" % name, url)


if __name__ == '__main__':
    if len(sys.argv) == 1:
        print("please set parameters")
    else:
        main(sys.argv[1])
