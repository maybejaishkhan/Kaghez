import asyncio
from gi.repository import Adw, Gio, Gtk

from .sources import SourcesPage  
from .extensions import ExtensionsPage  

from ...integrations.suwayomi import LOCAL_SOURCE_ID


@Gtk.Template(resource_path='/com/rini/kaori/pages/browse.ui')
class BrowsePage(Adw.NavigationPage):
    __gtype_name__ = "KaoriBrowsePage"

    search_bar = Gtk.Template.Child()
    sources_entry = Gtk.Template.Child()
    extensions_entry = Gtk.Template.Child()
    sources_page = Gtk.Template.Child()
    extensions_page = Gtk.Template.Child()

    def __init__(self):
        super().__init__()
        self.suwayomi = Gio.Application.get_default().suwayomi

    @Gtk.Template.Callback()
    def on_sources_search_changed(self, entry):
        self.sources_page.search_text = entry.get_text().strip()

    @Gtk.Template.Callback()
    def on_extensions_search_changed(self, entry):
        self.extensions_page.search_text = entry.get_text().strip()

    @Gtk.Template.Callback()
    def on_search_mode_changed(self, search_bar, *_):
        if not search_bar.get_search_mode():
            self.sources_entry.set_text("")
            self.extensions_entry.set_text("")

    @Gtk.Template.Callback()
    def on_languages_clicked(self, *_):
        langs = {s.lang for s in self.suwayomi.all_sources if s.id != LOCAL_SOURCE_ID}

        group = Adw.PreferencesGroup(description="Only sources in these languages are used")
        for lang in sorted(langs, key=lambda l: (l != "all", l)):
            row = Adw.SwitchRow(
                title=self.suwayomi.languageTitle(lang),
                active=lang in self.suwayomi.languages,
            )
            row.connect("notify::active", self.on_language_toggled, lang)
            group.add(row)

        page = Adw.PreferencesPage()
        page.add(group)
        dialog = Adw.PreferencesDialog(title="Languages")
        dialog.add(page)
        dialog.present(self)

    def on_language_toggled(self, row, pspec, lang):
        languages = [l for l in self.suwayomi.languages if l != lang]
        if row.get_active():
            languages.append(lang)
        asyncio.create_task(self.suwayomi.setBrowseLanguages(languages))
