"""Redirect to an external site after a form POST, without breaking the CSP.

The CSP sets `form-action 'self'`, and browsers apply it to every redirect that
follows a form submission: a POST answered by a 302 to Stripe or SuperPDP is
blocked. `ExternalRedirect` answers with a page that navigates by itself
(meta refresh + fallback link), which `form-action` does not govern.
"""

from urllib.parse import urlsplit

from django.http import HttpResponse
from django.template.loader import render_to_string


class ExternalRedirect(HttpResponse):
    """200 page that sends the browser to an external http(s) URL.

    Exposes `url` like `HttpResponseRedirect`.
    """

    def __init__(self, url, *args, **kwargs):
        if urlsplit(url).scheme not in ("http", "https"):
            raise ValueError(f"Unsafe redirect URL scheme: {url!r}")
        super().__init__(
            render_to_string("external_redirect.html", {"url": url}), *args, **kwargs
        )
        self.url = url
        # The URL may carry a one-time OAuth state: never cache this page.
        self["Cache-Control"] = "no-store"
        self["Referrer-Policy"] = "no-referrer"
