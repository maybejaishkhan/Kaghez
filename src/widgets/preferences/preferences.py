import asyncio
from gi.repository import Adw, Gio, GLib, GObject, Gtk

from .extension_stores import ExtensionStoresDialog

MODES = (
    ("webtoon", "Webtoon", "view-continuous-symbolic"),
    ("single", "Single Page", "view-paged-symbolic"),
    ("double", "Double Page", "view-dual-symbolic"),
)


@Gtk.Template(resource_path='/com/rini/kaghez/preferences/preferences.ui')
class KaghezPreferences(Adw.PreferencesDialog):
    __gtype_name__ = "KaghezPreferencesDialog"

    cbz_row = Gtk.Template.Child()
    location_row = Gtk.Template.Child()
    orientation_row = Gtk.Template.Child()
    direction_row = Gtk.Template.Child()

    loaded = GObject.Property(type=bool, default=False)
    mode_stack = GObject.Property(type=Gtk.Widget)

    def __init__(self):
        super().__init__()
        self.suwayomi = Gio.Application.get_default().suwayomi

        self.mode_stack = Adw.ViewStack()
        for name, title, icon in MODES:
            self.mode_stack.add_titled_with_icon(Adw.Bin(), name, title, icon)
        self.mode_stack.connect("notify::visible-child-name", self.on_reading_mode_changed)

        asyncio.create_task(self.load())

    async def load(self):
        settings, reader = await asyncio.gather(
            self.suwayomi.getServerSettings(),
            self.suwayomi.getGlobalReaderSettings(),
        )
        self.cbz_row.set_active(settings["downloadAsCbz"])
        path = settings["downloadsPath"]
        self.location_row.set_subtitle(
            Gio.File.new_for_path(path).get_basename() if path
            else "Choose where downloaded chapters are stored"
        )

        self.mode_stack.set_visible_child_name(reader.get("mode", "webtoon"))
        self.orientation_row.set_selected(int(reader.get("orientation") == "horizontal"))
        self.direction_row.set_selected(int(reader.get("direction") == "rtl"))
        self.loaded = True
        self.update_orientation_sensitivity()

    def update_orientation_sensitivity(self):
        self.orientation_row.set_sensitive(
            self.loaded and self.mode_stack.get_visible_child_name() == "webtoon"
        )

    def save_reading_mode(self):
        if self.loaded:
            orientation = "horizontal" if self.orientation_row.get_selected() == 1 else "vertical"
            asyncio.create_task(self.suwayomi.setGlobalReadingMode(
                self.mode_stack.get_visible_child_name(), orientation
            ))

    def on_reading_mode_changed(self, stack, pspec):
        self.update_orientation_sensitivity()
        self.save_reading_mode()

    @Gtk.Template.Callback()
    def on_orientation_changed(self, row, pspec):
        self.save_reading_mode()

    @Gtk.Template.Callback()
    def on_location_activated(self, row):
        Gtk.FileDialog(title="Select Download Location").select_folder(
            self.get_root(), None, self.on_location_selected
        )

    def on_location_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:  
            return
        self.location_row.set_subtitle(folder.get_basename())
        asyncio.create_task(self.suwayomi.setDownloadsPath(folder.get_path()))

    @Gtk.Template.Callback()
    def on_direction_changed(self, row, pspec):
        if self.loaded:
            asyncio.create_task(self.suwayomi.setGlobalReadingDirection(
                "rtl" if row.get_selected() == 1 else "ltr"
            ))

    @Gtk.Template.Callback()
    def on_cbz_toggled(self, row, pspec):
        if self.loaded:
            asyncio.create_task(self.suwayomi.setDownloadAsCbz(row.get_active()))

    @Gtk.Template.Callback()
    def on_stores_activated(self, row):
        ExtensionStoresDialog().present(self)

    @Gtk.Template.Callback()
    def on_open_browser_activated(self, row):
        Gtk.UriLauncher(uri=self.suwayomi.url).launch(self.get_root(), None, None)
