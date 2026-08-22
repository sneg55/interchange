#!/usr/bin/env bash
# Re-download the two typefaces the console is set in. Run when they need updating.
#
#   console/scripts/vendor-fonts.sh
#
# The console used `next/font/google`, which fetches from fonts.gstatic.com at
# BUILD time. That works on a laptop and failed in Cloud Build, repeatedly and
# only there, with `Failed to fetch 'Source Serif 4' from Google Fonts` after
# three retries per file. The same Dockerfile built locally in under two minutes.
#
# A build that reaches the network is a build that can fail for reasons that have
# nothing to do with the code, on someone else's schedule. These are vendored so
# it cannot: the files are in the repository, `next/font/local` reads them off
# disk, and the deploy has one less thing that can be down.
#
# Both are variable fonts, so each style is one file covering the whole weight
# range rather than one file per weight. Latin subset only, which is what the
# previous `subsets: ['latin']` asked for.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$(dirname "$HERE")/src/app/fonts"
# A browser user agent, deliberately. The CSS API serves woff2 to browsers it
# recognises and older formats to everything else, so a default curl agent gets
# ttf and the files are several times larger for no benefit.
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

mkdir -p "$OUT"

# One CSS request per file, so a filename can never be assigned by POSITION in
# a combined response. The combined query this script used to make listed the
# serif's italic @font-face before its upright one, and the positional mapping
# shipped the italic cut as `source-serif-4-latin.woff2`: every serif paragraph
# in the product rendered italic, and no build step could notice.
fetch_latin() { # $1 css2 url, $2 output filename
  local url
  # The latin block is the one whose unicode-range starts at U+0000.
  url=$(curl -sS -A "$UA" "$1" \
    | grep -B3 'unicode-range: U+0000' \
    | grep -oE 'https://[^)]+woff2' \
    | head -1)
  if [[ -z "$url" ]]; then
    echo "no latin woff2 in CSS for $2" >&2
    exit 1
  fi
  curl -sS -A "$UA" -o "$OUT/$2" "$url"
  printf '%-36s %s\n' "$2" "$url"
}

# Range syntax (`400..700`), not a weight list: a list makes the CSS API serve
# one static file per weight while a range serves the one variable file
# `next/font/local` declares with a weight range.
fetch_latin "https://fonts.googleapis.com/css2?family=Libre+Franklin:wght@400..700&display=swap" \
  libre-franklin-latin.woff2
fetch_latin "https://fonts.googleapis.com/css2?family=Source+Serif+4:wght@400..600&display=swap" \
  source-serif-4-latin.woff2
fetch_latin "https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,wght@1,400..600&display=swap" \
  source-serif-4-latin-italic.woff2

echo
ls -lh "$OUT"
