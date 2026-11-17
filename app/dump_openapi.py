"""python -m app.dump_openapi > openapi.yaml   (the file shipped in the repo; a test checks it matches the running app)"""
import sys
import yaml
from .main import app


def spec():
    return yaml.safe_dump(app.openapi(), sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    sys.stdout.write(spec())
