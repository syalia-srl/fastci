import os
import subprocess
import sys


def test_child():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, ["src", env.get("PYTHONPATH")]))
    subprocess.run([sys.executable, "-c", "import health.core"], check=True, env=env)
