#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if ! python3 - <<'PY' >/dev/null 2>&1
import tkinter  # noqa: F401
PY
then
  echo "현재 Python 환경에 tkinter 가 없습니다."
  echo "먼저 Homebrew로 python-tk@3.13 을 설치해 주세요:"
  echo "  brew install python-tk@3.13"
  exit 1
fi

VENV_DIR="$ROOT_DIR/.venv-build"

if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi

if ! "$VENV_DIR/bin/python" - <<'PY' >/dev/null 2>&1
import PyInstaller  # noqa: F401
PY
then
  echo "빌드용 가상환경에 PyInstaller를 설치합니다..."
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install pyinstaller
fi

"$VENV_DIR/bin/python" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "KMA ASOS Downloader" \
  --add-data "settings.example.json:." \
  kma_asos_app.py

echo "빌드 완료: $ROOT_DIR/dist/KMA ASOS Downloader.app"
