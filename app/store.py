"""SQLite document store + file storage (local disk or any S3-compatible service)."""
import json, os, shutil, sqlite3, threading
from .config import cfg, env

_lock = threading.RLock()
_conn = {}


def db():
    path = os.path.join(cfg.data_dir, "state.db")
    if path not in _conn:
        os.makedirs(cfg.data_dir, exist_ok=True)
        c = sqlite3.connect(path, check_same_thread=False)
        c.execute("create table if not exists docs(kind text, id text, body text, primary key(kind,id))")
        c.execute("create table if not exists idem(key text, route text, req text, status int, body text, primary key(key,route))")
        _conn[path] = c
    return _conn[path]


def put(kind, id, obj):
    with _lock:
        db().execute("insert or replace into docs values(?,?,?)", (kind, id, json.dumps(obj)))
        db().commit()


def get(kind, id):
    with _lock:
        r = db().execute("select body from docs where kind=? and id=?", (kind, id)).fetchone()
    return json.loads(r[0]) if r else None


def idem_get(key, route):
    with _lock:
        return db().execute("select req,status,body from idem where key=? and route=?", (key, route)).fetchone()


def idem_put(key, route, req, status, body):
    with _lock:
        db().execute("insert or replace into idem values(?,?,?,?,?)", (key, route, req, status, json.dumps(body)))
        db().commit()


def ping():
    with _lock:
        db().execute("select 1")


# ---- file storage adapter ----
def _s3(endpoint=None):
    import boto3
    from botocore.config import Config
    return boto3.client("s3", config=Config(signature_version="s3v4", s3={"addressing_style": "path"}), endpoint_url=endpoint or env("S3_ENDPOINT") or None, aws_access_key_id=env("S3_ACCESS_KEY"),
                        aws_secret_access_key=env("S3_SECRET_KEY"), region_name="us-east-1")


def save_output(local_path, key):
    """Copy a produced file to storage; return a download URL (S3 presigned for 7 days, or a local /files URL)."""
    if cfg.storage == "s3":
        c, b = _s3(), env("S3_BUCKET", "incubrix-editor")
        try:
            c.head_bucket(Bucket=b)
        except Exception:
            c.create_bucket(Bucket=b)
        c.upload_file(local_path, b, key)
        # the URL is signed for the host the CLIENT will use (S3_PUBLIC_ENDPOINT), not the docker-internal one
        signer = _s3(env("S3_PUBLIC_ENDPOINT") or None)
        return signer.generate_presigned_url("get_object", Params={"Bucket": b, "Key": key}, ExpiresIn=7 * 86400)
    dst = os.path.join(cfg.data_dir, "outputs", key)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(local_path, dst)
    return f"{cfg.public_url}/api/v1/files/{key}"
