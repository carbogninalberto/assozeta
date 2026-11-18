"""Build the Go server with its adjacent bundled native image executable."""
from pathlib import Path
import subprocess
from TESTS.adapters.django.login_jpeg import build_codec
from TESTS.adapters.go.binary_cache import cached_build

ROOT=Path(__file__).resolve().parents[3]


def build_server(output,log,tool_env,tags=()):
    output=Path(output)
    build_codec(output.with_name('image-codec'),log,tool_env)
    # A tagged build (for example the handoffprobe test build) is a different
    # binary from the same source tree, so the tags are part of the cache key.
    tagged=['-tags',','.join(tags)] if tags else []
    def build():
        subprocess.run(['go','build','-o',str(output),*tagged,'./cmd/server'],cwd=ROOT/'BE_GO',env=tool_env,
                       check=True,timeout=120,stdout=log,stderr=subprocess.STDOUT)
    cached_build('go',output,['go','build',*tagged,'./cmd/server'],tool_env,build)
