"""Local Bazel actions wrapping the upstream Cargo and Meson builds."""

def _remis_impl(ctx):
    out = ctx.actions.declare_directory(ctx.label.name + "-dist")
    ctx.actions.run(
        executable = "/usr/bin/python3",
        arguments = [
            ctx.file._driver.path,
            "remis",
            ctx.file.manifest.dirname,
            out.path,
        ],
        inputs = depset(ctx.files.srcs + [ctx.file._driver]),
        outputs = [out],
        use_default_shell_env = True,
        execution_requirements = {"no-sandbox": "1", "no-remote": "1"},
        mnemonic = "BuildRemisVgpu",
        progress_message = "Building patched Reims vGPU C library (Vulkan platform)",
    )
    return [DefaultInfo(files = depset([out]))]

remis_build = rule(
    implementation = _remis_impl,
    attrs = {
        "srcs": attr.label_list(allow_files = True),
        "manifest": attr.label(allow_single_file = True, mandatory = True),
        "_driver": attr.label(default = "//tools:build.py", allow_single_file = True),
    },
)

def _qemu_impl(ctx):
    out = ctx.actions.declare_directory(ctx.label.name + "-dist")
    remis = ctx.attr.remis[DefaultInfo].files.to_list()[0]
    ctx.actions.run(
        executable = "/usr/bin/python3",
        arguments = [
            ctx.file._driver.path,
            "qemu",
            ctx.file.configure.dirname,
            remis.path,
            ctx.file.keymap_manifest.dirname,
            out.path,
        ],
        inputs = depset(ctx.files.srcs + [ctx.file._driver, remis]),
        outputs = [out],
        use_default_shell_env = True,
        execution_requirements = {"no-sandbox": "1", "no-remote": "1"},
        mnemonic = "BuildQemu",
        progress_message = "Building patched QEMU for VMApple with Reims vGPU",
    )
    return [DefaultInfo(files = depset([out]))]

qemu_build = rule(
    implementation = _qemu_impl,
    attrs = {
        "srcs": attr.label_list(allow_files = True),
        "configure": attr.label(allow_single_file = True, mandatory = True),
        "keymap_manifest": attr.label(allow_single_file = True, mandatory = True),
        "remis": attr.label(mandatory = True),
        "_driver": attr.label(default = "//tools:build_qemu.py", allow_single_file = True),
    },
)

def _boot_impl(ctx):
    qemu = ctx.attr.qemu[DefaultInfo].files.to_list()[0]
    runner = ctx.actions.declare_file(ctx.label.name)
    ctx.actions.write(
        runner,
        "#!/bin/sh\nset -eu\n" +
        'runfiles="${RUNFILES_DIR:-$0.runfiles}"\n' +
        'exec /usr/bin/python3 "$runfiles/' + ctx.workspace_name + "/" + ctx.file._vm.short_path +
        '" launch --qemu "$runfiles/' + ctx.workspace_name + "/" + qemu.short_path +
        '/qemu-system-aarch64" "$@"\n',
        is_executable = True,
    )
    return [DefaultInfo(executable = runner, runfiles = ctx.runfiles(files = [ctx.file._vm, qemu]))]

boot_vm = rule(
    implementation = _boot_impl,
    executable = True,
    attrs = {
        "qemu": attr.label(mandatory = True),
        "_vm": attr.label(default = "//tools:vm.py", allow_single_file = True),
    },
)
