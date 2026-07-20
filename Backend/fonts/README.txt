Latin Modern Roman — the font LaTeX uses (the modern Unicode successor to
Donald Knuth's Computer Modern). This is what makes the "One-Page Tech"
resume template look like a real LaTeX resume rather than a Word document.

Source: the Latin Modern OpenType fonts shipped with TeX Live (GUST).
These .ttf files were converted from the original .otf (PostScript/CFF
outlines) to TrueType outlines, because reportlab cannot embed CFF fonts.

Licence: GUST Font Licence (GFL) — a free, LPPL-style licence that
explicitly permits redistribution and embedding in documents.
See: https://www.gust.org.pl/projects/e-foundry/licenses

They are committed to the repo on purpose: Render's container has no LaTeX
fonts installed, so vendoring them is what makes production match local.
