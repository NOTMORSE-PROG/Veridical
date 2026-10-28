# Supplemental embedded glyph coverage

These four matching DejaVu Sans faces and the complete `LICENSE_DEJAVU`
are vendored verbatim from the Matplotlib v3.11.1 distribution. Exact upstream
URLs and SHA-256 values are recorded in `DejaVuSans-source.json`.

`families.json` declares ordered coverage: Public Sans first, IBM Plex Sans
next, then DejaVu Sans. Requested bold/italic styles use their matching face.
Width measurement and drawing use the same original-text runs. The extra
family supports source characters such as U+25CF BLACK CIRCLE without
substituting a different bullet or changing saved evidence.

This is bounded cmap coverage, not universal Unicode or shaping support.
The supplemental family admits only configured punctuation/symbol categories
and supported direction classes. Its larger cmap does not authorize newly
covered script text whose shaping or bidirectional layout is unverified.
Unsupported characters and unverified combining sequences still stop export.
Fonts are embedded in the PDF; no runtime download or Matplotlib dependency
is required. Redistribution retains all notices in the supplied license.
