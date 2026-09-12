"""Validate generated manifest URLs and resource references without changing Office."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import urllib.parse
import xml.etree.ElementTree as ET


@unittest.skipUnless(os.name == "nt", "Windows manifest generator")
class ManifestTests(unittest.TestCase):
    def test_real_frontend_token_encoding_hosts_and_resources(self):
        shell = shutil.which("powershell.exe")
        if not shell:
            self.skipTest("PowerShell is not available")
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "manifest.xml"
            token = "test-token&plus+quote'"
            command = [shell, "-NoProfile", "-File", str(root / "scripts" / "New-OfficeManifest.ps1"),
                       "-GatewayUrl", "http://127.0.0.1:8787", "-Token", token, "-OutFile", str(output)]
            subprocess.run(command, capture_output=True, check=True, timeout=20)
            original = output.read_bytes()
            doc = ET.fromstring(original)
            ns = {"o": "http://schemas.microsoft.com/office/appforoffice/1.1"}
            self.assertEqual({h.attrib["Name"] for h in doc.findall("o:Hosts/o:Host", ns)},
                             {"Workbook", "Document", "Presentation"})
            url = doc.find("o:DefaultSettings/o:SourceLocation", ns).attrib["DefaultValue"]
            parsed = urllib.parse.urlsplit(url)
            self.assertEqual(parsed.hostname, "pivot.claude.ai")
            query = urllib.parse.parse_qs(parsed.query)
            self.assertEqual(query["gateway_token"], [token])
            self.assertEqual(query["gateway_url"], ["http://127.0.0.1:8787"])
            ids = {n.attrib["id"] for n in doc.iter() if "id" in n.attrib}
            for node in doc.iter():
                if "resid" in node.attrib:
                    self.assertIn(node.attrib["resid"], ids)
            self.assertNotIn(b"__GATEWAY", original)
            subprocess.run(command, capture_output=True, check=True, timeout=20)
            backups = list(Path(folder).glob("manifest.xml.bak-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)
