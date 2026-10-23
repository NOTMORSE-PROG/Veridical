# IBM Plex Sans PDF fallback

The four unmodified complete TrueType faces are from IBM/plex commit
`71f558992315a12ca1f06a2215d57962a1992162`. Exact upstream URLs, byte lengths and
SHA-256 values are recorded in `IBMPlexSans-source.json`. The accompanying
`IBMPlexSans-LICENSE.txt` contains the upstream copyright and SIL OFL 1.1.
No font conversion or runtime font download is required.

Public Sans remains the primary PDF family. Matching regular, bold, italic and
bold-italic Plex faces are embedded only when the original selected font lacks a
character. Actual font coverage drives selection; this is not universal Unicode
or complex-script support. Greek, tested mathematical symbols and precomposed
accents have been checked in rendered output. Combining sequences require shaping
that this renderer has not verified; they stop export with an explicit error,
as do characters absent from both fonts. The saved report and source text remain
available and unchanged.
