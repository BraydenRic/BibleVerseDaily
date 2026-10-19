"""Instagram API with Instagram Login: publish Reels and refresh tokens."""
from __future__ import annotations

import os
import time

import requests

GRAPH = "https://graph.instagram.com"
API_VERSION = os.environ.get("IG_API_VERSION", "v26.0")

# Codes Instagram returns when it can't download or read the file at video_url, for example
# 9004 / 2207052 "Media download has failed". Those are worth retrying from a different host.
# Anything else (expired token, rate limit, bad parameter) would fail the same way again.
MEDIA_FETCH_CODES = ("9004", "2207052", "2207003", "2207026", "2207020", "2207008")


class InstagramError(RuntimeError):
    pass


class MediaFetchError(InstagramError):
    """Instagram couldn't fetch or process the video from the URL it was given."""


class Instagram:
    def __init__(self, user_id: str, token: str):
        self.user_id = user_id
        self.token = token

    def _call(self, method: str, path: str, **params) -> dict:
        # A run happens once a day, so ride out brief network blips and 5xx errors
        # rather than failing the whole day on them.
        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                r = requests.request(
                    method, f"{GRAPH}/{API_VERSION}/{path}",
                    headers={"Authorization": f"Bearer {self.token}"},
                    json=params if method == "POST" else None,
                    params=params if method == "GET" else None,
                    timeout=60,
                )
            except (requests.ConnectionError, requests.Timeout) as e:
                problem = f"{type(e).__name__}: {e}"
            else:
                if r.status_code < 500:
                    break
                problem = f"{r.status_code}: {r.text}"
            if attempt == attempts:
                raise InstagramError(f"{method} {path} failed {attempts} times, last: {problem}")
            time.sleep(15 * attempt)
        if r.status_code >= 400:
            error = MediaFetchError if any(code in r.text for code in MEDIA_FETCH_CODES) else InstagramError
            raise error(f"{method} {path} -> {r.status_code}: {r.text}")
        return r.json()

    def publish_reel(self, video_url: str, caption: str, log=print) -> str:
        container = self._call("POST", f"{self.user_id}/media", media_type="REELS",
                               video_url=video_url, caption=caption, share_to_feed=True)["id"]
        log(f"Container {container} created; waiting for Instagram to process the video")
        for _ in range(20):                     # about 10 minutes
            time.sleep(30)
            info = self._call("GET", container, fields="status_code,status")
            log(f"  status: {info.get('status_code')}")
            if info.get("status_code") == "FINISHED":
                break
            if info.get("status_code") in ("ERROR", "EXPIRED"):
                # Processing failures are nearly always about the file or fetching it,
                # so let the caller try the backup host with a fresh container.
                raise MediaFetchError(f"Instagram could not process the video: {info}")
        else:
            raise InstagramError("Timed out waiting for Instagram to process the video")
        return self._call("POST", f"{self.user_id}/media_publish", creation_id=container)["id"]

    def media(self, media_id: str, fields: str = "caption,permalink") -> dict:
        return self._call("GET", media_id, fields=fields)


def refresh_token(token: str) -> tuple[str, int]:
    """Swap a long-lived token (at least 24h old) for a new 60-day one."""
    r = requests.get(f"{GRAPH}/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=60)
    data = r.json()
    if r.status_code >= 400 or "access_token" not in data:
        raise InstagramError(f"Token refresh failed: {data.get('error', data)}")
    return data["access_token"], int(data.get("expires_in", 0))
