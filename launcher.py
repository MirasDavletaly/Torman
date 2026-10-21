import argparse
import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path

from werkzeug.serving import WSGIRequestHandler, make_server

from torman.app import create_app


class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


def set_window_icon(window, asset_root):
    import tkinter as tk

    # Keep references: Tk removes PhotoImage objects when Python collects them.
    window._brand_icons = [tk.PhotoImage(file=str(asset_root / f"icon-{size}.png")) for size in (16, 32, 48, 64, 128)]
    window.iconphoto(True, *window._brand_icons)
    if sys.platform == "win32":
        window.iconbitmap(str(asset_root / "torman.ico"))


def main():
    parser = argparse.ArgumentParser(description="Torman — подбор товаров из PDF")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    data_dir = args.data_dir or Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "Torman"
    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=data_dir / "app.log", level=logging.WARNING, encoding="utf-8")
    app = create_app(data_dir, root / "static")
    server = make_server("127.0.0.1", args.port, app, threaded=True, request_handler=QuietHandler)
    url = f"http://127.0.0.1:{server.server_port}"
    (data_dir / "server.json").write_text(__import__("json").dumps({"url": url, "pid": os.getpid()}), encoding="utf-8")
    if args.headless:
        if sys.stdout:
            print(f"Torman: {url}", flush=True)
        server.serve_forever()
        return
    import tkinter as tk
    from tkinter import ttk

    if sys.platform == "win32":
        import ctypes

        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        set_app_id("Torman.Desktop")

    threading.Thread(target=server.serve_forever, daemon=True).start()
    window = tk.Tk()
    window.title("Torman")
    window.geometry("440x310")
    window.resizable(False, False)
    window.configure(bg="#f6f8fa")
    brand_root = root / "static" / "brand"
    set_window_icon(window, brand_root)
    window._brand_logo = tk.PhotoImage(file=str(brand_root / "logo-dark.png"))
    tk.Label(window, image=window._brand_logo, bg="#f6f8fa", borderwidth=0).pack(pady=(28, 14))
    tk.Label(window, text="Подбор товаров из PDF через Gemini", font=("Segoe UI", 11), bg="#f6f8fa").pack()
    tk.Label(window, text="Программа работает. Каталог сохраняется автоматически.", font=("Segoe UI", 9), bg="#f6f8fa").pack(pady=(10, 18))
    ttk.Button(window, text="Открыть приложение", command=lambda: webbrowser.open(url)).pack(ipadx=28, ipady=7)

    def close():
        server.shutdown()
        window.destroy()

    ttk.Button(window, text="Завершить работу", command=close).pack(pady=8)
    window.protocol("WM_DELETE_WINDOW", close)
    window.after(400, lambda: webbrowser.open(url))
    window.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logging.exception("Startup error")
        if "--headless" in sys.argv:
            raise
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        brand_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "static" / "brand"
        if (brand_root / "torman.ico").is_file():
            set_window_icon(root, brand_root)
        messagebox.showerror("Torman", "Не удалось запустить программу. Проверьте доступ к папке данных и попробуйте снова.\n" + str(exc))
