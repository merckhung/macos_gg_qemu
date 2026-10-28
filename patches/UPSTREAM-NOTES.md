# Patch provenance

Bazel fetches pinned public source archives (`MODULE.bazel`) and applies these
plain unified diffs before building. Patch files contain no mail headers, local
machine paths, or author metadata (`tools/check_public_tree.py`).

| Patch | Upstream repository | Base revision | Purpose |
| --- | --- | --- | --- |
| `qemu-goldengate.patch` | `steelbrain/qemu-reims-vgpu` | `4895365e5bf9d344e494a39eceacb475079fedb5` | Run macOS 27 Golden Gate (`vmapple`) under arm64 TCG on Linux x86-64 and satisfy `AppleVirtIOSound` (`virtio-sound-pci`) |
| `reims-vgpu-goldengate.patch` | `steelbrain/reims-vgpu` | `12adb1fb46953ae55d4a7b2d66d9110bb19b0b1a` | Support 16 KiB arm64 guest pages on 4 KiB x86-64 hosts, `Ctrl+Alt+F` fullscreen toggle on Linux SDL, pinned `metal2vulkan` lock sources, and relaxed runtime `Cubic -> Linear` sampler & `2x -> 4x` MSAA Lavapipe fallbacks |

`provenance.json` records the upstream base revisions and SHA-256 digests of the
repository patches. Cargo fetches `steelbrain/metal2vulkan` at the exact git
revision (`f6a96b7da82f3032e3fd7b1dc52c07bfd9b78dc8`) declared in upstream
`reims-vgpu`'s `Cargo.toml`.

## `qemu-goldengate.patch`

Upstream `steelbrain/qemu-reims-vgpu` (`4895365e5bf9d344e494a39eceacb475079fedb5`,
pinned as `vendor/qemu` by `steelbrain/reims-vgpu`) wires QEMU's `apple-gfx`
device to the `libreims_vgpu` C API (`reims-vgpu.pc`), targeting HVF on macOS
and KVM on arm64/x86-64. To boot macOS 27.0 Golden Gate (`26A428`, arm64
`RELEASE_ARM64_VMAPPLE`) under multithreaded TCG on an x86-64 Linux host with
working audio, `qemu-goldengate.patch` makes the following changes:

1. **`vmapple` under aarch64 TCG (`hw/vmapple/Kconfig`, `meson.build`, `hw/vmapple/vmapple.c`, `hw/vmapple/aes.c`, `hw/vmapple/bdif.c`, `hw/vmapple/cfg.c`):**
   - Enables `MAC_PVG` and `VMAPPLE` on `aarch64-softmmu` without requiring macOS `ParavirtualizedGraphics.framework` (`meson.build` links `reims-vgpu` via `pkg-config`).
   - Configures the `vmapple` machine timebase, ECID/UUID/AUX/root block wiring, AES engine, backdoor interface (`bdif`), and machine configuration table expected by `AVPBooter.vmapple2.bin` and XNU 27.0.
2. **ARMv8.3+ pointer authentication and PSCI under TCG (`target/arm/cpu.h`, `target/arm/tcg/pauth_helper.c`, `target/arm/tcg/psci.c`):**
   - Preserves Apple Silicon pointer-authentication semantics and PSCI secondary-core bring-up expected by XNU's arm64 `vmapple` kernel under multithreaded TCG.
3. **macOS 27 `AppleVirtIOSound` compatibility (`hw/audio/virtio-snd-pci.c`, `hw/audio/virtio-snd.c`, `include/hw/audio/virtio-snd.h`, `hw/vmapple/vmapple.c`):**
   - Relinks PCI capabilities in strictly ascending PCI configuration-space offset order (`x-ascending-capabilities=on`, matching `virtio-net-pci`); macOS 27's `AppleVirtIOPCI` refuses to start devices whose capability list links from higher offsets to lower offsets.
   - Implements `VIRTIO_SND_R_JACK_INFO` and `VIRTIO_SND_R_CHMAP_INFO` (with `jacks=0` and `chmaps=2`, stereo `FL`/`FR` output + input channel maps) and wires `virtio-sound-pci` onto the `vmapple` PCIe root complex so `AppleVirtIOSound` registers `"Apple Virtual Sound Device"` (2ch 48 kHz) in CoreAudio.

## `reims-vgpu-goldengate.patch`

Upstream `steelbrain/reims-vgpu` (`12adb1fb46953ae55d4a7b2d66d9110bb19b0b1a`)
implements Apple's ParavirtualizedGraphics / Metal serialization protocol over
Vulkan (`metal2vulkan`). To run an arm64 macOS 27 guest on an x86-64 Linux host,
`reims-vgpu-goldengate.patch` makes five focused additions:

1. **Configurable guest page shift (`RVG_GUEST_PAGE_SHIFT=14`):**
   - Upstream derives `GUEST_PAGE_SHIFT` (`APV_PAGE_SHIFT`) from host `cfg(target_arch)` (`12` on `x86_64`, `14` on `aarch64`). An arm64 Apple Silicon guest running under TCG on an x86-64 host uses 16 KiB guest pages (`1 << 14`) in ParavirtualizedGraphics task page tables and IOSurface page mappers (`crates/reims-vgpu-core/src/guest/records.rs`, `crates/reims-vgpu-core/src/iosurface/descriptor.rs`, `crates/reims-vgpu-core/src/iosurface/device.rs`). `tools/build.py` sets `RVG_GUEST_PAGE_SHIFT=14` at compile time while keeping the host's native `os_page_size()` (`sysconf(_SC_PAGESIZE)` = 4 KiB) for host `mmap` alignment.
2. **Linux SDL window `Ctrl+Alt+F` fullscreen toggle (`crates/reims-vgpu-window/src/native/linux/input.rs`, `sdl.rs`, `features.md`):**
   - Intercepts `Ctrl+Alt+F` on key-down in the Linux SDL3 window backend (suppressing the guest `F` key event, mirroring `Ctrl+Alt+Super` grab release) and toggles `SDL_SetWindowFullscreen` against the current `SDL_WINDOW_FULLSCREEN` window flag. Covered by unit tests in `native::linux::input::tests`.
3. **Relaxed runtime `MTLSamplerMinMagFilterCubic` fallback (`crates/reims-vgpu-vulkan/src/gpu/states.rs`):**
   - `metal2vulkan`'s shader-emulated bicubic sampler only covers single-level (`NotMipmapped`), isotropic, non-argument-buffer, measured-format bindings; when macOS apps such as `Weather.app` bind a runtime `MTLSamplerMinMagFilterCubic` (raw value `2`) together with a mip filter (`MTLSamplerMipFilterLinear`) on a mipmapped `ASTC4x4SRGB` sky LUT texture, `drawPrimitives` otherwise returns `BindError::Unsupported(NotImplemented(SamplerBicubicFilter))` and aborts the QEMU process.
   - Outside strict mode (`REIMS_VGPU_STRICT=1` / `#[cfg(test)]`), `sampler_descriptor` substitutes `SamplerMinMagFilter::Linear` (`1`) for runtime filter `2` and logs a once-per-process warning. Strict mode preserves `SamplerMinMagFilter::Cubic` unchanged. Covered by `cubic_sampler_approximates_with_linear_outside_strict_mode`.
4. **Relaxed runtime `sampleCount = 2 -> 4` MSAA promotion on 4x-only Vulkan drivers (`crates/reims-vgpu-vulkan/src/api.rs`, `gpu/device.rs`, `gpu/resource.rs`, `gpu/states.rs`, `gpu/encode_render.rs`, `gpu/encode_blit.rs`, `gpu/command.rs`):**
   - Apple's ParavirtualizedGraphics wire protocol reports a single scalar `maxSampleCount` (`key 1 = 4`), causing the macOS guest driver to report `supportsTextureSampleCount:2 == YES` whenever 4x MSAA is supported. Mesa Lavapipe exposes `VK_SAMPLE_COUNT_1_BIT | VK_SAMPLE_COUNT_4_BIT` without `VK_SAMPLE_COUNT_2_BIT`, so SceneKit / 2x-MSAA apps such as `Chess.app` otherwise fail `newTextureWithDescriptor:` with `Unsupported { feature: TextureSampleCount, needs: FramebufferColorSampleCount(2) }` and abort QEMU.
   - Outside strict mode, when `!supports_texture_sample_count(2) && supports_texture_sample_count(4)`, `VulkanDevice::effective_sample_count` promotes 2-sample texture descriptors, heap texture size queries, render/tile pipeline `raster_sample_count`, and render-pass `default_raster_sample_count` to 4 samples (logging once per process), while `TextureObject` preserves `guest_sample_count = 2` for `validate_texture_dimension` and translates `CopyBufferToTexture` / `CopyTextureToBuffer` sample layouts (`(s0, s1) <-> (s0, s0, s1, s1)`) through shared staging buffers and post-GPU completion callbacks. Strict mode and drivers with native 2x MSAA preserve `sampleCount = 2` unchanged. Covered by `sample_count_2_promotes_to_4_outside_strict_mode_when_2x_msaa_lacked`.
5. **`Cargo.lock` git source URLs (`Cargo.lock`):**
   - Adds the explicit `source = "git+https://github.com/steelbrain/metal2vulkan?rev=f6a96b7..."` lines matching `Cargo.toml` so `cargo --locked` succeeds without a local path override.
