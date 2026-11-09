"""Collect bundled libraries' notices without copying the developer environment."""

from importlib.metadata import distribution
from pathlib import Path
import shutil
import sys


DEPENDENCIES = ("PySide6-Essentials", "shiboken6", "paramiko", "bcrypt", "cryptography",
                "invoke", "PyNaCl", "cffi", "pycparser", "PyInstaller")


def main(bundle):
    source = Path(__file__).resolve().parent
    notices = bundle / "licenses"
    notices.mkdir(exist_ok=True)
    for path in (source / "licenses").glob("*.txt"):
        shutil.copy2(path, notices / path.name)
    shutil.copy2(source / "THIRD-PARTY.txt", notices / "THIRD-PARTY.txt")
    for name in DEPENDENCIES:
        package = distribution(name)
        target = notices / name
        for entry in package.files or ():
            if entry.name.lower().startswith(("license", "copying", "notice", "copyright")):
                original = package.locate_file(entry)
                if original.is_file() and any(part.endswith(".dist-info") for part in entry.parts):
                    target.mkdir(exist_ok=True)
                    shutil.copy2(original, target / entry.name)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, notices / "Python-LICENSE.txt")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
