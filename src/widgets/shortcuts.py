from gi.repository import Gtk

def build_shortcuts():
    builder = Gtk.Builder.new_from_resource('/com/rini/kaori/shortcuts.ui')
    return builder.get_object("shortcuts_dialog")
