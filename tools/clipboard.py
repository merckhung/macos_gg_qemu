#!/usr/bin/env python3
"""Share the clipboard between this Linux host and the macOS guest, both ways.

macOS ships no SPICE/vdagent clipboard agent, and QEMU's clipboard (VNC, GTK, qemu-vdagent)
needs one in the guest. This bridge uses what the guest does have: Remote Login (sshd) and
`pbcopy`/`pbpaste`, which reach the logged-in user's pasteboard from an SSH session of the same
user. On the host it uses the X11 CLIPBOARD selection through `xclip` (GNOME keeps the Wayland
and XWayland clipboards in sync), on the display in $DISPLAY.

  python3 tools/clipboard.py setup        # once: install the host's SSH key in the guest
  python3 tools/clipboard.py run          # the bridge; Ctrl+C stops it
  python3 tools/clipboard.py get|put TEXT # one-shot reads/writes of the guest's pasteboard

Text only (UTF-8). The guest is polled once a second over one long-lived SSH session, so a
copy on either side appears on the other within about two seconds.
"""
import argparse
import base64
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time

# One long-lived process in the guest that reads the pasteboard only when its change count moves,
# and prints each new text base64-encoded on its own line. Spawning pbpaste every second instead
# cost the guest a process launch and a TCC pasteboard check per poll, which kept tccd at a full
# CPU under TCG and slowed every app that asks TCC anything.
GUEST_LOOP = (
    "exec osascript -l JavaScript -e '"
    "ObjC.import(\"AppKit\"); ObjC.import(\"Foundation\");"
    "var pb = $.NSPasteboard.generalPasteboard, seen = -1;"
    "var out = $.NSFileHandle.fileHandleWithStandardOutput;"
    "while (true) {"
    " var n = pb.changeCount;"
    " if (n != seen) { seen = n;"
    "  var s = pb.stringForType($.NSPasteboardTypeString);"
    "  var d = s.isNil() ? $.NSData.data : s.dataUsingEncoding($.NSUTF8StringEncoding);"
    "  var line = \"CLIP:\" + ObjC.unwrap(d.base64EncodedStringWithOptions(0)) + \"\\n\";"
    "  out.writeData($(line).dataUsingEncoding($.NSUTF8StringEncoding)); }"
    " delay(1); }'"
)


def ssh_base(args):
    return ["ssh", "-p", str(args.port), "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            "-o", "ServerAliveInterval=15", "-o", "ConnectTimeout=60",
            "-o", "BatchMode=yes", f"{args.user}@{args.host}"]


def guest_put(args, text):
    subprocess.run(ssh_base(args) + ["LANG=en_US.UTF-8 pbcopy"], input=text.encode(),
                   check=True, timeout=120)


def guest_get(args):
    return subprocess.run(ssh_base(args) + ["LANG=en_US.UTF-8 pbpaste"], capture_output=True,
                          check=True, timeout=120).stdout.decode(errors="replace")


def host_get():
    try:
        result = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True,
                                timeout=10)
    except (subprocess.SubprocessError, OSError):
        return None
    return result.stdout.decode(errors="replace") if result.returncode == 0 else None


def host_put(text):
    # xclip forks a child that serves the selection until another client takes it.
    subprocess.run(["xclip", "-selection", "clipboard", "-i"], input=text.encode(),
                   check=True, timeout=10)


def setup(args):
    """Install the host's public key for the guest user, answering the password prompt once."""
    key = Path(args.key).expanduser()
    if not key.is_file():
        raise SystemExit(f"{key} not found; make one with ssh-keygen -t ed25519")
    password = os.environ.get("GUEST_PASSWORD") or input("Guest password: ")
    with tempfile.TemporaryDirectory() as scratch:
        askpass = Path(scratch) / "askpass"
        askpass.write_text("#!/bin/sh\nprintf '%s\\n' \"$GUEST_PASSWORD\"\n")
        askpass.chmod(0o700)
        env = dict(os.environ, SSH_ASKPASS=str(askpass), SSH_ASKPASS_REQUIRE="force",
                   GUEST_PASSWORD=password)
        command = ssh_base(args)
        command[command.index("BatchMode=yes")] = "BatchMode=no"
        remote = ("mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && "
                  "chmod 600 ~/.ssh/authorized_keys && "
                  "(grep -qxF \"$K\" ~/.ssh/authorized_keys || echo \"$K\" >> ~/.ssh/authorized_keys)")
        subprocess.run(command + [f"K='{key.read_text().strip()}'; {remote}"], env=env,
                       stdin=subprocess.DEVNULL, check=True, timeout=300)
    print("key installed")


def run(args):
    last = None
    while True:
        # The host's clipboard wins at (re)connection: it goes to the guest before the guest
        # is watched, so the guest's first report is that same text and changes nothing.
        now = host_get()
        if now:
            try:
                guest_put(args, now)
            except (subprocess.SubprocessError, OSError) as error:
                print(f"guest not reachable ({error}); retrying", flush=True)
                time.sleep(5)
                continue
        last = now
        guest = subprocess.Popen(ssh_base(args) + [GUEST_LOOP], stdout=subprocess.PIPE,
                                 stdin=subprocess.DEVNULL)
        sel = selectors.DefaultSelector()
        sel.register(guest.stdout, selectors.EVENT_READ)
        buffer = b""
        print("bridge connected", flush=True)
        try:
            while guest.poll() is None:
                for _ in sel.select(timeout=1.0):
                    chunk = os.read(guest.stdout.fileno(), 1 << 20)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if not line.startswith(b"CLIP:"):
                            continue
                        text = base64.b64decode(line[5:]).decode(errors="replace")
                        if text and text != last:
                            host_put(text)
                            last = text
                            print(f"guest -> host: {len(text)} chars", flush=True)
                now = host_get()
                if now and now != last:
                    guest_put(args, now)
                    last = now
                    print(f"host -> guest: {len(now)} chars", flush=True)
        except (subprocess.SubprocessError, OSError) as error:
            guest.terminate()
            print(f"bridge error ({error}); retrying", flush=True)
        except KeyboardInterrupt:
            guest.terminate()
            return
        finally:
            sel.close()
            if guest.stdout:
                guest.stdout.close()
            guest.wait()
        print("bridge disconnected; retrying", flush=True)
        time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=10022)
    parser.add_argument("--user", default=os.environ.get("GUEST_USER", "user"))
    parser.add_argument("--key", default="~/.ssh/id_ed25519.pub")
    parser.add_argument("command", choices=["setup", "run", "get", "put"])
    parser.add_argument("text", nargs="?")
    args = parser.parse_args()
    if args.command == "setup":
        setup(args)
    elif args.command == "run":
        run(args)
    elif args.command == "get":
        sys.stdout.write(guest_get(args))
    else:
        guest_put(args, args.text if args.text is not None else sys.stdin.read())


if __name__ == "__main__":
    main()
