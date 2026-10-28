#!/usr/bin/env python3
"""Unit tests for vm.py and guest_setup.py helpers and defaults."""
import io
import json
import plistlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import clipboard
import guest_setup
import vm


class VmAndGuestSetupTest(unittest.TestCase):
    def test_host_screen_size_parses_xdpyinfo_dimensions(self):
        fake = subprocess.CompletedProcess(
            args=["xdpyinfo"],
            returncode=0,
            stdout="screen #0:\n  dimensions:    1920x954 pixels (508x252 millimeters)\n",
        )
        with mock.patch.object(vm.subprocess, "run", return_value=fake):
            self.assertEqual(vm.host_screen_size(), (1920, 954))

    def test_auto_resolution_preserves_1x_and_hidpi_scales(self):
        self.assertEqual(vm.auto_resolution((1920, 954), 1.0), "1920x954")
        self.assertEqual(vm.auto_resolution((3840, 2008), 2.0), "3840x2008@220")
        self.assertEqual(vm.auto_resolution((1280, 800), 2.0), "1280x800")

    def test_host_screen_scale_reads_xft_dpi(self):
        fake = subprocess.CompletedProcess(
            args=["xrdb", "-query"],
            returncode=0,
            stdout="Xft.antialias:\t1\nXft.dpi:\t192\n",
        )
        with mock.patch.object(vm.subprocess, "run", return_value=fake):
            self.assertEqual(vm.host_screen_scale(), 2.0)

    def test_launch_defaults_use_virtio_sound_and_auto_resolution(self):
        with mock.patch.object(sys, "argv", ["vm.py", "launch"]), \
             mock.patch.object(vm, "launch") as mocked_launch:
            vm.main()
            args = mocked_launch.call_args[0][0]
            self.assertEqual(args.sound, "virtio")
            self.assertEqual(args.resolution, "auto")
            self.assertEqual(args.window_driver, "x11")

    def test_status_reports_stopped_when_qmp_socket_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp)
            run = local / "runtime/20261027T000000Z"
            run.mkdir(parents=True)
            (local / "runtime/latest").symlink_to(run.name, target_is_directory=True)
            out = io.StringIO()
            with mock.patch.object(sys, "argv", ["vm.py", "--local-dir", str(local), "status"]), \
                 mock.patch("sys.stdout", out):
                vm.main()
            self.assertEqual(json.loads(out.getvalue()), {"running": False, "status": "stopped"})

    def test_clipboard_host_get_returns_none_on_xclip_timeout(self):
        with mock.patch.object(
            clipboard.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd=["xclip"], timeout=10),
        ):
            self.assertIsNone(clipboard.host_get())

    def test_guest_setup_still_uses_relative_home_path(self):
        self.assertFalse(guest_setup.STILL.startswith("/"))
        self.assertEqual(guest_setup.STILL, "Pictures/Golden Gate.jpg")

    def test_rewrite_wallpaper_store_replaces_aerial_provider_on_host(self):
        raw = plistlib.dumps(
            {
                "AllSpacesAndDisplays": {
                    "Desktop": {
                        "Content": {
                            "Choices": [
                                {
                                    "Provider": "com.apple.wallpaper.choice.aerials",
                                    "Configuration": b"old",
                                    "Files": ["video.mov"],
                                }
                            ]
                        }
                    }
                }
            },
            fmt=plistlib.FMT_BINARY,
        )
        rewritten = plistlib.loads(
            guest_setup.rewrite_wallpaper_store(raw, "/guest-dir/Pictures/Golden Gate.jpg")
        )
        choice = rewritten["AllSpacesAndDisplays"]["Desktop"]["Content"]["Choices"][0]
        self.assertEqual(choice["Provider"], "com.apple.wallpaper.choice.image")
        self.assertNotIn("Files", choice)
        inner = plistlib.loads(choice["Configuration"])
        self.assertEqual(
            inner,
            {
                "type": "imageFile",
                "url": {"relative": "file:///guest-dir/Pictures/Golden%20Gate.jpg"},
            },
        )


if __name__ == "__main__":
    unittest.main()
