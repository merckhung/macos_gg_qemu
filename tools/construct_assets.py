#!/usr/bin/env python3
"""Construct a private QEMU asset bundle from one installed macosvm VM."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import tempfile

AUX_METADATA_BYTES = 0x4000
BLOCK_BYTES = 512


def construct(config, aux, firmware, output, aux_layout):
    root = Path(__file__).resolve().parent.parent
    output = output.resolve()
    if aux_layout not in {"full", "trimmed"}:
        raise ValueError("Unknown AUX layout")
    if output.is_relative_to(root):
        raise ValueError("Guest files must be constructed outside the repository")
    if output.exists():
        raise ValueError("Output already exists; choose a new private directory")
    metadata = json.loads(config.read_text())
    model = plistlib.loads(base64.b64decode(metadata["hardwareModel"], validate=True))
    identity = plistlib.loads(base64.b64decode(metadata["machineId"], validate=True))
    if model.get("PlatformVersion") != 2:
        raise ValueError("The launcher requires a VMApple platform-v2 image")
    ecid = identity.get("ECID")
    if type(ecid) is not int or not 0 < ecid < (1 << 64):
        raise ValueError("Machine metadata does not contain a valid ECID")
    skip = AUX_METADATA_BYTES if aux_layout == "full" else 0
    if aux.stat().st_size <= skip:
        raise ValueError("AUX contains no block data after its metadata header")
    if not firmware.is_file() or firmware.stat().st_size == 0:
        raise ValueError("Firmware input is empty or missing")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="guest-assets-", dir=output.parent) as scratch:
        stage = Path(scratch) / "assets"
        stage.mkdir()
        with aux.open("rb") as source, (stage / "aux.block.img").open("wb") as target:
            source.seek(skip)
            shutil.copyfileobj(source, target)
            target.write(bytes((-target.tell()) % BLOCK_BYTES))
        shutil.copyfile(firmware, stage / "AVPBooter.vmapple2.bin")
        (stage / "ECID.txt").write_text(str(ecid) + "\n")
        assets = {}
        for path in sorted(stage.iterdir()):
            with path.open("rb") as stream:
                sha = hashlib.file_digest(stream, "sha256").hexdigest()
            assets[path.name] = {"size": path.stat().st_size, "sha256": sha}
        (stage / "assets.json").write_text(json.dumps({
            "platform_version": 2, "assets": assets,
        }, indent=2) + "\n")
        stage.rename(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Original macosvm JSON from this VM")
    parser.add_argument("--aux", type=Path, required=True)
    parser.add_argument("--aux-layout", choices=["full", "trimmed"], required=True,
                        help="Full VZ AUX includes the 16 KiB metadata header; trimmed AUX does not")
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    construct(args.config, args.aux, args.firmware, args.output, args.aux_layout)
    print("Private QEMU firmware, AUX, ECID, and checksum manifest constructed")


if __name__ == "__main__":
    main()
