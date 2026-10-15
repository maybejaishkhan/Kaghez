# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the macOS build. Run from the repo root:
#
#   KAORI_ROOT=$PWD pyinstaller --clean --noconfirm mac/kaori.spec
#
# The `kaori` package is exposed to PyInstaller via a symlink at
# build/pypkg/kaori -> src (created by the workflow), since the repo's package
# directory is `src/` but is imported as `kaori`.

import glob
import os

ROOT = os.environ["KAORI_ROOT"]
PKGPATH = os.path.join(ROOT, "build", "pypkg")

gresource = os.environ.get("KAORI_GRESOURCE") or os.path.join(
    ROOT, "build", "src", "kaori.gresource"
)
jar = glob.glob(os.path.join(ROOT, "src", "Suwayomi-Server*.jar"))[0]

a = Analysis(
    [os.path.join(ROOT, "mac", "entry.py")],
    pathex=[PKGPATH],
    binaries=[],
    datas=[
        (gresource, "share/kaori"),
        (os.path.join(ROOT, "build", "version"), "share/kaori"),
        (os.path.join(ROOT, "build", "schemas"), "share/glib-2.0/schemas"),
        (os.path.join(ROOT, "build", "locale"), "share/locale"),
        (jar, "."),
    ],
    hiddenimports=["gi"],
    hookspath=[],
    hooksconfig={
        "gi": {
            "icons": ["Adwaita"],
            "themes": ["Adwaita"],
            "module-versions": {
                "Gtk": "4.0",
                "Adw": "1",
            },
        }
    },
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Kaori",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Kaori",
)
