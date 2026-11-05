#!/usr/bin/env bash
# Prepare the images that will be published (CI only — originals stay in git).
#  1. Cap large JPEG/PNG uploads at 2000px on the long side.
#  2. Strip GPS location metadata from every image, then verify none is left.
# Any failure stops the build: publishing a photo with its location is not an acceptable fallback.
set -euo pipefail

dir="${1:-static/images}"

find "$dir" -type f \( -iname '*.jpg' -o -iname '*.jpeg' \) -size +500k -exec mogrify -resize '2000x2000>' -quality 85 {} +
find "$dir" -type f -iname '*.png' -size +500k -exec mogrify -resize '2000x2000>' {} +

exiftool -q -r -overwrite_original -gps:all= -ext jpg -ext jpeg -ext png -ext webp "$dir"

# exiftool exits 2 when no file matches the -if condition, which is the outcome we want.
set +e
left=$(exiftool -q -r -if '$gps:all' -p '$Directory/$FileName' -ext jpg -ext jpeg -ext png -ext webp "$dir")
status=$?
set -e
if [ -n "$left" ] || [ "$status" -eq 1 ]; then
  echo "GPS metadata still present (or exiftool failed) in:" >&2
  echo "$left" >&2
  exit 1
fi
echo "Images prepared: no GPS metadata left in $dir"
