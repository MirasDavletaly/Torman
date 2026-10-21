"""Exercise the packaged executable over HTTP; never calls Google."""
import io
import json
import sys
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from pdf_fixture import pdf_bytes

ROOT = Path(__file__).resolve().parent.parent
EXECUTABLE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "dist" / "Torman.exe"
DATA = ROOT / ".test-output" / ("exe-smoke-" + uuid.uuid4().hex)
DATA.mkdir(parents=True)


def request(url, path, payload=None, content_type="application/json"):
    data = json.dumps(payload).encode() if isinstance(payload, dict) else payload
    headers = {"X-Torman-Request":"1", "Content-Type":content_type} if data is not None else {}
    req = urllib.request.Request(url + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        raw = response.read()
        return json.loads(raw) if "application/json" in response.headers.get("Content-Type", "") else raw


def start(previous_pid=None):
    process = subprocess.Popen([str(EXECUTABLE),"--headless","--data-dir",str(DATA)], creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError(f"Executable exited: {process.returncode}")
            try:
                meta = json.loads((DATA / "server.json").read_text(encoding="utf-8"))
                if meta["pid"] != previous_pid:
                    request(meta["url"], "/api/state")
                    return process, meta
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(.2)
        raise RuntimeError("Packaged executable did not become ready")
    except Exception:
        stop(process)
        raise


def stop(process):
    if process.poll() is None:
        # Only the exact process tree created by this test is terminated.
        subprocess.run(["taskkill","/PID",str(process.pid),"/T","/F"],capture_output=True,check=False,creationflags=subprocess.CREATE_NO_WINDOW)
        process.wait(timeout=10)


def run():
    process, meta = start()
    try:
        url = meta["url"]
        assert request(url,"/api/state")["catalog"] is None
        assert "Что будем искать?" in request(url,"/").decode("utf-8")
        assert b"v1.2.2" in request(url,"/")
        assert b"function renderResults" in request(url,"/assets/app.js")
        file=io.BytesIO(pdf_bytes())
        boundary="TormanTestBoundary"
        multipart=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.pdf"\r\nContent-Type: application/pdf\r\n\r\n'.encode()+file.getvalue()+f"\r\n--{boundary}--\r\n".encode())
        upload=request(url,"/api/upload",multipart,f"multipart/form-data; boundary={boundary}")
        params={"upload_id":upload["id"]}
        assert request(url,"/api/upload/preview",params)["page_count"]==2
        catalog=request(url,"/api/upload/commit",params)["catalog"]
        assert request(url,"/api/catalog/original")==file.getvalue()
        assert request(url,"/api/catalog/document?catalog_id="+catalog["id"])==file.getvalue()
        assert b"search-mode" in request(url,"/assets/pdf.css")
        assert request(url,"/assets/brand/torman.ico")[:4]==b"\x00\x00\x01\x00"
        assert b"direct-search" in request(url,"/")
        request(url,"/api/settings",{"model":"gemini-3.8-flash","key":"exe-smoke-not-a-real-key","remember":True})
        assert b"exe-smoke-not-a-real-key" not in (DATA/"torman.sqlite3").read_bytes()
    finally:
        stop(process)
    process, meta = start(meta["pid"])
    try:
        state=request(meta["url"],"/api/state")
        assert state["catalog"]["id"]==catalog["id"]
        assert state["catalog"]["page_count"]==2
        assert state["catalog"]["source_type"]=="pdf"
        assert state["index"]["ready"] is False
        assert state["settings"]["has_key"] is True
        pages=request(meta["url"],"/api/catalog")["pages"]
        assert "Pipe A1" in pages[0]["text"]
        assert "50 mm" in pages[0]["text"]
    finally:
        stop(process)
    print("PASS: packaged EXE starts, serves branded PDF/fast-search UI, imports PDF, retains exact source bytes and page links, persists catalogue and encrypted key after restart.")


if __name__=="__main__":
    run()
