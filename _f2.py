import io, queue, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"E:\守译-发布")
from app import bootstrap
bootstrap.prepare()
from app.config import AppConfig, Region
from app.pipeline import Pipeline, RESULT, TARGET
from app.overlay_native import NativeOverlay
from app.engines import local_llm
from app import window_target as wt

cfg = AppConfig()
cfg.source_lang, cfg.target_lang = "en", "zh"
cfg.translate.engine = "local-llm"
cfg.target_mode = "window"
cfg.target_window_title = "GitHub Desktop"
cfg.region = Region(left=0, top=0, width=1202, height=854)
cfg.settle_frames = 1
events = queue.Queue()
pl = Pipeline(cfg, events)
ov = NativeOverlay()
pl.start()
deadline = time.time() + 100
drawn = 0
while time.time() < deadline and drawn == 0:
    try:
        ev = events.get(timeout=0.5)
    except queue.Empty:
        continue
    if ev.kind == TARGET:
        reg = ev.meta.get("region")
        cfg.region = Region(left=reg[0], top=reg[1], width=reg[2], height=reg[3])
    elif ev.kind == RESULT:
        items = (ev.meta or {}).get("items") or []
        if items:
            ov.show(items, cfg.region)
            drawn = len(items)
            print("DRAWN", drawn, flush=True)
pl.stop(); local_llm.stop_server()
if drawn:
    import ctypes
    ctypes.windll.user32.SetForegroundWindow(wt.find_window("GitHub Desktop"))
    print("RAISED", flush=True)
    end = time.time() + 45
    while time.time() < end:
        time.sleep(0.3)
print("END", drawn, flush=True)
