# Artwork in this directory is NOT Apache licensed

Files installed here by `scripts/fetch-assets` are the work of **Tom Cartos**
and are provided under **Tom's Open Map License**.

> ### 📜 [Tom's Open Map License — read it in full](https://www.tomcartos.com/toms-open-map-license)

- Website: <https://www.tomcartos.com/>
- Attribution: *Cartography and map assets by Tom Cartos — https://www.tomcartos.com/*

EzVTT's own source code is licensed under the Apache License 2.0. **That licence
does not extend to this artwork, and EzVTT neither relicenses nor sublicenses
it.** You receive these assets under Tom's licence, directly from Tom Cartos.

If you redistribute EzVTT or build on it, read Tom's licence and comply with it
yourself. It is the authoritative statement of its own terms — do not rely on
any summary, including this file.

## For contributors

One term is reflected directly in EzVTT's implementation and must be preserved:
**the licence does not permit editing these assets, so nothing in this directory
is ever written to, cropped, or re-encoded.** Grid-footprint sizing is stored as
metadata and applied as a display-time transform over the unmodified original;
thumbnails are generated into `data/thumbs/`, never in place.

Any code path that writes into `assets/bundled/` is a bug. See ADR-005 in
`docs/DECISIONS.md`.

Please consider supporting Tom's work at <https://www.tomcartos.com/>.
