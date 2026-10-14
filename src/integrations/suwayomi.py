import gi
gi.require_version('Gdk', '4.0')
gi.require_version('Gly', '2')
gi.require_version('GlyGtk4', '2')
from gi.repository import Gdk, Gio, GLib, GObject, Gly, GlyGtk4

import asyncio
import enum
import json
import os
from pathlib import Path
from urllib.parse import urljoin

import httpx
from babel import Locale
from diskcache import Cache
from gql import Client
from gql.transport.aiohttp import AIOHTTPTransport

from . import models
from .queries import *

MODEL_CLASSES = {
    'Manga': models.Manga,
    'Chapter': models.Chapter,
    'Extension': models.Extension,
    'Source': models.Source,
    'Download': models.Download,
}

FETCH_CONNECTIONS = 8
DECODE_WORKERS = max(2, min(4, os.cpu_count() or 2))
IMAGE_TIMEOUT = 10
IMAGE_RETRIES = 1
CACHE_SIZE_LIMIT = 500 * 1024 * 1024
GRAPHQL_TIMEOUT = 20
POLL_INTERVAL = 10
LOCAL_SOURCE_ID = "0"

READING_MODES = {
    0: ("single", "vertical"),
    1: ("double", "vertical"),
    2: ("webtoon", "vertical"),
    3: ("webtoon", "horizontal"),
    4: ("webtoon", "vertical"),
}
CHAPTER_VARS = {"is_read": "isRead", "is_bookmarked": "isBookmarked", "last_page_read": "lastPageRead"}
FILTER_DEFAULTS = ("checkBoxDefault", "selectDefault", "triStateDefault", "textDefault", "sortDefault")


class SourceMangaType(enum.StrEnum):
    SEARCH = "SEARCH"
    POPULAR = "POPULAR"
    LATEST = "LATEST"


def readingModeValue(mode: str, orientation: str) -> int:
    return {"single": 0, "double": 1}.get(mode, 3 if orientation == "horizontal" else 4)


def makeClient(url: str) -> Client:
    transport = AIOHTTPTransport(timeout=GRAPHQL_TIMEOUT, url=url.rstrip('/') + '/api/graphql')
    return Client(transport=transport, fetch_schema_from_transport=False)


def watch(widget, obj: GObject.Object, callback, signal: str = "items-changed"):
    """Call callback() whenever `obj` emits `signal`, until `widget` is unrealized."""
    handler = obj.connect(signal, lambda *_: callback())
    widget.connect("unrealize", lambda *_: obj.disconnect(handler))


class Suwayomi(GObject.Object):
    __gtype_name__ = 'KaoriIntegrationSuwayomi'

    MODE_KEY = "webUI_readingMode"
    DIRECTION_KEY = "webUI_readingDirection"
    LANGUAGES_KEY = "webUI_browseLanguages"

    url = GObject.Property(type=str, default="http://localhost:4567")
    downloader_state = GObject.Property(type=str, default="STOPPED")
    download_queue = GObject.Property(type=Gio.ListStore)
    library = GObject.Property(type=Gio.ListStore)

    def __init__(self):
        super().__init__()
        self.loaded_models = {}
        self.pending = {}
        self.closing = set()
        self.session = None
        self.poller = None
        self.connect_lock = asyncio.Lock()
        self.client = makeClient(self.url)

        self.http = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(
                retries=IMAGE_RETRIES,
                limits=httpx.Limits(max_connections=FETCH_CONNECTIONS),
            ),
            timeout=httpx.Timeout(IMAGE_TIMEOUT, pool=None),
            follow_redirects=True,
        )
        self.decode_slots = asyncio.Semaphore(DECODE_WORKERS)
        self.cache = Cache(
            str(Path(GLib.get_user_cache_dir()) / "kaori" / "images"),
            size_limit=CACHE_SIZE_LIMIT,
            eviction_policy="least-recently-used",
        )

        self.download_queue = Gio.ListStore(item_type=models.Download)
        self.library = Gio.ListStore(item_type=models.Manga)
        self.sources = Gio.ListStore(item_type=models.Source)
        self.all_sources = Gio.ListStore(item_type=models.Source)
        self.extensions = Gio.ListStore(item_type=models.Extension)

        self.languages = ["en", "all"]

    def swapClient(self, url: str) -> Client | None:
        """Install a fresh client; returns the old one if it needs closing."""
        self.stopPolling()
        old, connected = self.client, self.session is not None
        self.session, self.url, self.client = None, url, makeClient(url)
        return old if connected else None

    def reconnect(self, url: str):
        if old := self.swapClient(url):
            task = asyncio.create_task(old.close_async())
            self.closing.add(task)
            task.add_done_callback(self.closing.discard)

    async def disconnect(self):
        if old := self.swapClient(self.url):
            await old.close_async()

    async def close(self):
        self.stopPolling()
        if self.session is not None:
            await self.client.close_async()
        await asyncio.gather(*self.closing)
        await self.http.aclose()
        self.cache.close()

    def startPolling(self):
        """Keep the stores in sync with changes made outside the app."""
        if self.poller is None:
            self.poller = asyncio.create_task(self.poll())

    def stopPolling(self):
        if self.poller:
            self.poller.cancel()
            self.poller = None

    async def poll(self):
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            await asyncio.gather(
                self.refreshLibrary(), self.refreshSources(), self.getDownloadStatus(),
                return_exceptions=True,
            )

    async def getSession(self):
        async with self.connect_lock:
            if self.session is None:
                self.session = await self.client.connect_async()
        return self.session

    async def query(self, gql_query, variable_values=None) -> dict:
        session = await self.getSession()
        return await session.execute(gql_query, variable_values=variable_values)

    async def shared(self, key, factory):
        if key not in self.pending:
            task = asyncio.create_task(factory())
            task.add_done_callback(lambda _: self.pending.pop(key, None))
            self.pending[key] = task
        return await asyncio.shield(self.pending[key])

    @staticmethod
    def replace(store: Gio.ListStore, items: list):
        if list(store) != items:
            store.splice(0, store.get_n_items(), items)

    async def loadBytes(self, url: str) -> bytes:
        if (data := await asyncio.to_thread(self.cache.get, url)) is None:
            data = (await self.http.get(url)).content
            await asyncio.to_thread(self.cache.set, url, data)
        return data

    async def getPaintable(self, url: str) -> Gdk.Paintable:
        raw_bytes = await self.shared(('bytes', url), lambda: self.loadBytes(url))
        async with self.decode_slots:
            return await asyncio.to_thread(self.makeTexture, raw_bytes)

    async def loadIcon(self, item):
        if item.icon_url:
            item.paintable = await self.getPaintable(item.icon_url)

    def makeTexture(self, raw_bytes: bytes) -> Gdk.Texture:
        gbytes = GLib.Bytes.new(raw_bytes)
        try:
            return Gdk.Texture.new_from_bytes(gbytes)
        except GLib.Error:
            frame = Gly.Loader.new_for_bytes(gbytes).load().next_frame()
            return GlyGtk4.frame_get_texture(frame)

    def getModel(self, model_id: int | str, item_type: str) -> GObject.Object | None:
        return self.loaded_models.get((item_type, model_id))

    def modelFor(self, item_type: str, item_id: int | str) -> GObject.Object:
        key = (item_type, item_id)
        if key not in self.loaded_models:
            self.loaded_models[key] = MODEL_CLASSES[item_type]()
        return self.loaded_models[key]

    def makeModel(self, item: dict | None, item_type: str) -> GObject.Object | None:
        if item is None:
            return None
        model = self.modelFor(item_type, item['pkgName'] if item_type == 'Extension' else item['id'])
        getattr(self, f"fill{item_type}")(model, item)
        return model

    def makeModels(self, nodes: list, item_type: str) -> list:
        return [self.makeModel(node, item_type) for node in nodes]

    def makeChapterStore(self, chapter_models: list) -> Gio.ListStore:
        store = Gio.ListStore(item_type=models.Chapter)
        store.splice(0, 0, chapter_models)
        return store

    def absoluteUrl(self, path: str | None) -> str:
        return urljoin(self.url, path) if path else ""

    def fillManga(self, model, item: dict):
        model.update_data(
            id=item.get('id'),
            title=item.get('title'),
            author=item.get('author'),
            artist=item.get('artist'),
            description=item.get('description'),
            genre=item.get('genre', []),
            status=item.get('status'),
            initialized=item.get('initialized', False),
            in_library=item.get('inLibrary', False),
            unread_count=item.get('unreadCount', 0),
            first_unread_chapter=self.makeModel(item.get('firstUnreadChapter'), 'Chapter'),
            last_read_chapter=self.makeModel(item.get('lastReadChapter'), 'Chapter'),
            real_url=item.get('realUrl'),
            thumbnail_url=self.absoluteUrl(item.get('thumbnailUrl')),
        )
        if chapters := item.get('chapters'):
            if 'totalCount' in chapters:
                model.total_chapters = chapters['totalCount']
            if 'nodes' in chapters:
                self.replace(model.chapters, self.makeModels(chapters['nodes'], 'Chapter'))

    def fillChapter(self, model, item: dict):
        model.update_data(id=item['id'], **{name: item[key] for key, name in CHAPTER_FIELDS.items() if key in item})

    def fillExtension(self, model, item: dict):
        values = {name: item[key] for key, name in EXTENSION_FIELDS.items() if key in item}
        if 'iconUrl' in item:
            values['icon_url'] = self.absoluteUrl(item['iconUrl'])
        model.update_data(pkg_name=item['pkgName'], **values)

    def fillSource(self, model, item: dict):
        extension = item.get('extension') or {}
        model.update_data(
            id=item['id'],
            name=item['name'],
            lang=item.get('lang', ''),
            icon_url=self.absoluteUrl(item.get('iconUrl')),
            is_configurable=item.get('isConfigurable', False),
            is_nsfw=item.get('isNsfw', False),
            extension_pkg_name=extension.get('pkgName', ''),
            extension_name=extension.get('name', ''),
        )

    async def getManga(self, manga_id: int) -> models.Manga:
        model = self.getModel(manga_id, 'Manga')
        if model and model.initialized:
            return model
        return await self.shared(('manga', manga_id), lambda: self.fetchManga(manga_id))

    async def fetchManga(self, manga_id: int) -> models.Manga:
        result = await self.query(
            FETCH_MANGA,
            {"id": manga_id, "fetchManga": True, "fetchChapters": True},
        )
        data = result['fetchMangaAndChapters']
        chapters = data['chapters']
        node = {**data['manga'], 'chapters': {'nodes': chapters, 'totalCount': len(chapters)}}
        return self.makeModel(node, 'Manga')

    async def getChapters(self, manga_id: int, first: int | None = None, after: str | None = None) -> list:
        result = await self.query(GET_CHAPTERS_MANGA, {"mangaId": manga_id, "first": first, "after": after})
        connection = result['chapters']
        chapter_models = self.makeModels(connection['nodes'], 'Chapter')
        if model := self.getModel(manga_id, 'Manga'):
            self.replace(model.chapters, chapter_models)
            model.total_chapters = connection['totalCount']
        return chapter_models

    async def refreshLibrary(self):
        await self.shared('library', self.loadLibrary)

    async def loadLibrary(self):
        result = await self.query(GET_LIBRARY)
        manga_models = self.makeModels(result['mangas']['nodes'], 'Manga')
        for model in set(self.library) - set(manga_models):
            model.in_library = False  # removed from the library elsewhere
        self.replace(self.library, manga_models)

    def syncLibraryMembership(self, model: models.Manga, in_library: bool):
        found, position = self.library.find(model)
        if in_library and not found:
            self.library.append(model)
        elif not in_library and found:
            self.library.remove(position)

    async def refreshExtensions(self):
        await self.shared('extensions', self.loadExtensions)

    async def loadExtensions(self):
        result = await self.query(FETCH_EXTENSIONS)
        self.replace(self.extensions, self.makeModels(result['fetchExtensions']['extensions'], 'Extension'))

    async def refreshSources(self):
        await self.shared('sources', self.loadSources)

    async def loadSources(self):
        result, _ = await asyncio.gather(self.query(GET_SOURCES), self.getBrowseLanguages())
        self.replace(self.all_sources, self.makeModels(result['sources']['nodes'], 'Source'))
        self.applyLanguages()

    def applyLanguages(self):
        self.replace(self.sources, [
            source for source in self.all_sources
            if source.lang in self.languages or source.id == LOCAL_SOURCE_ID
        ])

    async def getBrowseLanguages(self):
        languages = self.readMeta(await self.getGlobalMetaValues(), self.LANGUAGES_KEY)
        if languages is not None:
            self.languages = languages

    async def setBrowseLanguages(self, languages: list):
        self.languages = languages
        self.applyLanguages()
        await self.setGlobalMeta(self.LANGUAGES_KEY, languages)

    async def getSourceManga(
        self,
        source_id: str,
        fetch_type: SourceMangaType = SourceMangaType.POPULAR,
        page: int = 1,
        query: str | None = None,
        filters: list | None = None,
    ) -> list:
        result = await self.query(GET_SOURCE_MANGA, {
            "source": source_id,
            "type": fetch_type,
            "page": page,
            "query": query,
            "filters": filters or None,
        })
        return self.makeModels(result['fetchSourceManga']['mangas'], 'Manga')

    def makeFilter(self, node: dict) -> dict:
        return {
            "type": node.get("type"),
            "name": node.get("name") or "",
            "default": next((node[key] for key in FILTER_DEFAULTS if key in node), None),
            "values": node.get("values") or [],
            "filters": [self.makeFilter(child) for child in node.get("filters") or []],
        }

    async def getSourceFilters(self, source_id: str) -> list:
        result = await self.query(GET_SOURCE_FILTERS, {"id": source_id})
        return [self.makeFilter(node) for node in result['source']['filters']]

    async def changeExtension(self, pkg_name: str, **patch):
        model = self.getModel(pkg_name, 'Extension')
        model.is_busy = True
        result = await self.query(UPDATE_EXTENSION, {"pkgName": pkg_name, **patch})
        self.makeModel(result['updateExtension']['extension'], 'Extension')
        await self.refreshSources()  # installing or removing an extension adds or removes sources
        model.is_busy = False

    async def installExtension(self, pkg_name: str):
        await self.changeExtension(pkg_name, install=True)

    async def updateExtension(self, pkg_name: str):
        await self.changeExtension(pkg_name, update=True)

    async def uninstallExtension(self, pkg_name: str):
        await self.changeExtension(pkg_name, uninstall=True)

    def makePageModel(self, chapter_id: int, index: int, url: str) -> models.Page:
        return models.Page(index=index, chapter_id=chapter_id, url=self.absoluteUrl(url))

    async def getChapterPages(self, chapter_id: int) -> list:
        result = await self.query(FETCH_CHAPTER_PAGES, {"chapterId": chapter_id})
        data = result['fetchChapterPages']
        chapter, manga = data['chapter'], data['chapter']['manga']
        self.getModel(chapter_id, 'Chapter').update_data(
            page_count=chapter['pageCount'],
            is_downloaded=chapter['isDownloaded'],
        )
        self.getModel(manga['id'], 'Manga').download_count = manga['downloadCount']
        return [self.makePageModel(chapter_id, index, url) for index, url in enumerate(data['pages'])]

    async def updateChapter(self, chapter_id: int, **changes):
        """changes: any of is_read, is_bookmarked, last_page_read."""
        self.getModel(chapter_id, 'Chapter').update_data(**changes)

        variables = {"id": chapter_id, **{CHAPTER_VARS[name]: value for name, value in changes.items()}}
        node = (await self.query(UPDATE_CHAPTER, variables))['updateChapter']['chapter']
        self.makeModel(node, 'Chapter')
        self.getModel(node['manga']['id'], 'Manga').unread_count = node['manga']['unreadCount']

    async def deleteDownloadedChapter(self, chapter_id: int):
        self.getModel(chapter_id, 'Chapter').is_downloaded = False

        result = await self.query(DELETE_DOWNLOADED_CHAPTER, {"id": chapter_id})
        node = result['deleteDownloadedChapter']['chapters']
        self.makeModel(node, 'Chapter')
        self.getModel(node['manga']['id'], 'Manga').download_count = node['manga']['downloadCount']

    async def setMangaLibrary(self, manga_id: int, in_library: bool):
        model = self.getModel(manga_id, 'Manga')
        model.in_library = in_library
        self.syncLibraryMembership(model, in_library)

        result = await self.query(SET_MANGA_LIBRARY, {"id": manga_id, "inLibrary": in_library})
        node = result['updateManga']['manga']
        model.update_data(in_library_at=node['inLibraryAt'], unread_count=node['unreadCount'])

    async def addToLibrary(self, manga_id: int):
        await self.setMangaLibrary(manga_id, True)

    async def removeFromLibrary(self, manga_id: int):
        await self.setMangaLibrary(manga_id, False)

    def makeDownloadModel(self, item: dict) -> models.Download:
        chapter, manga = item['chapter'], item['manga']
        model = self.modelFor('Download', chapter['id'])
        model.update_data(
            chapter_id=chapter['id'],
            chapter_name=chapter['name'],
            manga_id=manga['id'],
            manga_title=manga['title'],
            progress=item['progress'],
            state=item['state'],
            tries=item['tries'],
        )
        return model

    async def getDownloadStatus(self):
        status = (await self.query(GET_DOWNLOAD_STATUS))['downloadStatus']
        self.downloader_state = status['state']
        queue = [self.makeDownloadModel(item) for item in status['queue']]
        done = {download.manga_id for download in set(self.download_queue) - set(queue)}
        self.replace(self.download_queue, queue)
        await asyncio.gather(*(self.getChapters(manga_id) for manga_id in done))  # refresh is_downloaded

    async def downloadCommand(self, gql_query, variables=None):
        await self.query(gql_query, variables)
        await self.getDownloadStatus()

    async def enqueueChapterDownload(self, chapter_id: int):
        await self.downloadCommand(ENQUEUE_CHAPTER_DOWNLOAD, {"id": chapter_id})

    async def dequeueChapterDownload(self, chapter_id: int):
        await self.downloadCommand(DEQUEUE_CHAPTER_DOWNLOAD, {"id": chapter_id})

    async def reorderChapterDownload(self, chapter_id: int, to: int):
        await self.downloadCommand(REORDER_CHAPTER_DOWNLOAD, {"chapterId": chapter_id, "to": to})

    async def clearDownloader(self):
        await self.downloadCommand(CLEAR_DOWNLOADER)

    async def startDownloader(self):
        await self.downloadCommand(START_DOWNLOADER)

    async def stopDownloader(self):
        await self.downloadCommand(STOP_DOWNLOADER)

    @staticmethod
    def metaValues(nodes: list) -> dict:
        return {meta['key']: meta['value'] for meta in nodes}

    def readMeta(self, values: dict, key: str):
        value = values.get(key)
        return json.loads(value) if value else None

    def parseReaderSettings(self, values: dict) -> dict:
        settings = {}
        if (mode := self.readMeta(values, self.MODE_KEY)) is not None:
            settings["mode"], settings["orientation"] = READING_MODES[mode]
        if (direction := self.readMeta(values, self.DIRECTION_KEY)) is not None:
            settings["direction"] = "rtl" if direction == 1 else "ltr"
        return settings

    async def getGlobalMetaValues(self) -> dict:
        result = await self.query(GET_GLOBAL_READER_META)
        return self.metaValues(result['metas']['nodes'])

    async def getReaderSettings(self, manga_id: int) -> dict:
        result = await self.query(GET_READER_META, {"id": manga_id})
        values = self.metaValues(result['metas']['nodes'])
        values.update(self.metaValues(result['manga']['meta']))
        return self.parseReaderSettings(values)

    async def setReaderSettings(self, manga_id: int, settings: dict):
        await asyncio.gather(
            self.setMeta(manga_id, self.MODE_KEY, readingModeValue(settings["mode"], settings["orientation"])),
            self.setMeta(manga_id, self.DIRECTION_KEY, 1 if settings["direction"] == "rtl" else 0),
        )

    async def setMeta(self, manga_id: int, key: str, value):
        await self.query(SET_MANGA_META, {"mangaId": manga_id, "key": key, "value": json.dumps(value)})

    async def getGlobalReaderSettings(self) -> dict:
        return self.parseReaderSettings(await self.getGlobalMetaValues())

    async def setGlobalReadingMode(self, mode: str, orientation: str):
        await self.setGlobalMeta(self.MODE_KEY, readingModeValue(mode, orientation))

    async def setGlobalReadingDirection(self, direction: str):
        await self.setGlobalMeta(self.DIRECTION_KEY, 1 if direction == "rtl" else 0)

    async def setGlobalMeta(self, key: str, value):
        await self.query(SET_GLOBAL_META, {"key": key, "value": json.dumps(value)})

    async def getServerSettings(self) -> dict:
        return (await self.query(GET_SERVER_SETTINGS))['settings']

    async def setServerSettings(self, **settings):
        await self.query(SET_SERVER_SETTINGS, {"settings": settings})

    async def setDownloadAsCbz(self, value: bool):
        await self.setServerSettings(downloadAsCbz=value)

    async def setDownloadsPath(self, path: str):
        await self.setServerSettings(downloadsPath=path)

    async def getExtensionStores(self) -> list:
        return (await self.getServerSettings())['extensionRepos']

    async def setExtensionStores(self, urls: list):
        await self.setServerSettings(extensionRepos=urls)
        await self.refreshExtensions()

    def languageTitle(self, lang: str) -> str:
        if lang == "all":
            return "All languages"
        if not lang or lang == "localsourcelang":
            return "Other"
        try:
            return Locale.parse(lang, sep="-").get_display_name("en")
        except Exception:
            return lang.upper()
