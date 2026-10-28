#!/usr/bin/env python3
"""Exercise guest asset construction using synthetic inputs only."""
import base64
import hashlib
import json
from pathlib import Path
import plistlib
import tempfile
import unittest

from construct_assets import construct, AUX_METADATA_BYTES


class ConstructAssetsTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.config = self.root / "vm.json"
        self.aux = self.root / "aux.full.img"
        self.firmware = self.root / "firmware"
        self.output = self.root / "constructed"
        self.payload = bytes(range(251)) * 3
        self.aux.write_bytes(bytes(AUX_METADATA_BYTES) + self.payload)
        self.firmware.write_bytes(b"synthetic firmware fixture")
        self.write_config(platform=2)

    def write_config(self, platform):
        def encode(value):
            return base64.b64encode(plistlib.dumps(value, fmt=plistlib.FMT_BINARY)).decode()
        self.config.write_text(json.dumps({"hardwareModel": encode({"PlatformVersion": platform}),
                                           "machineId": encode({"ECID": 12345})}))

    def test_full_aux_is_trimmed_once_padded_and_hashed(self):
        original = self.aux.read_bytes()
        construct(self.config, self.aux, self.firmware, self.output, "full")
        packed = (self.output / "aux.block.img").read_bytes()
        self.assertEqual(len(packed), 1024)
        self.assertEqual(packed[:len(self.payload)], self.payload)
        self.assertEqual(packed[len(self.payload):], bytes(1024 - len(self.payload)))
        self.assertEqual((self.output / "ECID.txt").read_text(), "12345\n")
        manifest = json.loads((self.output / "assets.json").read_text())
        for name, record in manifest["assets"].items():
            self.assertEqual(record["sha256"], hashlib.sha256((self.output / name).read_bytes()).hexdigest())
        self.assertEqual(self.aux.read_bytes(), original)
        self.assertNotIn(str(self.root), json.dumps(manifest))

    def test_trimmed_aux_keeps_its_first_block(self):
        self.aux.write_bytes(self.payload)
        construct(self.config, self.aux, self.firmware, self.output, "trimmed")
        self.assertEqual((self.output / "aux.block.img").read_bytes()[:len(self.payload)], self.payload)

    def test_unsupported_platform_creates_no_output(self):
        self.write_config(platform=1)
        with self.assertRaisesRegex(ValueError, "platform-v2"):
            construct(self.config, self.aux, self.firmware, self.output, "full")
        self.assertFalse(self.output.exists())

    def test_existing_bundle_is_preserved(self):
        self.output.mkdir()
        marker = self.output / "preserve"
        marker.write_text("existing private data")
        with self.assertRaisesRegex(ValueError, "already exists"):
            construct(self.config, self.aux, self.firmware, self.output, "full")
        self.assertEqual(marker.read_text(), "existing private data")


if __name__ == "__main__":
    unittest.main()
