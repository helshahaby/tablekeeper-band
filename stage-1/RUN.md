# Tablekeeper — Stage 1

Run these from this `stage-1/` directory.

Build:

```sh
docker build -t tablekeeper-stage1 .
```

Start, listening on port 8080:

```sh
docker run --rm -p 8080:8080 -e PORT=8080 tablekeeper-stage1
```

The service listens on `0.0.0.0:$PORT` (default `8080`). `GET /health` returns `200`
as soon as it is up, usually in under a second. All state is in memory: seed it with
`POST /_test/reset` and use `GET /_test/export` and `POST /_test/import` to snapshot
and restore it. The image needs no network at run time. IANA time-zone data comes
from the `tzdata` package, installed when the image is built.

## Tests

The tests are black-box HTTP tests and need only the Python 3.12+ standard library.
They start the server in-process:

```sh
python3 -m unittest discover -s tests -v
```

To run the same tests against a running container:

```sh
TABLEKEEPER_URL=http://127.0.0.1:8080 python3 -m unittest discover -s tests -v
```
