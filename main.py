#!/usr/bin/env python3

# system imports
import sys
from typing import List

# local imports
import rakuten


def _clean(value: str) -> str:
    # quotes and commas break #EXTINF attribute parsing in most players
    return str(value).replace('"', "'").replace("\n", " ").strip()


def generate_list(channels: List[rakuten.Channel]) -> str:
    ch_streams = rakuten.map_channels_streams(channels)

    lines = ["#EXTM3U"]
    for ch in sorted(channels, key=lambda x: (x.channel_number, x.title)):
        url = ch_streams.get(ch.id)
        if not url:
            continue  # never write an #EXTINF without a URL, it breaks the playlist

        attrs = [
            f'tvg-chno="{ch.channel_number}"' if ch.channel_number != 9999 else "",
            f'tvg-id="{_clean(ch.id)}"',
            f'tvg-name="{_clean(ch.title)}"',
            f'tvg-logo="{ch.logo}"' if ch.logo else "",
            f'group-title="{_clean(ch.category)}"',
        ]
        lines.append("#EXTINF:-1 " + " ".join(a for a in attrs if a) + f",{_clean(ch.title)}")
        lines.append(url)

    return "\n".join(lines) + "\n"


def main() -> int:
    try:
        channels = rakuten.get_channels()
    except (rakuten.RakutenError, Exception) as e:
        rakuten.log(f"ERROR fetching channels (geo-block / VPN not UK?): {e}")
        return 1

    rakuten.log(f"Found {len(channels)} channels for market '{rakuten.Api.market}'")
    m3u_list = generate_list(channels)
    count = m3u_list.count("#EXTINF")
    rakuten.log(f"Wrote {count} channels with streams")

    if count == 0:
        rakuten.log("ERROR: no playable channels, refusing to output an empty list")
        return 1

    sys.stdout.write(m3u_list)
    return 0


if __name__ == "__main__":
    sys.exit(main())
