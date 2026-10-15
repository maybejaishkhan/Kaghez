import asyncio
from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from .row import DownloadRow


@Gtk.Template(resource_path='/io/github/riyaniii/Kaori/download/page.ui')
class DownloadsPage(Adw.NavigationPage):
    __gtype_name__ = "KaoriDownloadsPage"

    downloads = GObject.Property(type=Gio.ListStore)
    downloader_state = GObject.Property(type=str)

    def __init__(self):
        super().__init__()
        self.suwayomi = Gio.Application.get_default().suwayomi
        self.poll_id = 0
        self.refreshing = False

        self.downloads = self.suwayomi.download_queue
        self.suwayomi.bind_property(
            "downloader-state", self, "downloader-state", GObject.BindingFlags.SYNC_CREATE,
        )
        asyncio.create_task(self.refresh())
        self.poll_id = GLib.timeout_add_seconds(1, self.on_poll)


    def on_poll(self):
        asyncio.create_task(self.refresh())
        return GLib.SOURCE_CONTINUE

    async def refresh(self):
        if self.refreshing:
            return
        self.refreshing = True
        try:
            await self.suwayomi.getDownloadStatus()
        finally:
            self.refreshing = False

    @Gtk.Template.Callback()
    def get_page(self, obj, count):
        return "downloads" if count > 0 else "greeter"

    @Gtk.Template.Callback()
    def get_toggle_icon(self, obj, state):
        return "media-playback-pause-symbolic" if state == "STARTED" else "media-playback-start-symbolic"

    @Gtk.Template.Callback()
    def get_toggle_tooltip(self, obj, state):
        return "Pause downloads" if state == "STARTED" else "Start downloads"

    @Gtk.Template.Callback()
    def on_setup(self, factory, list_item):
        list_item.set_selectable(False)
        list_item.set_activatable(False)
        row = DownloadRow()
        row.connect("reorder-requested", self.on_reorder_requested)
        row.connect("remove-requested", self.on_remove_requested)
        row.connect("retry-requested", self.on_retry_requested)
        list_item.set_child(row)

    @Gtk.Template.Callback()
    def on_bind(self, factory, list_item):
        list_item.get_child().model = list_item.get_item()

    @Gtk.Template.Callback()
    def on_unbind(self, factory, list_item):
        list_item.get_child().model = None

    @Gtk.Template.Callback()
    def on_header_setup(self, factory, header):
        header.set_child(Gtk.Label(
            xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["heading"],
            margin_top=12, margin_bottom=6, margin_start=12, margin_end=12,
        ))
        header.connect("notify::item", self.update_header)

    @Gtk.Template.Callback()
    def on_header_bind(self, factory, header):
        self.update_header(header)

    def update_header(self, header, *_):
        item = header.get_item()
        header.get_child().set_label(item.manga_title if item else "")

    def on_reorder_requested(self, row, dragged_chapter_id, target_chapter_id):
        for index, download in enumerate(self.suwayomi.download_queue):
            if download.chapter_id == target_chapter_id:
                asyncio.create_task(
                    self.suwayomi.reorderChapterDownload(dragged_chapter_id, index)
                )
                return

    def on_remove_requested(self, row, chapter_id):
        asyncio.create_task(self.suwayomi.dequeueChapterDownload(chapter_id))

    def on_retry_requested(self, row, chapter_id):
        asyncio.create_task(self.retry(chapter_id))

    async def retry(self, chapter_id):
        await self.suwayomi.dequeueChapterDownload(chapter_id)
        await self.suwayomi.enqueueChapterDownload(chapter_id)

    @Gtk.Template.Callback()
    def on_toggle_clicked(self, *_):
        if self.downloader_state == "STARTED":
            asyncio.create_task(self.suwayomi.stopDownloader())
        else:
            asyncio.create_task(self.suwayomi.startDownloader())

    @Gtk.Template.Callback()
    def on_clear_clicked(self, *_):
        dialog = Adw.AlertDialog(
            heading="Clear download queue?",
            body="Every queued chapter will be removed. Chapters already downloaded stay on the server.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clear", "Clear")
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self.on_clear_response)
        dialog.present(self)

    def on_clear_response(self, dialog, response):
        if response == "clear":
            asyncio.create_task(self.suwayomi.clearDownloader())
