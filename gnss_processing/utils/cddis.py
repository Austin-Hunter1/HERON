"""
Downloading from CDDIS, through NASA's Earthdata login.

One function, and it exists because the obvious way does not work.

CDDIS redirects to `urs.earthdata.nasa.gov` to authenticate, and `requests`
deliberately strips credentials when a redirect crosses hosts -- a security
feature, not a bug.  So a one-shot `requests.get(url, auth=...)` follows the
redirect, arrives unauthenticated, and returns **200 OK with a login page**.  The
failure then surfaces much later as `BadGzipFile: Not a gzipped file (b'<!')`,
which points nowhere near the cause.

Both of `gnss_tools`' download paths do the one-shot version --
`misc.data_utils.http_download` passes auth once and lets it be stripped, and
`orbits.sp3_utils.download_and_decompress_sp3_file` calls it with no auth at all.
Neither can fetch from CDDIS today.

The documented pattern is two hops on one session: let the first request end up at
the URS authorize URL, then request *that* URL with the credentials attached.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import requests

from . import environment_variables

CDDIS_ARCHIVE_URL = "https://cddis.nasa.gov/archive"


def credentials() -> tuple[str, str]:
    """Earthdata username and password, or a message saying how to get them."""
    username, password = environment_variables.get_earthdata_credentials()
    if not username or not password:
        raise RuntimeError(
            "Downloading from CDDIS needs Earthdata credentials. Set "
            "EARTHDATA_USERNAME and EARTHDATA_PASSWORD in .env -- registration is "
            "free at https://urs.earthdata.nasa.gov/, and the account must also "
            "accept the CDDIS EULA before downloads succeed."
        )
    return username, password


@dataclass(frozen=True)
class Availability:
    """One archive product, and whether it can be had for a given date."""

    label: str
    url: str
    available: bool
    detail: str
    """A size when available, the reason when not."""

    size_bytes: int | None = None
    """`Content-Length`, when the archive stated one."""

    used: bool = True
    """Whether the module that reported this would actually download it."""

    @property
    def status(self) -> str:
        """`detail`, prefixed so a printed column reads at a glance."""
        return ("yes  " if self.available else "no   ") + self.detail


def _authenticated_get(url: str, *, stream: bool = False) -> requests.Response:
    """
    The two-hop Earthdata fetch, shared by `download` and `exists`.

    See the module docstring for why one hop is not enough.  `stream` leaves the
    body unread so a caller that only wants the status line does not pull a
    hundred megabytes over the wire.
    """
    username, password = credentials()
    session = requests.Session()
    session.auth = (username, password)
    redirected = session.request("get", url, stream=stream)
    return session.get(redirected.url, auth=(username, password), stream=stream)


def exists(url: str) -> tuple[bool, str, int | None]:
    """
    Whether CDDIS publishes a file at `url`, without downloading it.

    Returns `(available, detail, size_bytes)` -- `detail` is a size when the file is
    there and the reason when it is not, so a caller can print either without a
    second branch, and `size_bytes` is that same size unformatted, for a caller
    comparing two of them.

    A 404 here is the normal answer for a product whose latency has not elapsed
    rather than an error, which is why this returns instead of raising.  A
    credential problem is *not* normal, and says so in its own detail string.

    Streamed deliberately.  Only the status line and headers are needed, and an
    SP3 file is tens of megabytes -- probing a handful of products would otherwise
    cost more than the download the caller is trying to decide about.
    """
    try:
        response = _authenticated_get(url, stream=True)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", None
    try:
        if response.status_code == 404:
            return False, "not published for this date", None
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}", None
        # A completed login returns the archive; an incomplete one returns 200 and
        # a login page, which is the failure the module docstring is about.  One
        # byte separates them and is worth reading.
        if next(response.iter_content(1), b"") == b"<":
            return False, "Earthdata login did not complete (HTML, not a file)", None
        length = response.headers.get("Content-Length")
        if not length:
            return True, "available", None
        # The size is worth reporting, not decoration: a daily `brdc` merged before
        # its day is over is a fraction of a finished one, and that is the only
        # cheap warning that the file is present but short.
        size = int(length)
        detail = f"{size / 1e6:.1f} MB" if size >= 1e6 else f"{size / 1e3:.0f} kB"
        return True, detail, size
    finally:
        response.close()


def download(url: str, destination: str | Path, *, overwrite: bool = False) -> Path:
    """
    Fetch one file from CDDIS to `destination`, creating parent directories.

    Cached: an existing file is left alone unless `overwrite`, because a notebook
    re-run should not re-download a hundred megabytes of orbit products.
    """
    destination = Path(destination)
    if destination.exists() and not overwrite:
        return destination

    response = _authenticated_get(url)

    if response.status_code != 200:
        raise RuntimeError(
            f"CDDIS returned {response.status_code} for {url}. A 401 means the "
            "Earthdata credentials in .env are wrong; a 404 means the product is "
            "not published under that name for this date."
        )
    if response.content[:1] == b"<":
        raise RuntimeError(
            f"CDDIS returned an HTML page rather than a file for {url}. That is "
            "an Earthdata login that did not complete -- check the credentials in "
            ".env, and that the account has accepted the CDDIS EULA."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)
    return destination
