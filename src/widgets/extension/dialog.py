from gi.repository import Adw, Gio, GObject, Gtk

from ...integrations import models


@Gtk.Template(resource_path='/io/github/riyaniii/Kaori/extension/dialog.ui')
class ExtensionDialog(Adw.Dialog):
    __gtype_name__ = "KaoriExtensionDialog"

    model = GObject.Property(type=models.Extension)

    def __init__(self, model):
        super().__init__()
        self.suwayomi = Gio.Application.get_default().suwayomi
        self.model = model

    @Gtk.Template.Callback()
    def get_language(self, obj, lang):
        return self.suwayomi.languageTitle(lang)

    @Gtk.Template.Callback()
    def get_age_rating(self, obj, is_nsfw):
        return "NSFW" if is_nsfw else "SAFE"

    @Gtk.Template.Callback()
    def get_status(self, obj, is_installed, has_update, is_obsolete):
        parts = ["Installed" if is_installed else "Not installed"]
        if has_update:
            parts.append("update available")
        if is_obsolete:
            parts.append("obsolete")
        return ", ".join(parts)
