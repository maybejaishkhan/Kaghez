import asyncio
import random
from gi.repository import Adw, Gio, GObject, Gtk

from ..manga import MangaOverview
from ..containers import MangaRow

from ...integrations.suwayomi import LOCAL_SOURCE_ID, watch

MAX_ROWS = 5
ROW_SIZE = 8
CANDIDATES = 15
MAX_CONCURRENT = 3
CAROUSEL_SIZE = 6


@Gtk.Template(resource_path='/com/rini/kaori/pages/home.ui')
class HomePage(Adw.NavigationPage):
    __gtype_name__ = "KaoriHomePage"

    manga_carousel = Gtk.Template.Child()
    carousel_stack = Gtk.Template.Child()
    main_box = Gtk.Template.Child()

    state = GObject.Property(type=str, default="home")

    def __init__(self):
        super().__init__()
        self.suwayomi = Gio.Application.get_default().suwayomi
        self.rows = []
        self.rows_task = None

        watch(self, self.suwayomi.library, self.on_library_changed)
        watch(self, self.suwayomi.sources, self.reload_rows)

        self.sync_carousel()
        asyncio.create_task(self.load())

    async def load(self):
        await asyncio.gather(self.suwayomi.refreshLibrary(), self.suwayomi.refreshSources())
        self.reload_rows()

    def update_state(self):
        self.state = "home" if self.rows or self.suwayomi.library.get_n_items() else "greeter"

    def on_library_changed(self):
        self.sync_carousel()
        self.update_state()

    def sync_carousel(self):
        while self.manga_carousel.get_n_pages():
            self.manga_carousel.remove(self.manga_carousel.get_nth_page(0))
        for manga in list(self.suwayomi.library)[:CAROUSEL_SIZE]:
            self.manga_carousel.append(MangaOverview(manga))
        self.carousel_stack.set_visible_child_name(
            "carousel" if self.manga_carousel.get_n_pages() else "empty"
        )

    def pick_sources(self) -> list:
        by_extension = {}
        for source in self.suwayomi.sources:
            if source.id != LOCAL_SOURCE_ID:
                by_extension.setdefault(source.extension_pkg_name, []).append(source)
        picked = [random.choice(group) for group in by_extension.values()]
        random.shuffle(picked)
        return picked[:CANDIDATES]

    def reload_rows(self):
        if self.rows_task:
            self.rows_task.cancel()
        self.rows_task = asyncio.create_task(self.load_rows())

    async def load_rows(self):
        for row in self.rows:
            self.main_box.remove(row)
        self.rows = []
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async def add_row(source):
            async with semaphore:
                if len(self.rows) >= MAX_ROWS:
                    return
                mangas = await self.suwayomi.getSourceManga(source.id)
            if mangas and len(self.rows) < MAX_ROWS:
                row = MangaRow(source.name)
                row.set_items(mangas[:ROW_SIZE])
                self.main_box.append(row)
                self.rows.append(row)

        await asyncio.gather(*map(add_row, self.pick_sources()), return_exceptions=True)
        self.update_state()
