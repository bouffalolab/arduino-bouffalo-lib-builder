#!/usr/bin/env bash
# Maintainer-only: freeze the stdlib/tomli tool; users do not need Python or the SDK.
# Host requirements: Python 3.9.17, PyInstaller 6.15.0, tomli 2.2.1.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=$(realpath -m "$1")
python3 - <<'PY_CHECK'
import sys,importlib.metadata as m
assert sys.version_info[:3] == (3,9,17), "freeze with Python 3.9.17"
assert m.version("pyinstaller") == "6.15.0"
assert m.version("tomli") == "2.2.1"
PY_CHECK
python3 -m PyInstaller --noconfirm --clean --onefile --name bl-layout \
  --distpath "$OUT/dist" --workpath "$OUT/build" --specpath "$OUT" \
  "$ROOT/tools/bl_layout.py"

mkdir -p "$OUT/licenses"
python3 - "$OUT/licenses" <<'PY_LICENSE'
from pathlib import Path
import importlib.metadata as m
import shutil,sys
out=Path(sys.argv[1])
for name,filename,dest in [('pyinstaller','COPYING.txt','LICENSE.PyInstaller'),('tomli','LICENSE','LICENSE.tomli')]:
    d=m.distribution(name)
    src=next(d.locate_file(x) for x in d.files if Path(str(x)).name==filename)
    shutil.copy2(src,out/dest)
PY_LICENSE
# The Python license is pinned with the builder source, not fetched during customer builds.
cp "$ROOT/tools/licenses/LICENSE.Python-3.9.17" "$OUT/licenses/"
