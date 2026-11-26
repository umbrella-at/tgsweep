#!/bin/sh
# Puts site/ on forester as a new release of tgsweep.atworks.dev (docs/infrastructure.md).
# Each release is a folder of its own; the `current` link switches to it in one step,
# the three newest stay for a rollback. The Caddy site file is not touched here.
set -eu

HOST=${TGSWEEP_SITE_HOST:-forester}
DIR=/var/www/tgsweep
REL=$(date +%Y%m%d-%H%M%S)

cd "$(dirname "$0")/.."
test -f site/index.html || { echo "site/index.html not found" >&2; exit 1; }

# unchanged files are hard links to the previous release, so a deploy sends only what changed
ssh "$HOST" "mkdir -p $DIR/releases"
# (the rsync of macOS has no --chmod: the server makes the files readable for caddy itself)
rsync -rlptz --link-dest="$DIR/current/" site/ "$HOST:$DIR/releases/$REL/"
ssh "$HOST" "set -e
  test -f $DIR/releases/$REL/index.html
  chmod -R a+rX,go-w $DIR/releases/$REL
  ln -sfn releases/$REL $DIR/current.new
  mv -Tf $DIR/current.new $DIR/current
  cd $DIR/releases && ls -1d */ | sort | head -n -3 | xargs -r rm -rf --"
echo "deployed $REL to $HOST:$DIR/current"
