from __future__ import annotations

import os
import socket
import threading
import urllib.error
import urllib.request

import gi
import uvicorn

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")

from gi.repository import GLib, Gtk, WebKit2

os.environ["SURFACE_CONDUCTIVITY_DESKTOP"] = "1"

from .config import load_config
from .main import create_app


class DesktopApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="com.surfaceconductivity.monitor")
        self._server: uvicorn.Server | None = None
        self._server_thread: threading.Thread | None = None
        self._window: Gtk.ApplicationWindow | None = None
        self._stack: Gtk.Stack | None = None
        self._status_label: Gtk.Label | None = None
        self._webview: WebKit2.WebView | None = None
        self._config = load_config()
        self._port = _find_free_local_port()
        self._app, _service = create_app(self._config)
        self._display_url = f"http://127.0.0.1:{self._port}"
        self._ready_checks = 0

    def do_activate(self) -> None:
        if self._window is not None:
            self._window.present()
            return

        self._build_window()
        self._start_server()
        self._window.present()
        GLib.timeout_add(250, self._wait_for_server)

    def do_shutdown(self) -> None:
        self._stop_server()
        super().do_shutdown()

    def _build_window(self) -> None:
        window = Gtk.ApplicationWindow(application=self)
        window.set_title("Fermart Lab Data Logger")
        window.set_default_size(1320, 900)
        window.set_position(Gtk.WindowPosition.CENTER)

        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        stack.set_transition_duration(180)

        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        status_box.set_border_width(28)
        status_box.set_halign(Gtk.Align.CENTER)
        status_box.set_valign(Gtk.Align.CENTER)

        title = Gtk.Label(label="Starting Fermart Lab Data Logger")
        title.get_style_context().add_class("title-2")

        status_label = Gtk.Label(
            label=f"Connecting to local service on {self._display_url}"
        )
        status_label.set_justify(Gtk.Justification.CENTER)
        status_label.set_line_wrap(True)

        hint = Gtk.Label(
            label="This desktop window replaces the browser. You can close it normally when you finish."
        )
        hint.set_justify(Gtk.Justification.CENTER)
        hint.set_line_wrap(True)

        status_box.pack_start(title, False, False, 0)
        status_box.pack_start(status_label, False, False, 0)
        status_box.pack_start(hint, False, False, 0)

        webview = WebKit2.WebView()
        webview.get_settings().set_enable_write_console_messages_to_stdout(True)

        stack.add_named(status_box, "status")
        stack.add_named(webview, "app")
        stack.set_visible_child_name("status")

        window.add(stack)
        window.connect("delete-event", self._on_window_delete)
        window.show_all()

        self._window = window
        self._stack = stack
        self._status_label = status_label
        self._webview = webview

    def _start_server(self) -> None:
        server_config = uvicorn.Config(
            self._app,
            host="127.0.0.1",
            port=self._port,
            log_level="info",
            reload=False,
        )
        self._server = uvicorn.Server(server_config)
        self._server_thread = threading.Thread(target=self._server.run, daemon=True)
        self._server_thread.start()

    def _wait_for_server(self) -> bool:
        self._ready_checks += 1
        if self._server is None:
            return False

        if self._server_is_reachable():
            if self._webview is not None and self._stack is not None:
                self._webview.load_uri(self._display_url)
                self._stack.set_visible_child_name("app")
            return False

        if self._status_label is not None and self._ready_checks % 8 == 0:
            self._status_label.set_text(
                f"Waiting for local service to start on {self._display_url}..."
            )
        return True

    def _server_is_reachable(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self._display_url}/api/state", timeout=0.3) as response:
                return response.status == 200
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            return False

    def _stop_server(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._server_thread is not None and self._server_thread.is_alive():
            self._server_thread.join(timeout=5)

    def _on_window_delete(self, *_args) -> bool:
        self.quit()
        return False


def run_desktop_app() -> None:
    app = DesktopApp()
    app.run(None)


def _find_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
