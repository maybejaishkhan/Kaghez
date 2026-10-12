from gi.repository import Gtk, Adw, Gio, GObject

from ...integrations import models

@Gtk.Template(resource_path='/com/rini/kaghez/manga/card.ui')
class MangaCard(Gtk.Box):
    __gtype_name__ = "KaghezMangaCard"

    model = GObject.Property(type=models.Manga)
    show_title = GObject.Property(type=bool, default=True)


    def __init__(self, model=None, **kwargs):
        super().__init__(**kwargs)
        if model is not None:
            self.model = model

