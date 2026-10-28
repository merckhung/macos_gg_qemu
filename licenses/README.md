# Licenses and notices

License files are preserved as supplied; the inventory does not replace
per-file terms or relicense upstream material.

| Component | Pinned source | License evidence |
| --- | --- | --- |
| QEMU fork | `steelbrain/qemu-reims-vgpu@4895365e5bf9d344e494a39eceacb475079fedb5` | `qemu/LICENSE`, `qemu/COPYING`, `qemu/COPYING.LIB`; QEMU's top-level notice describes GPLv2 and the separately licensed components |
| Reims vGPU | `steelbrain/reims-vgpu@12adb1fb46953ae55d4a7b2d66d9110bb19b0b1a` | `reims-vgpu/LICENSE`, workspace Cargo metadata: `GPL-2.0-or-later OR LGPL-3.0-or-later` |
| Metal2Vulkan | `steelbrain/metal2vulkan@f6a96b7da82f3032e3fd7b1dc52c07bfd9b78dc8` | `metal2vulkan/LICENSE`, Cargo metadata: `LGPL-3.0-or-later` |
| keycodemapdb | `qemu/keycodemapdb@f5772a62ec52591ff6870b7e8ef32482371f22c6` | `keycodemapdb/LICENSE.BSD`, `keycodemapdb/LICENSE.GPL2` |
| This project's build/launch utilities | This repository | `../LICENSE`: GPL-2.0-or-later |

The Rust build collects dependency metadata and original license/notice files
into `bazel-bin/remis_vgpu-dist/`. `python3 tools/collect_licenses.py` copies
the selected dependency notices here. `rust-packages.json` records the pinned
Rust dependency versions and declarations. `host-packages.json` records runtime
package names and notice files without an installed-machine version inventory.
The notices cover linked QEMU libraries, the Vulkan loader, Mesa Lavapipe,
LLVM and SPIR-V tools, and available shader compilers and Vulkan validation
layers. The collector follows driver and layer manifests to include their
dynamically loaded dependencies. Original package copyright
files reference the standard texts retained under `common/`.

Apple macOS, firmware, and guest-owned assets are user-supplied, external to
this project, and are not licensed or redistributed by it. Screenshots document
the requested live test and do not grant rights to depicted software or artwork.
