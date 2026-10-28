#!/usr/bin/env python3
"""Collect build-selected Rust licenses and Linux runtime package notices."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust-dist", type=Path, default=root / "bazel-bin/remis_vgpu-dist")
    parser.add_argument("--qemu", type=Path, default=root / "bazel-bin/qemu-dist/qemu-system-aarch64")
    parser.add_argument("--vulkan-icd", type=Path,
                        default=Path("/usr/share/vulkan/icd.d/lvp_icd.json"))
    args = parser.parse_args()
    metadata = json.loads((args.rust_dist / "cargo-metadata.json").read_text())
    used = {tuple(line.split()[:2]) for line in (args.rust_dist / "used-rust.txt").read_text().splitlines()}
    inventory = []
    dest = root / "licenses/rust"
    dest.mkdir(parents=True, exist_ok=True)
    # Bazel artifacts are read-only; keep the collected repository copies editable.
    for path in [dest, *dest.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    keep_rust = set()
    for package in sorted(metadata["packages"], key=lambda p: (p["name"], p["version"])):
        if (package["name"], "v" + package["version"]) not in used:
            continue
        name = package["name"] + "-" + package["version"]
        keep_rust.add(name)
        source = args.rust_dist / "licenses" / name
        if not source.is_dir():
            raise RuntimeError(f"Missing collected licenses for {name}")
        shutil.copytree(source, dest / name, dirs_exist_ok=True)
        for path in [dest / name, *(dest / name).rglob("*")]:
            path.chmod(0o755 if path.is_dir() else 0o644)
        inventory.append({"name": package["name"], "version": package["version"],
                          "license": package["license"], "source": package["source"],
                          "files": sorted(p.name for p in source.iterdir())})
    for existing in list(dest.iterdir()):
        if existing.is_dir() and existing.name not in keep_rust:
            shutil.rmtree(existing)
    (root / "licenses/rust-packages.json").write_text(json.dumps(inventory, indent=2) + "\n")

    targets = [args.qemu]
    configs = [(args.vulkan_icd, "ICD"),
               (Path("/usr/share/vulkan/explicit_layer.d/VkLayer_khronos_validation.json"), "layer")]
    for config, key in configs:
        if not config.is_file():
            continue
        name = json.loads(config.read_text())[key]["library_path"]
        library = Path(name)
        if not library.is_absolute():
            library = config.parent / library
        if not library.is_file():
            entries = subprocess.check_output(["ldconfig", "-p"], text=True).splitlines()
            matches = [Path(line.split(" => ", 1)[1]) for line in entries
                       if " => " in line and line.split()[0] == Path(name).name]
            if not matches:
                owner = subprocess.run(["dpkg-query", "-S", config],
                                       capture_output=True, text=True)
                if owner.returncode == 0:
                    for entry in owner.stdout.splitlines():
                        package = entry.rsplit(": ", 1)[0]
                        files = subprocess.check_output(["dpkg-query", "-L", package], text=True)
                        matches.extend(Path(file) for file in files.splitlines()
                                       if Path(file).name == Path(name).name and Path(file).is_file())
            if not matches:
                raise RuntimeError("Cannot resolve a configured Vulkan library")
            library = matches[0]
        targets.append(library)
    for program in ["llvm-dis-21", "spirv-val", "glslc", "glslangValidator"]:
        if executable := shutil.which(program, path="/usr/bin:/bin"):
            targets.append(Path(executable))
    ldd = "\n".join(subprocess.check_output(["ldd", target], text=True) for target in targets)
    # Include dynamically loaded drivers, validation, and shader tools as well
    # as QEMU's direct dependencies. Publish package names and notices only.
    ldd += "\n" + "\n".join(str(target) for target in targets[1:])
    packages = {"mesa-vulkan-drivers", "libvulkan1"}
    for line in ldd.splitlines():
        for word in line.split():
            if not word.startswith("/"):
                continue
            candidates = [word, str(Path(word).resolve())]
            for path in candidates:
                result = subprocess.run(["dpkg-query", "-S", path], text=True, capture_output=True)
                if result.returncode == 0:
                    for entry in result.stdout.splitlines():
                        if entry.startswith("diversion "):
                            continue
                        packages.add(entry.rsplit(": ", 1)[0])
                    break
            else:
                raise RuntimeError(f"No package owns linked library {word}")
    host = root / "licenses/host"
    host.mkdir(exist_ok=True)
    host_inventory = []
    for name in sorted({package.split(":")[0] for package in packages}):
        notice = Path("/usr/share/doc") / name / "copyright"
        if not notice.is_file():
            raise RuntimeError(f"Missing copyright notice for {name}")
        shutil.copy2(notice, host / (name + ".copyright"))
        host_inventory.append({"package": name,
                               "notice": "host/" + name + ".copyright"})
    (root / "licenses/host-packages.json").write_text(json.dumps(host_inventory, indent=2) + "\n")
    common = root / "licenses/common"
    common.mkdir(exist_ok=True)
    for name in ["GPL-2", "GPL-3", "LGPL-2.1", "LGPL-3", "Apache-2.0", "BSD"]:
        shutil.copy2(Path("/usr/share/common-licenses") / name, common / name)
    print(f"Collected {len(inventory)} Rust packages and {len(host_inventory)} host package notices")


if __name__ == "__main__":
    main()
