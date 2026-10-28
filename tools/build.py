#!/usr/bin/env python3
"""Build libreims_vgpu from Bazel's patched external sources using installed host toolchains.

The library is upstream reims-vgpu's C API (`cargo xtask dist`) on its Vulkan platform. An
arm64 Golden Gate guest runs under QEMU's TCG on this x86-64 host, so the library is built for
the guest's 16 KiB pages (`RVG_GUEST_PAGE_SHIFT=14`, added by reims-vgpu-goldengate.patch);
upstream otherwise sizes guest pages from the host architecture.
"""
import glob
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def run(args, **kwargs):
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True, **kwargs)


def copy_source(src, dest):
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git", "target"))


def find_lld(scratch):
    """An `lld-link` for the option ROM `cargo xtask dist` always builds (firmware/Makefile).

    The ROM is for the PCI device of an x86-64 guest and unused by vmapple, but the dist step
    builds it unconditionally. lld picks its flavour from its own name, so any lld binary works
    behind a symlink called lld-link.
    """
    candidates = [os.environ.get("LLD", "")]
    candidates += [shutil.which(name) or "" for name in ("lld-link", "ld.lld", "lld")]
    candidates += sorted(glob.glob("/usr/lib/llvm-*/bin/lld"), reverse=True)
    candidates += sorted(glob.glob(str(Path.home() / "Android/Sdk/ndk/*/toolchains/llvm/prebuilt/linux-x86_64/bin/lld")),
                         reverse=True)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            shim = scratch / "bin" / "lld-link"
            shim.parent.mkdir(parents=True, exist_ok=True)
            shim.symlink_to(Path(candidate).resolve())
            return shim
    raise RuntimeError("lld not found: install lld (or set LLD=/path/to/lld)")


def build_remis(source, out, scratch):
    source_copy = scratch / "reims-vgpu"
    copy_source(source, source_copy)
    target = scratch / "target"
    lld = find_lld(scratch)
    env = os.environ.copy()
    prefix_maps = ["--remap-path-prefix=" + str(scratch) + "=/build",
                   "--remap-path-prefix=" + str(Path.home()) + "=/builder"]
    c_prefix_maps = ["-ffile-prefix-map=" + str(scratch) + "=/build",
                     "-ffile-prefix-map=" + str(Path.home()) + "=/builder"]
    for variable in ("CARGO_HOME", "RUSTUP_HOME"):
        if env.get(variable):
            prefix_maps.append("--remap-path-prefix=" + env[variable] + "=/toolchain/" + variable.lower())
            c_prefix_maps.append("-ffile-prefix-map=" + env[variable] + "=/toolchain/" + variable.lower())
    env["RUSTFLAGS"] = (env.get("RUSTFLAGS", "") + " " + " ".join(prefix_maps)).strip()
    for flag_var in ("CFLAGS", "CXXFLAGS"):
        env[flag_var] = (env.get(flag_var, "") + " " + " ".join(c_prefix_maps)).strip()
    env.update(CARGO_TARGET_DIR=str(target), RVG_GUEST_PAGE_SHIFT="14",
               PATH=str(lld.parent) + os.pathsep + env["PATH"],
               MAKEFLAGS="LD=" + str(lld))
    # rust-toolchain.toml in the source selects the pinned toolchain through rustup.
    run(["cargo", "xtask", "dist"], cwd=source_copy, env=env)
    dist = target / "dist-vulkan"
    out.mkdir(parents=True, exist_ok=True)
    (out / "lib/pkgconfig").mkdir(parents=True)
    shutil.copy2(dist / "lib/libreims_vgpu.a", out / "lib")
    # The package file is relative to itself (prefix=${pcfiledir}/../..), so it survives the copy.
    shutil.copy2(dist / "lib/pkgconfig/reims-vgpu.pc", out / "lib/pkgconfig")
    shutil.copytree(dist / "include", out / "include")
    shutil.copytree(dist / "share", out / "share")
    cargo_args = ["--features", "reims-vgpu-c/vulkan"]
    metadata = subprocess.check_output(["cargo", "metadata", "--locked", *cargo_args,
                                        "--filter-platform", "x86_64-unknown-linux-gnu",
                                        "--format-version", "1"], cwd=source_copy, env=env)
    tree = subprocess.check_output(["cargo", "tree", "--locked", "-p", "reims-vgpu-c", *cargo_args,
                                    "--target", "x86_64-unknown-linux-gnu", "--edges", "normal,build",
                                    "--prefix", "none", "--format", "{p}"], cwd=source_copy, env=env)
    used = {tuple(line.split()[:2]) for line in tree.decode().splitlines() if line.strip()}
    (out / "used-rust.txt").write_text("".join(name + " " + version + "\n" for name, version in sorted(used)))
    # Publish only dependency facts; Cargo's raw metadata embeds local paths.
    packages = json.loads(metadata)["packages"]
    public_metadata = {"packages": [
        {key: package[key] for key in ("name", "version", "license", "source")}
        for package in packages if (package["name"], "v" + package["version"]) in used
    ]}
    (out / "cargo-metadata.json").write_text(json.dumps(public_metadata, indent=2) + "\n")
    licenses = out / "licenses"
    licenses.mkdir()
    for package in packages:
        if (package["name"], "v" + package["version"]) not in used:
            continue
        dest = licenses / (package["name"] + "-" + package["version"])
        dest.mkdir(exist_ok=True)
        root = Path(package["manifest_path"]).parent
        for file in root.iterdir():
            if file.is_file() and file.name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "AUTHORS")):
                shutil.copy2(file, dest)
    shutil.copy2(source_copy / "Cargo.lock", out)


def main():
    mode, first, output = sys.argv[1:]
    first, output = map(lambda p: Path(p).resolve(), (first, output))
    # The host toolchains and registry cache are explicit local prerequisites.
    os.environ["PATH"] = str(Path.home() / ".cargo/bin") + os.pathsep + os.environ["PATH"]
    with tempfile.TemporaryDirectory(prefix="qrm-build-") as work:
        if mode != "remis":
            raise ValueError(mode)
        build_remis(first, output, Path(work))


if __name__ == "__main__":
    main()
