#!/usr/bin/env python3
"""Check publishable files for local machine paths and patch mail metadata."""
from pathlib import Path
import re
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parent.parent
    listed = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root)
    patterns = {
        "private home directory": re.compile(r"/(?:home|Users)/"),
        "absolute Windows path": re.compile(r"\b[A-Za-z]:[/\\]"),
        "Bazel cache identity": re.compile(r"/_" + r"bazel_[^/\s]+/"),
        "private network address": re.compile(
            r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
            r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"),
    }
    headers = re.compile(r"^(?:From [0-9a-f]{7,}|From:|Author:|Date:|Subject:|Signed-off-by:|Co-authored-by:)")
    errors = []
    for name in sorted(set(listed.decode().split("\0")) - {""}):
        path = root / name
        if path.is_symlink():
            errors.append(f"{name}: publishable symlink requires review")
            continue
        try:
            lines = path.read_text().splitlines()
        except UnicodeDecodeError:
            # PNGs contain only the captured frame, with no host metadata.
            if path.suffix.lower() == ".png":
                from PIL import Image
                with Image.open(path) as capture:
                    if capture.info:
                        errors.append(f"{name}: image metadata requires review")
            else:
                errors.append(f"{name}: unexpected binary file")
            continue
        if any("\0" in line for line in lines):
            errors.append(f"{name}: unexpected binary file")
            continue
        for number, line in enumerate(lines, 1):
            for label, pattern in patterns.items():
                if pattern.search(line):
                    errors.append(f"{name}:{number}: {label}")
            if path.suffix == ".patch" and headers.match(line):
                errors.append(f"{name}:{number}: patch mail metadata")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Publishable tree contains no detected private paths, cache identities, network addresses, or patch mail headers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
