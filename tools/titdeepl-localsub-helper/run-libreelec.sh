#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f .env ]; then
  echo "Missing .env file. Copy .env.example to .env and fill in your values."
  exit 1
fi

docker compose up -d --build
docker compose ps
