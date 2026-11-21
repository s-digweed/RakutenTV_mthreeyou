# system imports
import os
import sys
from collections import namedtuple
from typing import Dict, List, Optional

# 3rd parties imports
import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# Channel definition
CHANNEL_FIELDS = [
    "id",
    "numerical_id",
    "title",
    "type",
    "channel_number",
    "category",
    "language_ids",
    "logo",
]

Channel = namedtuple("Channel", CHANNEL_FIELDS)

# Load configuration (.env locally, env vars in GitHub Actions)
load_dotenv()


def log(msg: str) -> None:
    """Log to stderr so stdout stays a clean m3u."""
    print(msg, file=sys.stderr)


class RakutenError(RuntimeError):
    pass


class Api:
    api_base_url = "https://gizmo.rakuten.tv/v3"

    origin = "https://www.rakuten.tv"
    referer = "https://www.rakuten.tv/"
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    classification_id = {
        "al": 270, "at": 300, "ba": 245, "be": 308, "bg": 269, "ch": 319,
        "cz": 272, "de": 307, "dk": 283, "ee": 288, "es": 5, "fi": 284,
        "fr": 23, "gr": 279, "hr": 302, "ie": 41, "is": 287, "it": 36,
        "jp": 309, "lt": 290, "lu": 74, "me": 259, "mk": 275, "nl": 69,
        "no": 286, "pl": 277, "pt": 64, "ro": 268, "rs": 266, "se": 282,
        "sk": 273, "uk": 18,
    }

    # The market code is not always a valid locale ("uk" would be Ukrainian),
    # so map the markets where they differ.
    market_locale = {
        "uk": "en", "ie": "en", "at": "de", "ch": "de", "be": "fr",
        "lu": "fr", "dk": "da", "se": "sv", "cz": "cs", "ee": "et",
        "gr": "el", "al": "sq", "ba": "bs", "me": "sr", "jp": "ja",
    }

    market = os.getenv("CLASSIFICATION", "uk").strip().lower()
    locale = os.getenv("LOCALE", "").strip().lower() or market_locale.get(market, market)

    if market not in classification_id:
        raise RakutenError(
            f"Unknown CLASSIFICATION '{market}'. Valid: {', '.join(sorted(classification_id))}"
        )

    _session: Optional[requests.Session] = None

    @classmethod
    def session(cls) -> requests.Session:
        if cls._session is None:
            s = requests.Session()
            s.headers.update({
                "Origin": cls.origin,
                "Referer": cls.referer,
                "User-Agent": cls.user_agent,
                "Accept": "application/json, text/plain, */*",
            })
            retry = Retry(
                total=3,
                backoff_factor=2,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=("GET", "POST"),
            )
            s.mount("https://", HTTPAdapter(max_retries=retry))

            # Optional explicit proxy (set by the workflow from the RAKUTEN_UK secret).
            # HTTP(S)_PROXY env vars are honoured by requests anyway.
            proxy = os.getenv("RAKUTEN_PROXY", "").strip()
            if proxy:
                s.proxies.update({"http": proxy, "https": proxy})
            cls._session = s
        return cls._session

    @classmethod
    def base_query(cls) -> Dict:
        return {
            "classification_id": cls.classification_id[cls.market],
            "device_identifier": "web",
            "locale": cls.locale,
            "market_code": cls.market,
        }

    @classmethod
    def _call(cls, method: str, path: str, **kwargs) -> Dict:
        response = cls.session().request(
            method, cls.api_base_url + path, timeout=30, **kwargs
        )
        try:
            payload = response.json()
        except ValueError:
            raise RakutenError(
                f"{method} {path} -> HTTP {response.status_code}, non-JSON body: "
                f"{response.text[:300]!r}"
            )
        if not response.ok or "errors" in payload:
            raise RakutenError(
                f"{method} {path} -> HTTP {response.status_code}: "
                f"{payload.get('errors', payload)}"
            )
        return payload

    @classmethod
    def get_live_channels(cls) -> List[Dict]:
        channels: List[Dict] = []
        page = 1
        while True:
            query = {**cls.base_query(), "page": page, "per_page": 100}
            payload = cls._call("GET", "/live_channels", params=query)
            data = payload.get("data", []) or []
            channels.extend(data)
            pagination = (payload.get("meta") or {}).get("pagination") or {}
            total_pages = pagination.get("total_pages") or pagination.get("last_page") or 1
            if not data or page >= int(total_pages):
                break
            page += 1
        return channels

    @classmethod
    def get_live_channel_categories(cls) -> Dict:
        return cls._call("GET", "/live_channel_categories", params=cls.base_query())

    @classmethod
    def get_live_streaming(cls, channel: Channel) -> Dict:
        query = {
            **cls.base_query(),
            "device_stream_audio_quality": "2.0",
            "device_stream_hdr_type": "NONE",
            "device_stream_video_quality": "FHD",
            "disable_dash_legacy_packages": "false",
        }
        data = {
            "audio_language": (channel.language_ids or ["ENG"])[0],
            "audio_quality": "2.0",
            "classification_id": cls.classification_id[cls.market],
            "content_id": channel.id,
            "content_type": "live_channels",
            "device_serial": "not implemented",
            "player": "web:HLS-NONE:NONE",
            "strict_video_quality": False,
            "subtitle_language": "MIS",
            "video_type": "stream",
        }
        return cls._call("POST", "/avod/streamings", params=query, json=data)


# methods
def map_channels_categories(api_response: Dict) -> Dict[str, str]:
    channels_categories_map = {}
    for category in api_response.get("data", []) or []:
        name = category.get("name") or "Other"
        for channel in category.get("live_channels", []) or []:
            # entries can be plain ids or channel objects
            ch_id = channel.get("id") if isinstance(channel, dict) else channel
            if ch_id:
                channels_categories_map.setdefault(ch_id, name)
    return channels_categories_map


def map_channels_streams(channels: List[Channel]) -> Dict[str, str]:
    ch_stream_map = {}
    for channel in channels:
        try:
            payload = Api.get_live_streaming(channel)
        except (RakutenError, requests.RequestException) as e:
            log(f"[skip] {channel.title}: {e}")
            continue

        stream_infos = (payload.get("data") or {}).get("stream_infos") or []
        url = next((s.get("url") for s in stream_infos if s and s.get("url")), None)
        if not url:
            log(f"[skip] {channel.title}: no stream url")
            continue

        # drop the expiring query-string tokens, keep the base playlist
        head, sep, _ = url.partition(".m3u8")
        ch_stream_map[channel.id] = head + sep if sep else url

    return ch_stream_map


def _pick_logo(channel: Dict) -> str:
    images = channel.get("images") or {}
    if not isinstance(images, dict):
        return ""
    for key in ("artwork_negative", "artwork", "logo", "snapshot"):
        val = images.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    for val in images.values():
        if isinstance(val, str) and val.startswith("http"):
            return val
    return ""


def _to_int(value, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_channels() -> List[Channel]:
    live_channels_raw = Api.get_live_channels()
    categories_raw = Api.get_live_channel_categories()
    cc_map = map_channels_categories(categories_raw)

    ch_list: List[Channel] = []
    for channel in live_channels_raw:
        ch_id = channel.get("id")
        if not ch_id:
            continue

        langs = [
            lang.get("id")
            for lang in (channel.get("labels") or {}).get("languages", []) or []
            if lang.get("id")
        ]

        ch_list.append(Channel(
            id=ch_id,
            numerical_id=_to_int(channel.get("numerical_id")),
            title=(channel.get("title") or ch_id).strip(),
            type=channel.get("type", ""),
            channel_number=_to_int(channel.get("channel_number"), 9999),
            category=cc_map.get(ch_id, "Other"),
            language_ids=langs,
            logo=_pick_logo(channel),
        ))

    return ch_list
