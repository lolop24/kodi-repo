#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f .env ]; then
  cp .env.example .env
  sed -i 's/^HELPER_HEADLESS=.*/HELPER_HEADLESS=1/' .env
  sed -i 's/^HELPER_USE_XVFB=.*/HELPER_USE_XVFB=0/' .env
  echo "Created .env from .env.example. Update HELPER_TOKEN before using the helper."
fi

python3 -m ensurepip --upgrade
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium

mkdir -p /storage/.config/system.d
cp titdeepl-localsub-helper.service /storage/.config/system.d/titdeepl-localsub-helper.service
systemctl daemon-reload
systemctl enable --now titdeepl-localsub-helper.service
systemctl status titdeepl-localsub-helper.service --no-pager
