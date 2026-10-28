#!/usr/bin/env python3
"""One-time settings inside the Golden Gate guest, applied over SSH (Remote Login).

The guest runs under TCG on a CPU-only Vulkan device, roughly an order of magnitude slower than
the hardware macOS expects, and some defaults that are free on a Mac cost minutes here:

  wallpaper  Golden Gate's default wallpaper is an Aerial: a 300 s, 4096x2160 10-bit HEVC video
             (about 460 MB) that WallpaperAerialsExtension decodes in software here. After a
             login the agent first launches every wallpaper extension, then times out acquiring
             the Aerial (`makeWallpaper ... WallpaperExtensionError (3)`, watchdog timeouts) and
             falls back, so the desktop stays black for about ten minutes, and the decode keeps
             the guest's CPUs busy afterwards. This replaces it with a still of the same Aerial
             (its first frame, extracted on the host with ffmpeg), which the image extension
             shows as soon as it starts.
  power      Display sleep, the screen saver and the password on wake off: a sleeping guest
             display stops presenting, which looks like a frozen window, and waking it needs a
             second login.
  all        both.

  python3 tools/guest_setup.py all            # after `tools/clipboard.py setup` installed the key
"""
import argparse
import getpass
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import tempfile
import urllib.parse

AERIALS = "Library/Application Support/com.apple.wallpaper/aerials/videos"
STORE_PLIST = "Library/Application Support/com.apple.wallpaper/Store/Index.plist"
STILL = "Pictures/Golden Gate.jpg"


def ssh(args, command, **kwargs):
    return subprocess.run(["ssh", "-p", str(args.port), "-o", "StrictHostKeyChecking=no",
                           "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                           "-o", "BatchMode=yes", "-o", "ConnectTimeout=120",
                           f"{args.user}@{args.host}", command], check=True, timeout=1800, **kwargs)


def rewrite_wallpaper_store(plist_bytes: bytes, guest_still_path: str) -> bytes:
    uri = "file://" + urllib.parse.quote(guest_still_path)
    cfg = plistlib.dumps({"type": "imageFile", "url": {"relative": uri}}, fmt=plistlib.FMT_BINARY)
    data = plistlib.loads(plist_bytes)

    def walk(node):
        if isinstance(node, dict):
            if "Provider" in node and "aerial" in str(node.get("Provider", "")).lower():
                node["Provider"] = "com.apple.wallpaper.choice.image"
                node["Configuration"] = cfg
                node.pop("Files", None)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return plistlib.dumps(data, fmt=plistlib.FMT_BINARY)


def wallpaper(args):
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is needed on the host to extract the Aerial's still")
    probe = ssh(
        args,
        f'printf "%s\\n" "$HOME" && ls -S "$HOME/{AERIALS}" | head -1 && '
        f'[ -f "$HOME/{STORE_PLIST}" ] && echo HAS_STORE || echo NO_STORE',
        capture_output=True,
        text=True,
    )
    lines = probe.stdout.splitlines()
    guest_home = lines[0].strip() if len(lines) > 0 else ""
    name = lines[1].strip() if len(lines) > 1 else ""
    has_store = len(lines) > 2 and lines[2].strip() == "HAS_STORE"
    if not guest_home or not name.endswith(".mov"):
        raise SystemExit("the guest has no downloaded Aerial video; nothing to replace")
    with tempfile.TemporaryDirectory() as scratch:
        video = Path(scratch) / "aerial.mov"
        still = Path(scratch) / "still.jpg"
        with video.open("wb") as video_out:
            ssh(args, f'cat "$HOME/{AERIALS}/{name}"', stdout=video_out)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-frames:v", "1",
                        "-vf", "scale=in_range=tv:out_range=pc,format=rgb24", "-q:v", "2",
                        str(still)], check=True)
        ssh(args, f'mkdir -p "$HOME/Pictures" && rm -f "$HOME/Pictures/Golden\\\\ Gate.jpg" && cat > "$HOME/{STILL}"',
            input=still.read_bytes())
    if has_store:
        raw_store = ssh(args, f'cat "$HOME/{STORE_PLIST}"', capture_output=True).stdout
        rewritten = rewrite_wallpaper_store(raw_store, f"{guest_home}/{STILL}")
        ssh(args, f'cat > "$HOME/{STORE_PLIST}"', input=rewritten)
    ssh(args, "killall WallpaperAgent WallpaperAerialsExtension 2>/dev/null || true")
    print(f"desktop picture: ~/{STILL} (first frame of {name})")


def power(args):
    quoted = shlex.quote(args.password)
    ssh(args, "sudo -S pmset -a displaysleep 0 sleep 0 disksleep 0 && "
              "defaults -currentHost write com.apple.screensaver idleTime -int 0 && "
              f"sysadminctl -screenLock off -password {quoted}",
        input=(args.password + "\n").encode())
    print("display sleep, screen saver and password on wake: off")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=10022)
    parser.add_argument("--user", default="user")
    parser.add_argument("--password", default=os.environ.get("GUEST_PASSWORD"),
                        help="the guest user's password, for sudo and sysadminctl "
                             "(default: $GUEST_PASSWORD, else asked)")
    parser.add_argument("command", choices=["wallpaper", "power", "all"])
    args = parser.parse_args()
    if args.command in ("power", "all") and not args.password:
        args.password = getpass.getpass("Guest password: ")
    if args.command in ("wallpaper", "all"):
        wallpaper(args)
    if args.command in ("power", "all"):
        power(args)


if __name__ == "__main__":
    main()
