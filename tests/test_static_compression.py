"""Compression des statiques en production.

`CompressedManifestStaticFilesStorage` (WhiteNoise) produit des variantes
.gz et, si le module `brotli` est importable, .br au `collectstatic`.
`brotli` n'est pas une dépendance directe (il arrive via
weasyprint[woff] → fonttools[woff]) : si une montée de version le fait
disparaître ou casse son API, les .br disparaissent silencieusement.
"""

import gzip

import brotli
from whitenoise.compress import Compressor


def test_whitenoise_produces_brotli_and_gzip_variants(tmp_path):
    css = tmp_path / "app.css"
    content = b"body { color: #333; margin: 0; }\n" * 200
    css.write_bytes(content)

    outputs = list(Compressor(quiet=True).compress(str(css)))

    assert sorted(outputs) == sorted([f"{css}.br", f"{css}.gz"])
    assert brotli.decompress((tmp_path / "app.css.br").read_bytes()) == content
    assert gzip.decompress((tmp_path / "app.css.gz").read_bytes()) == content
