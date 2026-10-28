# Constructing Golden Gate guest images

The QEMU launcher consumes an installed ARM64 macOS disk together with the
firmware, AUX/NVRAM block data, and machine identity belonging to that VM. Keep
these files in a private directory outside the checkout. All paths below are
shell variables supplied by the person preparing the image.

## Restore an installed disk from an IPSW

Perform the restore on an Apple Silicon Mac whose Virtualization.framework
supports the selected Golden Gate restore image. Obtain the desired macOS 27
UniversalMac IPSW through [Apple Developer downloads](https://developer.apple.com/download/).
This project's Linux QEMU boot uses an already installed disk; the restore step
uses Apple's installer. [Apple's installation guide](https://developer.apple.com/documentation/virtualization/installing-macos-on-a-virtual-machine)
describes restore-image compatibility, hardware configuration, and installation.

Install or build [macosvm](https://github.com/s-u/macosvm), then set
`MACOS_RESTORE_IPSW` to the downloaded IPSW and `PRIVATE_VM_BUNDLE` to a new
private directory outside the repository. Use the exact guest build associated
with the patches when reproducing a test; compatibility with other builds
requires a separate boot check.

```sh
: "${MACOS_RESTORE_IPSW:?Set this to your private Golden Gate IPSW}"
: "${PRIVATE_VM_BUNDLE:?Choose a private directory outside the checkout}"
mkdir -p "$PRIVATE_VM_BUNDLE"
PRIVATE_VM_BUNDLE="$(cd "$PRIVATE_VM_BUNDLE" && pwd)"

macosvm --disk "$PRIVATE_VM_BUNDLE/disk.img,size=64g" \
  --aux "$PRIVATE_VM_BUNDLE/aux.full.img" \
  --restore "$MACOS_RESTORE_IPSW" \
  --net nat -c 4 -r 8g "$PRIVATE_VM_BUNDLE/macosvm.json"

macosvm --gui "$PRIVATE_VM_BUNDLE/macosvm.json"
```

Finish Setup Assistant, create a local account, and save its credentials
privately. Enable Remote Login for that account if SSH diagnostics are needed.
Shut down from inside the guest and wait for macosvm to exit before copying the
files. The [macosvm documentation](https://github.com/s-u/macosvm#quick-start)
describes this restore-and-GUI workflow and the configuration file it generates.

Preserve `disk.img`, `aux.full.img`, and `macosvm.json` as one set. Restore creates
the disk's installed APFS layout and its corresponding VM identity. A fresh
identity or another VM's AUX does not recreate the original set.

## Assemble QEMU assets from the installed VM files

This step also works when you already have those three files. Obtain
`AVPBooter.vmapple2.bin` from a compatible macOS host's Virtualization.framework
resources, and set `VZ_FIRMWARE` to that private copy. The system resource location
used in [QEMU's VMApple documentation](https://www.qemu.org/docs/master/system/arm/vmapple.html)
is:

```text
/System/Library/Frameworks/Virtualization.framework/Resources/AVPBooter.vmapple2.bin
```

Run the repository's construction helper with Python 3.11 or newer, either on
the Mac or after transferring the matching files to the QEMU host:

```sh
: "${VZ_FIRMWARE:?Set this to your compatible private firmware file}"
VMAPPLE_ASSETS="$PRIVATE_VM_BUNDLE/qemu-assets"
MACOS_DISK="$PRIVATE_VM_BUNDLE/disk.img"

python3 tools/construct_assets.py \
  --config "$PRIVATE_VM_BUNDLE/macosvm.json" \
  --aux "$PRIVATE_VM_BUNDLE/aux.full.img" --aux-layout full \
  --firmware "$VZ_FIRMWARE" --output "$VMAPPLE_ASSETS"
```

The helper builds a new directory containing:

| File | Construction |
| --- | --- |
| `ECID.txt` | Decode the JSON `machineId` from base64, parse its binary plist, and write its decimal `ECID` |
| `aux.block.img` | Skip the full AUX's first 16 KiB of VZ metadata, retain the remaining bytes, then zero-pad its final sector to a 512-byte boundary |
| `AVPBooter.vmapple2.bin` | Copy the supplied platform-v2 firmware |
| `assets.json` | Record the resulting file sizes, SHA-256 checksums, and platform version without source paths |

It checks the JSON hardware model for VMApple platform version 2 and preserves
the input files. An existing output directory is refused. The caller must supply
files from the same VM; checksums detect changed files after construction, while
they cannot prove that independently supplied files originally belonged together.

For an AUX that already has its metadata header removed, pass
`--aux-layout trimmed`. Remove the header exactly once. QEMU's documented AUX
conversion is the basis for this transformation; the sector padding makes the
result usable by the block-device launcher.

The raw disk is the whole installed virtual disk. If your stopped source VM uses
QCOW2, flatten its complete backing chain to a separate raw file first:

```sh
bazel-bin/qemu-dist/qemu-img convert -f qcow2 -O raw \
  "$SOURCE_QCOW2" "$PRIVATE_VM_BUNDLE/disk.img"
```

For an existing raw disk, use it directly or make a sparse copy. Retain the
matching AUX and identity when changing the disk container format.

## Stage and boot the constructed files

Transfer the raw disk and constructed asset directory privately if the restore
and QEMU hosts differ. Set the variables to their new locations after transfer.
From the repository root:

```sh
python3 tools/prepare_assets.py \
  --source "$VMAPPLE_ASSETS" --disk "$MACOS_DISK" --copy-disk
bazel run //:boot
python3 tools/vm.py status
```

`prepare_assets.py` verifies the constructed manifest and stages a local sparse
disk copy beside the repository. Omit `--copy-disk` to use a symlink. Every launch
creates another writable QCOW2 overlay and AUX copy; retain a run directory to
preserve its changes. The IPSW, raw disk, firmware, metadata, and credentials
remain outside Git.

The construction helper is checked with synthetic fixtures and the supplied VM
files. The IPSW installation recipe follows the upstream tools and has not been
executed as part of this project's Linux validation. A newly restored disk still
needs its own live QEMU desktop test; see [current verification](verification.json).
