"""PyInstaller entry point for the macOS build.

Mirrors src/kaori.in's frozen-executable behaviour, but as a committed script
PyInstaller can analyse. It:

  1. points PATH/JAVA_HOME at the bundled Temurin JRE (so Suwayomi starts
     without a system Java),
  2. points GSETTINGS_SCHEMA_DIR at the bundled schema,
  3. registers the bundled gresource,
  4. hands off to kaori.main.

Data layout inside the frozen bundle (PyInstaller 6 onedir):

  Contents/Resources/Kaori            <- the executable
  Contents/Resources/_internal/       <- sys._MEIPASS
    share/kaori/kaori.gresource
    share/kaori/version
    share/glib-2.0/schemas/
    share/locale/<lang>/LC_MESSAGES/kaori.mo
    jre/bin/java
    Suwayomi-Server.jar               <- matches main.py's JAR path derivation
"""

import gettext
import locale
import os
import signal
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio

APPLICATION_ID = "io.github.riyaniii.Kaori"


def frozen_base() -> str:
    # PyInstaller 6.x puts bundled data under _internal/ next to the exe.
    return os.path.join(os.path.dirname(sys.executable), "_internal")


def read_version(pkgdatadir: str) -> str:
    try:
        with open(os.path.join(pkgdatadir, "version"), encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def main() -> int:
    base = frozen_base()
    pkgdatadir = os.path.join(base, "share", "kaori")
    localedir = os.path.join(base, "share", "locale")
    schemadir = os.path.join(base, "share", "glib-2.0", "schemas")
    jre_bin = os.path.join(base, "jre", "bin")

    # Prefer the bundled JRE so the Suwayomi server can be started out of the box.
    if os.path.isdir(jre_bin):
        os.environ["JAVA_HOME"] = os.path.dirname(jre_bin)
        os.environ["PATH"] = jre_bin + os.pathsep + os.environ.get("PATH", "")

    if os.path.isdir(schemadir):
        os.environ["GSETTINGS_SCHEMA_DIR"] = schemadir

    sys.path.insert(1, pkgdatadir)
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    try:
        locale.bindtextdomain("kaori", localedir)
        locale.textdomain("kaori")
    except AttributeError:
        print("Cannot set locale.")

    try:
        gettext.bindtextdomain("kaori", localedir)
        gettext.textdomain("kaori")
    except AttributeError:
        print("Cannot load translations.")

    resource = Gio.Resource.load(os.path.join(pkgdatadir, "kaori.gresource"))
    resource._register()

    from kaori import main as kaori_main

    return kaori_main.main(read_version(pkgdatadir))


if __name__ == "__main__":
    sys.exit(main())
