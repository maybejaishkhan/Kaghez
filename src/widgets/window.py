from gi.repository import Gtk, Adw, GLib, Gdk, Pango

import asyncio

from .. import actions
from .pages import *
from .download import DownloadsPage

from .chapter import ChapterPanel
from .manga import MangaPage
from .reader import ReaderPage

SWIPE_COMMIT_DISTANCE = 40.0
SWIPE_COMMIT_VELOCITY = 600.0

# Mouse side buttons (X11 "Button 4 / Button 5" in the user's words) arrive in
# GTK4/GDK as button numbers 8 (Back/previous) and 9 (Forward/next).
# GDK buttons 4-7 are scroll directions, so those must NOT be used here.
MOUSE_BACK_BUTTON = 8
MOUSE_FORWARD_BUTTON = 9

FORWARD_HISTORY_LIMIT = 50


def _forward_key(nav_view):
    """Stable identity key for a NavigationView's forward stack.

    Keyed on the view itself: ``get_navigation_stack()`` returns a new
    Gio.ListModel wrapper object on every call, so ``id()`` of it is
    useless as a dict key (it changes between popped/pushed/get-next-page
    for the same view).
    """
    return ("view", id(nav_view))


def forward_push(stacks, nav_view, page, limit=FORWARD_HISTORY_LIMIT):
    """Record a popped page on the view's forward stack (browser-style redo).

    Pure helper (no GTK calls) so the history semantics are headless-testable.
    Returns True if the page was recorded.
    """
    if page is None:
        return False
    key = _forward_key(nav_view)
    stack = stacks.setdefault(key, [])
    if stack and stack[-1] is page:
        return False
    stack.append(page)
    del stack[:max(len(stack) - limit, 0)]
    return True


def forward_peek(stacks, nav_view):
    """Return the top of the view's forward stack without removing it.

    Must be non-destructive: Adw may emit ``get-next-page`` several times for
    a single (possibly cancelled) forward gesture, so removal happens in the
    ``pushed`` handler instead.
    """
    stack = stacks.get(_forward_key(nav_view))
    if not stack:
        return None
    return stack[-1]


def on_history_pushed(stacks, nav_view, page):
    """Update forward history after a page was pushed onto a NavigationView.

    - Forward navigation completing: the pushed page is the top of the forward
      stack -> drop it from the stack (it is visible again).
    - Fresh navigation (opened a new manga/source/reader): any redo history
      is invalidated -> clear the stack (browser semantics).
    """
    key = _forward_key(nav_view)
    stack = stacks.get(key)
    if not stack:
        return
    if stack and stack[-1] is page:
        stack.pop()
    else:
        stack.clear()


def on_history_popped(stacks, nav_view, page):
    """Record a popped page so Forward can re-push it later."""
    forward_push(stacks, nav_view, page)


@Gtk.Template(resource_path='/com/rini/kaghez/window.ui')
class KaghezWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'KaghezWindow'

    main_stack = Gtk.Template.Child()
    overlay_split_view = Gtk.Template.Child()
    main_nav_view = Gtk.Template.Child()
    home_nav_view = Gtk.Template.Child()
    library_nav_view = Gtk.Template.Child()
    search_nav_view = Gtk.Template.Child()
    browse_nav_view = Gtk.Template.Child()
    downloads_nav_view = Gtk.Template.Child()
    toast_overlay = Gtk.Template.Child()

    header_bar = Gtk.Template.Child()
    chapter_panel = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Browser-style forward history: one redo stack per NavigationView.
        # Popped pages are kept alive here so Forward can re-push them
        # (dynamic pages are destroyed on pop otherwise). Keyed by view;
        # see _forward_key().
        self._forward_history = {}
        self._tracked_nav_views = []

        list(list(list(list(list(list(self.header_bar)[0])[0])[1])[0])[0])[0].set_ellipsize(Pango.EllipsizeMode.NONE)

        self.create_action(actions.show_manga, parameter_type="i")
        self.create_action(actions.show_reader, parameter_type="(ii)")
        self.create_action(actions.set_chapters, parameter_type="i")

        self.create_action(actions.toggle_chapter_panel, ['<control>s'], parameter_type=None)

        self.create_action(actions.show_source)
        self.create_action(actions.show_extension)
        self.create_action(actions.switch_page)
        self.create_action(actions.show_toast)

        self.create_action(actions.open_search_page, ['<control>f', '<control>k'], parameter_type=None)
        self.create_action(actions.open_downloads_page, ['<control>j'], parameter_type=None)
        self.create_action(actions.open_library_page, ['<control>l'], parameter_type=None)

        self.create_action(actions.extension_action)
        self.create_action(actions.toggle_library, parameter_type="i")

        self.create_action(actions.download_chapter, parameter_type="i")
        self.create_action(actions.delete_chapter_download, parameter_type="i")

        scroll_controller = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        scroll_controller.connect("scroll-begin", self.on_swipe_begin)
        scroll_controller.connect("scroll", self.on_swipe_scroll)
        scroll_controller.connect("scroll-end", self.on_swipe_scroll_end)
        self.overlay_split_view.add_controller(scroll_controller)
        self.swipe_accum = 0.0
        self.swipe_start_time = 0.0

        self._setup_side_button_navigation()

    def create_action(self, callback: callable, shortcuts: list | None = None, parameter_type: str = "s"):
        shortcuts = shortcuts or []
        def call_action(action, variant):
            app = self.get_application()
            if variant is None:
                result = callback(app)
            else:
                value = variant.unpack()
                result = callback(app, *value) if isinstance(value, tuple) else callback(app, value)
            if asyncio.iscoroutine(result):
                task = asyncio.create_task(result)
                task.add_done_callback(
                    lambda t: t.exception() and print(f"Action '{callback.__name__}' failed:", t.exception())
                )
        self.get_application().create_action(
            name=callback.__name__,
            callback=call_action,
            shortcuts=shortcuts,
            parameter_type=GLib.VariantType.new(parameter_type) if parameter_type else None
        )

    def on_swipe_begin(self, controller):
        self.swipe_accum = 0.0
        self.swipe_start_time = GLib.get_monotonic_time() / 1_000_000

    def on_swipe_scroll(self, controller, dx, dy):
        self.swipe_accum += dx

        if self.overlay_split_view.get_show_sidebar():
            return Gdk.EVENT_STOP

        return Gdk.EVENT_PROPAGATE

    def on_swipe_scroll_end(self, controller):
        now = GLib.get_monotonic_time() / 1_000_000
        elapsed = max(now - self.swipe_start_time, 1e-3)
        velocity = self.swipe_accum / elapsed  # px/s

        far_enough = abs(self.swipe_accum) > SWIPE_COMMIT_DISTANCE
        fast_enough = abs(velocity) > SWIPE_COMMIT_VELOCITY

        if far_enough or fast_enough:
            opening = self.swipe_accum > 0 if far_enough else velocity > 0

            current_page = self.main_stack.get_visible_child().get_visible_page()
            maybe_reader = self.main_nav_view.get_visible_page()

            if isinstance(current_page, MangaPage) and not isinstance(maybe_reader, ReaderPage):
                self.overlay_split_view.set_show_sidebar(opening)
            else:
                return

        self.swipe_accum = 0.0

    def _setup_side_button_navigation(self):
        # Forward history: enable the native Forward mouse button
        # (button 9) via get-next-page + per-view redo stacks fed by popped.
        # NOTE: we deliberately do NOT intercept the Back mouse button
        # (button 8) ourselves: Adw.NavigationView already pops natively,
        # and grabbing it here as well would double-pop (skip a screen).
        for nav_view in self._all_nav_views():
            self._track_nav_view(nav_view)

    def _all_nav_views(self):
        views = [self.main_nav_view]
        for name in (
            "home_nav_view",
            "library_nav_view",
            "search_nav_view",
            "browse_nav_view",
            "downloads_nav_view",
        ):
            try:
                view = getattr(self, name)
            except Exception:
                continue
            if view is not None and view not in views:
                views.append(view)
        for view in self._find_source_nav_views():
            if view not in views:
                views.append(view)
        return views

    def _find_source_nav_views(self):
        found = []

        def walk(widget):
            try:
                child = widget.get_first_child()
            except Exception:
                return
            while child is not None:
                if isinstance(child, Adw.NavigationView):
                    found.append(child)
                try:
                    walk(child)
                    child = child.get_next_sibling()
                except Exception:
                    break

        try:
            walk(self)
        except Exception:
            pass
        return found

    def _track_nav_view(self, nav_view):
        if nav_view is None or nav_view in self._tracked_nav_views:
            return
        self._tracked_nav_views.append(nav_view)
        nav_view.connect("popped", self._on_nav_view_popped)
        nav_view.connect("pushed", self._on_nav_view_pushed)
        try:
            nav_view.connect("get-next-page", self._on_get_next_page)
        except Exception:
            pass

    def _on_nav_view_popped(self, nav_view, page):
        on_history_popped(self._forward_history, nav_view, page)

    def _on_nav_view_pushed(self, nav_view, *args):
        try:
            page = nav_view.get_visible_page()
        except Exception:
            page = args[0] if args else None
        on_history_pushed(self._forward_history, nav_view, page)
        for view in self._find_source_nav_views():
            self._track_nav_view(view)

    def _on_get_next_page(self, nav_view):
        # Peek only: Adw may emit this repeatedly for one (possibly
        # cancelled) gesture; the entry is consumed in "pushed".
        return forward_peek(self._forward_history, nav_view)
