"""Check this project's installable layout and release metadata without contacting HACS."""

import json
import re
import struct
import tomllib
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    components = root / "custom_components"
    directories = sorted(path.name for path in components.iterdir() if path.is_dir() and path.name != "__pycache__")
    assert directories == ["alorair_lite"], "Distribution must contain exactly the alorair_lite integration"
    component = components / "alorair_lite"
    manifest = json.loads((component / "manifest.json").read_text())
    hacs = json.loads((root / "hacs.json").read_text())
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]

    assert project["license"] == "MIT", "Project metadata must identify its MIT license"
    assert (root / "LICENSE").read_bytes() == (component / "LICENSE").read_bytes(), (
        "Packaged license differs from the authoritative root LICENSE; run make sync-license"
    )
    assert (component / "NOTICE.md").is_file(), "Include the third-party artwork notice in the integration"
    for key in ("domain", "documentation", "issue_tracker", "codeowners", "name", "version"):
        assert manifest.get(key), f"Missing integration manifest field: {key}"
    assert manifest["domain"] == component.name, "Integration domain and directory disagree"
    assert manifest["version"] == project["version"], "Integration and project versions disagree"
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]), "Use a stable three-part release version"
    assert hacs["name"] == manifest["name"], "HACS and integration names disagree"
    assert re.fullmatch(r"\d+\.\d+\.\d+", hacs["homeassistant"]), "Declare a stable minimum HA version"
    tested = next(
        dep.removeprefix("homeassistant==") for dep in project["dependencies"] if dep.startswith("homeassistant==")
    )
    assert tuple(map(int, tested.split("."))) >= tuple(map(int, hacs["homeassistant"].split("."))), (
        "Tests must use at least the declared minimum Home Assistant version"
    )
    assert not hacs.get("content_in_root", False), "This project uses custom_components/<domain>"
    assert not hacs.get("zip_release", False), "The manual-install ZIP is not a HACS ZIP-release archive"
    for relative in ("__init__.py", "config_flow.py", "strings.json", "translations/en.json", "brand/icon.png"):
        assert (component / relative).is_file(), f"Missing installation file: {relative}"
    for path in component.rglob("*.json"):
        json.loads(path.read_text())
    icon = (component / "brand/icon.png").read_bytes()
    assert icon[:8] == b"\x89PNG\r\n\x1a\n", "Brand icon must be a PNG"
    width, height = struct.unpack(">II", icon[16:24])
    assert width == height and width > 0, "Brand icon must be square"
    print(f"Local distribution checks passed: {manifest['version']}, minimum HA {hacs['homeassistant']}")
    print("This checks local files only; public HACS installation requires separate validation.")


if __name__ == "__main__":
    main()
