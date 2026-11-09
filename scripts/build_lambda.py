"""
Build the Lambda deployment package: build/validator_lambda.zip

What goes in the zip:
    handler.py                    (from lambda/)
    src/dq_platform/*.py          (the shared validation engine)
    config/dq_rules.yaml          (the data contract)
    yaml/                         (PyYAML, Linux build, since Lambda runs on Linux)

pandas, numpy and boto3 are NOT bundled: they come from the AWS SDK for pandas
Lambda layer, which keeps our package tiny.

Usage (from the repo root, with .venv active):
    python scripts/build_lambda.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = REPO_ROOT / "build" / "lambda_package"
ZIP_PATH = REPO_ROOT / "build" / "validator_lambda.zip"

ENGINE_FILES = ["__init__.py", "rules.py", "validator.py", "outputs.py"]


def main() -> None:
    # 1. Start from a clean folder so old files never sneak into the package
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    (BUILD_DIR / "src" / "dq_platform").mkdir(parents=True)
    (BUILD_DIR / "config").mkdir()

    # 2. Copy our code and the rules contract
    shutil.copy(REPO_ROOT / "lambda" / "handler.py", BUILD_DIR / "handler.py")
    for name in ENGINE_FILES:
        shutil.copy(REPO_ROOT / "src" / "dq_platform" / name, BUILD_DIR / "src" / "dq_platform" / name)
    shutil.copy(REPO_ROOT / "config" / "dq_rules.yaml", BUILD_DIR / "config" / "dq_rules.yaml")

    # 3. Install PyYAML built for Lambda's Linux environment (not for Windows)
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install", "pyyaml",
            "--target", str(BUILD_DIR),
            "--platform", "manylinux2014_x86_64",
            "--implementation", "cp",
            "--python-version", "3.12",
            "--only-binary=:all:",
            "--quiet",
        ],
        check=True,
    )

    # 4. Zip it with forward-slash paths (Lambda runs on Linux)
    ZIP_PATH.unlink(missing_ok=True)
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(BUILD_DIR.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, path.relative_to(BUILD_DIR).as_posix())

    size_kb = ZIP_PATH.stat().st_size / 1024
    print(f"Built {ZIP_PATH.relative_to(REPO_ROOT)} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
