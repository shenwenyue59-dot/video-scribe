import threading
import urllib.request
import webbrowser
import time
import uvicorn

URL = 'http://127.0.0.1:8765'

def ready():
    try:
        with urllib.request.urlopen(URL + '/api/health', timeout=1) as r:
            return b'video-scribe' in r.read()
    except Exception:
        return False

def open_when_ready():
    for _ in range(60):
        if ready():
            webbrowser.open(URL)
            return
        time.sleep(.5)

if __name__ == '__main__':
    if ready():
        webbrowser.open(URL)
    else:
        threading.Thread(target=open_when_ready, daemon=True).start()
        uvicorn.run('app:app', host='127.0.0.1', port=8765)
