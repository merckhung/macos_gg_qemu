#!/usr/bin/env python3
"""Compile QEMU against the independently Bazel-built Reims static library."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def run(args, **kwargs):
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True, **kwargs)


def main():
    _, source, remis, keymaps, output = sys.argv[1:]
    source, remis, keymaps, output = [Path(p).resolve() for p in (source, remis, keymaps, output)]
    os.environ["PATH"] = str(Path.home() / ".cargo/bin") + os.pathsep + os.environ["PATH"]
    # vendor/qemu finds the library through pkg-config (reims-vgpu.pc, relative to itself) and
    # links libreims_vgpu.a statically. Host libraries installed under ~/.local come after it.
    with tempfile.TemporaryDirectory(prefix="qrm-qemu-") as scratch:
        work = Path(scratch)
        remis_stage = work / "remis"
        shutil.copytree(remis / "include", remis_stage / "include")
        shutil.copytree(remis / "lib", remis_stage / "lib")
        pkg_paths = [str(remis_stage / "lib/pkgconfig"),
                     str(Path.home() / ".local/lib/x86_64-linux-gnu/pkgconfig"),
                     str(Path.home() / ".local/lib/pkgconfig")]
        if os.environ.get("PKG_CONFIG_PATH"):
            pkg_paths.append(os.environ["PKG_CONFIG_PATH"])
        os.environ["PKG_CONFIG_PATH"] = os.pathsep.join(pkg_paths)
        src = work / "qemu"
        shutil.copytree(source, src)
        shutil.copytree(keymaps, src / "subprojects/keycodemapdb", dirs_exist_ok=True)
        build = work / "build"
        build.mkdir()
        run([src / "configure", "--target-list=aarch64-softmmu",
             "--extra-cflags=-ffile-prefix-map=" + str(Path.home()) + "=/builder"
             + " -ffile-prefix-map=" + str(work) + "=/build"
             + " -I" + str(Path.home() / ".local/include"),
             "--extra-ldflags=-L" + str(Path.home() / ".local/lib/x86_64-linux-gnu")
             + " -L" + str(Path.home() / ".local/lib"),
             "--disable-werror", "--disable-docs", "--disable-guest-agent", "--disable-tests",
             "--disable-rust", "--enable-tools", "--enable-tcg", "--enable-pvg",
             "--enable-slirp", "--enable-vnc",
             # The guest's display opens in the library's own SDL3 window (reims-vgpu-window),
             # not in a QEMU UI; VNC and QMP screendump read the same console surface.
             "--disable-gtk", "--disable-sdl", "--disable-opengl",
             # virtio-sound-pci plays through the host's PulseAudio (PipeWire's pulse server).
             "--enable-pa", "--audio-drv-list=pa",
             "--disable-capstone", "--disable-libssh", "--disable-curl",
             # The bundled eBPF skeleton embeds an upstream build path.
             # User-mode networking and private raw/QCOW2 disks need neither.
             "--disable-bpf", "--disable-fuse"], cwd=build)
        run(["ninja", "-j", str(min(os.cpu_count() or 1, 24)), "qemu-system-aarch64", "qemu-img"], cwd=build)
        output.mkdir(parents=True, exist_ok=True)
        for name in ["qemu-system-aarch64", "qemu-img"]:
            shutil.copy2(build / name, output)
        for name in ["LICENSE", "COPYING", "COPYING.LIB"]:
            shutil.copy2(src / name, output)
        shutil.copytree(src / "pc-bios/keymaps", output / "share/keymaps")
        shutil.copytree(keymaps, output / "keycodemapdb",
                        ignore=shutil.ignore_patterns("data", "tests", "tools", "meson.build", "BUILD.bazel", "REPO.bazel", "MODULE.bazel"))


if __name__ == "__main__":
    main()
