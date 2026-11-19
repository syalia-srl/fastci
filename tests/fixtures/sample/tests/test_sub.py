import os
import subprocess
import sys


def test_child_serves():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(["src", env.get("PYTHONPATH", "")])
    out = subprocess.run([sys.executable, "-c", "from sample.server import serve; print(serve())"],
                         capture_output=True, text=True, env=env)
    assert out.stdout.strip() == "served"


def test_child_reads_data():
    out = subprocess.run([sys.executable, "-c", "import os; print(open(os.path.join('static', 'da' + 'ta.json')).read())"],
                         capture_output=True, text=True)
    assert '"n"' in out.stdout
