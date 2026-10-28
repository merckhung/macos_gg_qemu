#!/usr/bin/env python3
"""Launch Golden Gate and drive this run's QMP endpoint from the host."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT.parent / (ROOT.name + "-local")


class QMP:
    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX)
        try:
            self.socket.settimeout(30)
            self.socket.connect(str(path))
            self.stream = self.socket.makefile("rwb", buffering=0)
            self.receive()
            self.command("qmp_capabilities")
        except Exception:
            self.socket.close()
            raise

    def receive(self):
        line = self.stream.readline()
        if not line:
            raise RuntimeError("QMP connection closed")
        return json.loads(line)

    def command(self, name, **arguments):
        self.stream.write((json.dumps({"execute": name, "arguments": arguments}) + "\n").encode())
        while True:
            response = self.receive()
            if "error" in response:
                raise RuntimeError(response["error"])
            if "return" in response:
                return response["return"]

    def key(self, *codes):
        self.command("send-key", keys=[{"type": "qcode", "data": c} for c in codes], **{"hold-time": 200})
        time.sleep(0.4)

    def type(self, text):
        plain = {" ": "spc", "\n": "ret", "\t": "tab", "-": "minus", "=": "equal",
                 "[": "bracket_left", "]": "bracket_right", ";": "semicolon",
                 "'": "apostrophe", "`": "grave_accent", "\\": "backslash",
                 ",": "comma", ".": "dot", "/": "slash"}
        shifted = dict(zip('!@#$%^&*()_+{}:"~|<>?',
                           ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0', 'minus',
                            'equal', 'bracket_left', 'bracket_right', 'semicolon', 'apostrophe',
                            'grave_accent', 'backslash', 'comma', 'dot', 'slash']))
        keys = []
        for char in text:
            if char in shifted:
                keys.append(("shift", shifted[char]))
            elif char in plain:
                keys.append((plain[char],))
            elif char.isascii() and char.isalnum():
                keys.append(("shift", char.lower()) if char.isupper() else (char,))
            else:
                raise ValueError("Only US keyboard ASCII text is supported")
        for codes in keys:
            self.key(*codes)

    def close(self):
        self.stream.close()
        self.socket.close()


def latest_run(local):
    return (local / "runtime/latest").resolve(strict=True)


def require_stopped(run):
    try:
        connection = QMP(run / "qmp.sock")
    except (OSError, RuntimeError) as error:
        if isinstance(error, TimeoutError):
            raise RuntimeError("QMP observation timed out; confirm the VM's state before launching") from error
        pid_file = run / "pid"
        if not pid_file.is_file():
            raise RuntimeError("Run has no PID record; confirm it is stopped before launching") from error
        pid = int(pid_file.read_text())
        command_line = Path(f"/proc/{pid}/cmdline")
        if command_line.exists() and str(run).encode() in command_line.read_bytes():
            raise RuntimeError(f"QEMU PID {pid} is live but QMP is unavailable") from error
    else:
        connection.close()
        raise RuntimeError("The VM still answers QMP; stop it before starting another")


def host_screen_size():
    """The host screen's size in pixels, from X11 on $DISPLAY, or None."""
    try:
        out = subprocess.run(["xdpyinfo"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        if "dimensions:" in line:
            width, height = line.split()[1].split("x")
            return int(width), int(height)
    return None


def host_screen_scale():
    """The X11 desktop's pixels-per-point scale (Xft.dpi / 96, else GDK_SCALE, else 1.0)."""
    try:
        out = subprocess.run(["xrdb", "-query"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    for line in out.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "Xft.dpi":
            try:
                dpi = float(value.strip())
            except ValueError:
                break
            if dpi > 0:
                return dpi / 96.0
    try:
        gdk = float(os.environ.get("GDK_SCALE", ""))
        if gdk > 0:
            return gdk
    except ValueError:
        pass
    return 1.0


def auto_resolution(size=None, scale=None):
    """Guest resolution string matching the host screen's pixels and HiDPI backing scale."""
    if size is None:
        size = host_screen_size()
    if not size:
        return "1920x1080"
    width, height = size
    if scale is None:
        scale = host_screen_scale()
    if scale > 1.3:
        ppi = round(110.0 * scale)
        if 1 <= ppi <= 65535 and width >= 1920:
            # Match apple_gfx_guest_scale_unknown in hw/display/apple-gfx.c so QEMU accepts @ppi.
            import struct
            mm_f32 = struct.unpack("f", struct.pack("f", width / (ppi / 25.46)))[0]
            mm = int(float(mm_f32) + 0.5)
            cm = (mm + 9) // 10
            if 0 < mm <= 65535 and cm >= 29 and width * 35 >= 2021 * cm:
                return f"{width}x{height}@{ppi}"
    return f"{width}x{height}"


def launch(args):
    qemu = args.qemu.resolve(strict=True)
    assets = args.local_dir / "assets"
    disk = (assets / "GoldenGate-27.0-26A428.img").resolve(strict=True)
    firmware = (assets / "AVPBooter.vmapple2.bin").resolve(strict=True)
    aux = (assets / "aux.block.img").resolve(strict=True)
    ecid = (assets / "ECID.txt").read_text().strip()
    runtime = args.local_dir / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    previous = runtime / "latest"
    if previous.exists():
        require_stopped(previous.resolve())
    run = runtime / datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run.mkdir()
    if args.continue_from:
        # Chain a new overlay onto a stopped run's disk, so the guest keeps what that run
        # changed (first-login setup, settings, Remote Login), and take its NVRAM along.
        base = args.continue_from.resolve(strict=True)
        shutil.copy2(base / "aux.img", run / "aux.img")
        backing, backing_format = base / "root.qcow2", "qcow2"
    else:
        shutil.copy2(aux, run / "aux.img")
        backing, backing_format = disk, "raw"
    subprocess.run([qemu.parent / "qemu-img", "create", "-f", "qcow2", "-F", backing_format,
                    "-b", backing, run / "root.qcow2"], check=True, stdout=subprocess.DEVNULL)
    resolution = args.resolution
    if resolution == "auto":
        resolution = auto_resolution()
    command = [str(qemu), "-L", str(qemu.parent / "share"),
               "-machine", f"vmapple,uuid={ecid},memory-backend=mem0",
               # apple-gfx maps guest RAM through a shared file. Isolated tasks need its host
               # mapping aligned to the guest's 16 KiB pages, which memfd does not promise.
               "-object", f"memory-backend-file,id=mem0,size={args.memory},share=on,"
                          "mem-path=/dev/shm,align=2M",
               "-global", f"apple-gfx-mmio.resolution={resolution}",
               # The largest translation cache TCG allows on an x86-64 host: macOS runs far
               # more code than the 1 GiB default holds, and each full cache flushes every
               # translation (ten times in a quarter hour of login at the default).
               "-accel", "tcg,thread=multi,tb-size=2048", "-cpu", "max", "-smp", str(args.cpus),
               "-m", args.memory, "-display", "none", "-vnc", f"127.0.0.1:{args.vnc_display}",
               "-monitor", "none", "-serial", f"file:{run}/serial.log",
               "-qmp", f"unix:{run}/qmp.sock,server=on,wait=off",
               "-action", "panic=pause,shutdown=pause", "-no-reboot", "-bios", str(firmware),
               "-drive", f"if=pflash,format=raw,file={run}/aux.img,file.locking=off,cache=writeback",
               "-drive", f"if=pflash,format=raw,file={disk},readonly=on,file.locking=off",
               "-drive", f"if=none,id=aux,format=raw,file={run}/aux.img,file.locking=off,cache=writeback",
               "-device", "vmapple-virtio-blk-pci,variant=aux,drive=aux,share-rw=on",
               "-drive", f"if=none,id=root,format=qcow2,file={run}/root.qcow2,cache=writeback",
               "-device", "vmapple-virtio-blk-pci,variant=root,drive=root,share-rw=on",
               "-netdev", f"user,id=net0,ipv6=off,hostfwd=tcp:127.0.0.1:{args.ssh_port}-:22",
               "-device", "virtio-net-pci,netdev=net0,mac=52:54:00:76:61:70,romfile=",
               "-audiodev", f"{args.audio},id=snd0"]
    if args.sound in ("virtio", "both"):
        # macOS 27's AppleVirtIO kext has AppleVirtIOSound for virtio device 0x19.
        command += ["-device", "virtio-sound-pci,audiodev=snd0"]
    if args.sound in ("usb", "both"):
        # AppleUSBAudio drives QEMU's USB Audio Class device on vmapple's xHCI.
        command += ["-device", "usb-audio,audiodev=snd0"]
    for event in args.trace:
        command += ["-trace", event]
    environment = os.environ.copy()
    environment.update(QEMU_VMAPPLE_BOOT_ARGS="serial=3 -v -apfs_vm_edtforce",
                       VK_DRIVER_FILES=str(args.vulkan_icd), VK_ICD_FILENAMES=str(args.vulkan_icd),
                       REIMS_VGPU_LOG=args.log_level)
    if args.headless:
        environment["REIMS_VGPU_WINDOW"] = "0"
    if args.full_screen:
        environment["REIMS_VGPU_WINDOW_BORDERLESS"] = "1"
    if args.window_driver != "auto":
        # x11 (through XWayland on a Wayland desktop) leaves the window inspectable from the
        # host with xwininfo/xprop/xwd, which a Wayland client's window is not.
        environment["SDL_VIDEO_DRIVER"] = args.window_driver
    # Keep shader caches and other temporary files with the private VM state.
    environment["TMPDIR"] = str(run)
    with (run / "qemu.log").open("wb") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   env=environment, start_new_session=True, cwd=run)
    (run / "pid").write_text(str(process.pid) + "\n")
    (run / "launch.json").write_text(json.dumps({"argv": command, "pid": process.pid,
                                                "icd": str(args.vulkan_icd),
                                                "resolution": resolution,
                                                "window": not args.headless,
                                                "continue_from": str(args.continue_from or "")},
                                               indent=2) + "\n")
    if previous.is_symlink():
        previous.unlink()
    previous.symlink_to(run.name, target_is_directory=True)
    time.sleep(2)
    if process.poll() is not None:
        raise RuntimeError((run / "qemu.log").read_text(errors="replace"))
    print(f"QEMU PID {process.pid}; VNC localhost:{5900 + args.vnc_display}; "
          f"SSH localhost:{args.ssh_port}; resolution {resolution}")
    print(run)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-dir", type=Path, default=LOCAL)
    sub = parser.add_subparsers(dest="command", required=True)
    boot = sub.add_parser("launch")
    boot.add_argument("--qemu", type=Path, default=ROOT / "bazel-bin/qemu-dist/qemu-system-aarch64")
    boot.add_argument("--cpus", type=int, default=8)
    boot.add_argument("--memory", default="8G")
    boot.add_argument("--vulkan-icd", type=Path, default=Path("/usr/share/vulkan/icd.d/lvp_icd.json"))
    boot.add_argument("--vnc-display", type=int, default=1)
    boot.add_argument("--ssh-port", type=int, default=10022)
    boot.add_argument("--resolution", default="auto",
                      help="guest mode WxH[@ppi], 'host', or 'auto' (the host screen's size)")
    boot.add_argument("--headless", action="store_true",
                      help="no window (VNC and QMP only)")
    boot.add_argument("--full-screen", action="store_true",
                      help="open the window full screen (Ctrl+Alt+F switches at any time)")
    boot.add_argument("--window-driver", default="x11", choices=["x11", "wayland", "auto"],
                      help="SDL video driver for the window")
    boot.add_argument("--sound", default="virtio", choices=["usb", "virtio", "both", "none"],
                      help="guest sound card")
    boot.add_argument("--trace", action="append", default=[], help="QEMU -trace pattern")
    boot.add_argument("--audio", default="pa", help="QEMU audiodev driver (pa, alsa, none)")
    boot.add_argument("--log-level", default="info", help="REIMS_VGPU_LOG filter")
    boot.add_argument("--continue-from", type=Path,
                      help="a stopped run directory whose disk and NVRAM the new run continues")
    sub.add_parser("status")
    sub.add_parser("stop")
    capture = sub.add_parser("screenshot")
    capture.add_argument("output", type=Path)
    key = sub.add_parser("key")
    key.add_argument("codes", nargs="+")
    typing = sub.add_parser("type")
    typing.add_argument("--file", type=Path, help="Read text from a private file; otherwise read stdin")
    click = sub.add_parser("click")
    click.add_argument("x", type=int)
    click.add_argument("y", type=int)
    click.add_argument("--width", type=int, help="guest screen width (default: measured)")
    click.add_argument("--height", type=int, help="guest screen height (default: measured)")
    args = parser.parse_args()
    args.local_dir = args.local_dir.resolve()
    if args.command == "launch":
        launch(args)
        return
    run = latest_run(args.local_dir)
    if args.command == "status":
        try:
            connection = QMP(run / "qmp.sock")
        except (OSError, RuntimeError):
            print(json.dumps({"running": False, "status": "stopped"}))
            return
        try:
            print(json.dumps(connection.command("query-status")))
        finally:
            connection.close()
        return
    connection = QMP(run / "qmp.sock")
    try:
        if args.command == "stop":
            connection.command("quit")
        elif args.command == "screenshot":
            from PIL import Image
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="capture-", dir=run) as scratch:
                raw = Path(scratch) / "screen.ppm"
                connection.command("screendump", filename=str(raw), format="ppm")
                with Image.open(raw) as capture:
                    capture.save(output, format="PNG")
            print(output)
        elif args.command == "key":
            connection.key(*args.codes)
        elif args.command == "type":
            connection.type(args.file.read_text() if args.file else sys.stdin.read())
        elif args.command == "click":
            if not args.width or not args.height:
                # Absolute pointer values span the guest's screen, so scale by its real size.
                from PIL import Image
                with tempfile.TemporaryDirectory(prefix="capture-", dir=run) as scratch:
                    raw = Path(scratch) / "screen.ppm"
                    connection.command("screendump", filename=str(raw), format="ppm")
                    with Image.open(raw) as capture:
                        args.width = args.width or capture.size[0]
                        args.height = args.height or capture.size[1]
            connection.command("input-send-event", events=[
                {"type": "abs", "data": {"axis": "x", "value": round(args.x * 32767 / (args.width - 1))}},
                {"type": "abs", "data": {"axis": "y", "value": round(args.y * 32767 / (args.height - 1))}}])
            time.sleep(0.25)
            for down in [True, False]:
                connection.command("input-send-event", events=[
                    {"type": "btn", "data": {"button": "left", "down": down}}])
                time.sleep(0.1)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
