#!/usr/bin/env python3
"""Export only the standalone repository sources, without credentials or outputs."""
import argparse
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("README.md", "VERSION", "Dockerfile", ".gitignore", ".dockerignore", ".cnb.yml", "scripts", "tools", "examples", "tests")


def package(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for source in SOURCES:
            path = ROOT / source
            entries = sorted(path.rglob("*")) if path.is_dir() else [path]
            for entry in entries:
                if not entry.is_file() or entry.is_symlink() or "__pycache__" in entry.parts or entry.suffix == ".pyc" or entry.name == ".env":
                    continue
                archive.add(entry, arcname="kernel-builder/" + str(entry.relative_to(ROOT)), recursive=False)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "dist/kernel-builder.tar.gz"))
    args = parser.parse_args()
    print(package(args.output))
