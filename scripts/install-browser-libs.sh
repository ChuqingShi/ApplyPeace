#!/usr/bin/env bash
# Install the shared libraries headless Chromium needs, without root.
#
# Downloads Debian packages with apt-get into a temp dir, extracts their .so
# files into $APPLYPEACE_RUNTIME/lib, and repeats until `ldd` reports nothing
# missing. ApplyPeace adds that directory to LD_LIBRARY_PATH when it launches
# the browser.
set -euo pipefail

RUNTIME="${APPLYPEACE_RUNTIME:-/data/workspace/.applypeace-runtime}"
LIBDIR="$RUNTIME/lib"
APT_TMP="$(mktemp -d)"
trap 'rm -rf "$APT_TMP"' EXIT
mkdir -p "$LIBDIR" "$APT_TMP"/{lists/partial,cache/archives/partial,debs}

APT_OPTS=(-o "Dir::State::Lists=$APT_TMP/lists" -o "Dir::Cache=$APT_TMP/cache" -o Debug::NoLocking=1)
apt-get "${APT_OPTS[@]}" update -qq

BROWSER="$(find "${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}" -name chrome-headless-shell -type f | head -1)"
[ -n "$BROWSER" ] || { echo "Headless Chromium not found; run: uv run playwright install chromium-headless-shell" >&2; exit 1; }

# Debian bookworm packages that provide Chromium's runtime libraries (and their deps).
PKGS=(libglib2.0-0 libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 libdbus-1-3
      libx11-6 libx11-xcb1 libxcb1 libxau6 libxdmcp6 libbsd0 libmd0 libxcomposite1 libxdamage1
      libxext6 libxfixes3 libxrandr2 libxrender1 libxi6 libgbm1 libdrm2 libwayland-server0
      libxkbcommon0 libasound2 libexpat1 libxshmfence1 libpcre2-8-0 libffi8 libsystemd0
      libcap2 libgcrypt20 libgpg-error0 liblz4-1 liblzma5 libzstd1 libmount1 libblkid1 libselinux1
      libfontconfig1 libfreetype6 libpng16-16 libbrotli1 fonts-liberation fonts-dejavu-core)
(cd "$APT_TMP/debs" && apt-get "${APT_OPTS[@]}" download "${PKGS[@]}" -qq)
for deb in "$APT_TMP"/debs/*.deb; do
  dpkg-deb -x "$deb" "$APT_TMP/extract"
done
find "$APT_TMP/extract" -name '*.so*' \( -type f -o -type l \) -exec cp -P {} "$LIBDIR"/ \;

# Fonts plus a minimal fontconfig file; without fonts pages render no text.
mkdir -p "$RUNTIME/fonts" "$RUNTIME/fontconfig-cache"
find "$APT_TMP/extract/usr/share/fonts" -name '*.ttf' -exec cp {} "$RUNTIME/fonts"/ \;
cat > "$RUNTIME/fonts.conf" <<CONF
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>$RUNTIME/fonts</dir>
  <cachedir>$RUNTIME/fontconfig-cache</cachedir>
</fontconfig>
CONF

if ! LD_LIBRARY_PATH="$LIBDIR" ldd "$BROWSER" | grep -q "not found"; then
  echo "All browser libraries present in $LIBDIR"
  exit 0
fi
echo "Still missing; add the providing package to PKGS:" >&2
LD_LIBRARY_PATH="$LIBDIR" ldd "$BROWSER" | grep "not found" >&2
exit 1
