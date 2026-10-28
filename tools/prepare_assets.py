#!/usr/bin/env python3
"""Stage matching VMApple assets outside the public source repository."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ASSETS = ("AVPBooter.vmapple2.bin", "aux.block.img", "ECID.txt")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--disk", type=Path, required=True)
    parser.add_argument("--local-dir", type=Path, default=root.parent / (root.name + "-local"))
    parser.add_argument("--copy-disk", action="store_true", help="Copy the disk locally instead of symlinking")
    args = parser.parse_args()
    dest = args.local_dir.resolve() / "assets"
    if dest.is_relative_to(root):
        raise ValueError("Private assets must be outside the repository")
    disk = args.disk.resolve(strict=True)
    manifest = json.loads((args.source / "assets.json").read_text())
    if manifest.get("platform_version") != 2:
        raise ValueError("Expected a VMApple platform-v2 asset manifest")
    expected_hashes = {name: manifest["assets"][name]["sha256"] for name in ASSETS}
    # Validate the complete input set before staging any files.
    for name, expected in expected_hashes.items():
        if digest(args.source / name) != expected:
            raise ValueError(f"SHA-256 mismatch for {name}")
    dest.mkdir(parents=True, exist_ok=True)
    for name, expected in expected_hashes.items():
        output = dest / name
        if output.exists():
            if digest(output) != expected:
                raise ValueError(f"Existing asset differs; preserving {output}")
        else:
            shutil.copy2(args.source / name, output)
    output = dest / "GoldenGate-27.0-26A428.img"
    if output.exists():
        if output.stat().st_size != disk.stat().st_size:
            raise ValueError(f"Existing disk has a different size; preserving {output}")
        print(f"Preserving existing disk: {output}")
    elif args.copy_disk:
        temporary = output.with_suffix(".copying.img")
        if temporary.exists():
            raise ValueError(f"A prior copy exists: {temporary}")
        subprocess.run(["cp", "--reflink=auto", "--sparse=always", "--", disk, temporary], check=True)
        temporary.rename(output)
    else:
        output.symlink_to(disk)
    print(dest)


if __name__ == "__main__":
    main()
