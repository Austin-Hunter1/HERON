"""The local web page display (D-023): a second thin layer over ``LinkClient``.

Same job as the terminal UI in ``tui/``, different screen. Run it with
``heron-base web`` and open the printed URL in a browser on the same
laptop. See ``server.py`` for the design and the HTTP API.
"""

from heron_base.web.server import WebDashboard

__all__ = ["WebDashboard"]
