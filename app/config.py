import os


def env(k, d=""):
    return os.environ.get(k, d)


def flag(k, d="true"):
    return env(k, d).lower() in ("1", "true", "yes")


class C:  # read at access time so tests can monkeypatch os.environ
    data_dir = property(lambda s: env("DATA_DIR", "./data"))
    public_url = property(lambda s: env("PUBLIC_URL", "http://localhost:8000").rstrip("/"))
    api_token = property(lambda s: env("API_TOKEN"))
    rate = property(lambda s: int(env("RATE_LIMIT_PER_MIN", "120")))
    font = property(lambda s: env("FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
    transcriber = property(lambda s: env("TRANSCRIBER", "auto"))
    whisper_model = property(lambda s: env("WHISPER_MODEL", "base.en"))
    nlu = property(lambda s: env("NLU", "rules"))
    storage = property(lambda s: env("STORAGE", "local"))
    callback_secret = property(lambda s: env("CALLBACK_SECRET", "change-me"))
    snap = property(lambda s: flag("SNAP_CUTS"))
    low_conf = property(lambda s: float(env("LOW_CONF", "0.6")))
    max_speed = property(lambda s: float(env("MAX_SPEED", "4")))
    min_speed = property(lambda s: float(env("MIN_SPEED", "0.25")))
    min_caption = property(lambda s: float(env("MIN_CAPTION_S", "0.7")))
    max_upload = property(lambda s: int(env("MAX_UPLOAD_MB", "500")) * 1024 * 1024)


cfg = C()
