"""Build the Go server with its adjacent bundled native image executable."""
from pathlib import Path
import subprocess
from TESTS.adapters.django.login_jpeg import build_codec

ROOT=Path(__file__).resolve().parents[3]


def build_server(output,log,tool_env):
    output=Path(output)
    build_codec(output.with_name('image-codec'),log,tool_env)
    subprocess.run(['go','build','-o',str(output),'./cmd/server'],cwd=ROOT/'BE_GO',env=tool_env,
                   check=True,timeout=120,stdout=log,stderr=subprocess.STDOUT)
