#!/bin/sh
# Start a separate Chrome that the LinkedIn sender can attach to.
# Log in to LinkedIn by hand in this window. Keep the window open while you send.
# The profile lives in data/chrome-profile, which git ignores.
cd "$(dirname "$0")/.." || exit 1
mkdir -p data
PORT="${PORT:-9333}"
PROFILE="$(pwd)/data/chrome-profile"
if [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
  CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
else
  CHROME="$(command -v google-chrome || command -v chromium || command -v chromium-browser)"
fi
if [ -z "$CHROME" ]; then
  echo "Chrome not found. Install Google Chrome first." >&2
  exit 1
fi
exec "$CHROME" --remote-debugging-port="$PORT" --user-data-dir="$PROFILE" \
  --no-first-run "https://www.linkedin.com/login"
