"""Host side of the safe renderer: run rasterize.py under bubblewrap, after inference (never during timed runs).

Sandbox: all namespaces unshared (no network), empty tmpfs /tmp, read-only /usr and font config only, no home, no
data drive, cleared environment, die-with-parent, plus rlimits inside and a wall timeout outside. The SVG arrives on
stdin; only PNG bytes and a JSON status line come back.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
BWRAP = ["bwrap", "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
         "--symlink", "usr/bin", "/bin", "--ro-bind-try", "/etc/fonts", "/etc/fonts",
         "--ro-bind-try", "/var/cache/fontconfig", "/var/cache/fontconfig",
         "--ro-bind", str(HERE / "rasterize.py"), "/rasterize.py", "--proc", "/proc", "--dev", "/dev",
         "--tmpfs", "/tmp", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
         "--setenv", "HOME", "/tmp", "--setenv", "XDG_CACHE_HOME", "/tmp", "--chdir", "/tmp"]


def render(svg: str, png_path: Path, timeout: float = 40) -> dict:
    try:
        r = subprocess.run(BWRAP + ["/usr/bin/python3", "-I", "/rasterize.py"], input=svg.encode("utf-8"),
                           capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "stage": "timeout"}
    lines = [l for l in r.stderr.decode("utf-8", "replace").splitlines() if l.startswith("{")]
    try:
        st = json.loads(lines[-1]) if lines else {"ok": False, "stage": "no-status"}
    except ValueError:
        st = {"ok": False, "stage": "bad-status"}
    st["rc"] = r.returncode
    if r.returncode != 0 and not lines:
        st["stderr_tail"] = r.stderr.decode("utf-8", "replace")[-400:]
    if st.get("ok") and r.stdout.startswith(b"\x89PNG"):
        png_path.parent.mkdir(parents=True, exist_ok=True)
        png_path.write_bytes(r.stdout)
        st["png"] = str(png_path)
    elif st.get("ok"):
        st.update(ok=False, stage="no-png")
    return st
