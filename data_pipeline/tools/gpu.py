"""Local driver for the GPU server over SSH (no `sshpass` on this machine).

Reads GPU_SSH / GPU_SSH_PASSWORD from the repo-root .env. Commands run through a
login shell so conda's python is on PATH.

    python data_pipeline/tools/gpu.py run "nvidia-smi"
    python data_pipeline/tools/gpu.py put <local> <remote>
    python data_pipeline/tools/gpu.py get <remote> <local>
    python data_pipeline/tools/gpu.py setup          # upload + run setup_gpu.sh
"""
from __future__ import annotations

import argparse
import os
import posixpath
import re
import stat
import sys
from pathlib import Path

import paramiko
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[2]
SERVER_DIR = REPO / "data_pipeline" / "server"
DEFAULT_WORK = "/root/autodl-tmp/music"


def connect() -> paramiko.SSHClient:
    load_dotenv(REPO / ".env")
    s = os.getenv("GPU_SSH", "")
    pw = os.getenv("GPU_SSH_PASSWORD", "")
    um = re.search(r"([\w.-]+)@([\w.-]+)", s)
    pm = re.search(r"-p\s+(\d+)", s)
    if not um:
        sys.exit("cannot parse GPU_SSH in .env: " + repr(s))
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(um.group(2), port=int(pm.group(1)) if pm else 22,
                username=um.group(1), password=pw, timeout=25, banner_timeout=25)
    return cli


def _ensure_remote_dir(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    parts = remote_dir.strip("/").split("/")
    cur = ""
    for p in parts:
        cur += "/" + p
        try:
            sftp.stat(cur)
        except IOError:
            sftp.mkdir(cur)


def put(sftp: paramiko.SFTPClient, local: str, remote: str) -> None:
    lp = Path(local)
    if lp.is_dir():
        for f in lp.rglob("*"):
            rel = f.relative_to(lp).as_posix()
            r = f"{remote.rstrip('/')}/{rel}"
            if f.is_dir():
                _ensure_remote_dir(sftp, r)
            else:
                _ensure_remote_dir(sftp, posixpath.dirname(r))
                sftp.put(str(f), r)
                print(f"put {rel} -> {r}")
    else:
        _ensure_remote_dir(sftp, posixpath.dirname(remote))
        sftp.put(str(lp), remote)
        print(f"put {lp} -> {remote}")


def get(sftp: paramiko.SFTPClient, remote: str, local: str) -> None:
    def _walk(r: str, l: Path) -> None:
        l.mkdir(parents=True, exist_ok=True)
        for entry in sftp.listdir_attr(r):
            rp = f"{r.rstrip('/')}/{entry.filename}"
            lp = l / entry.filename
            if stat.S_ISDIR(entry.st_mode):
                _walk(rp, lp)
            else:
                sftp.get(rp, str(lp))
                print(f"get {rp} -> {lp}")
    _walk(remote, Path(local))


def cmd_run(cli: paramiko.SSHClient, command: str) -> int:
    stdin, stdout, stderr = cli.exec_command(f"bash -lc {shell_quote(command)}", timeout=None)
    for line in iter(stdout.readline, ""):
        sys.stdout.write(line)
    err = stderr.read().decode("utf-8", "replace")
    if err.strip():
        sys.stderr.write(err)
    return stdout.channel.recv_exit_status()


def shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def main() -> int:
    ap = argparse.ArgumentParser(description="GPU server driver")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").add_argument("command")
    p = sub.add_parser("put"); p.add_argument("local"); p.add_argument("remote")
    p = sub.add_parser("get"); p.add_argument("remote"); p.add_argument("local")
    sub.add_parser("setup")
    args = ap.parse_args()

    cli = connect()
    try:
        if args.cmd == "run":
            return cmd_run(cli, args.command)
        with cli.open_sftp() as sftp:
            if args.cmd == "put":
                put(sftp, args.local, args.remote)
            elif args.cmd == "get":
                get(sftp, args.remote, args.local)
            elif args.cmd == "setup":
                _ensure_remote_dir(sftp, DEFAULT_WORK)
                put(sftp, str(SERVER_DIR / "setup_gpu.sh"), f"{DEFAULT_WORK}/setup_gpu.sh")
                return cmd_run(cli, f"bash {DEFAULT_WORK}/setup_gpu.sh")
    finally:
        cli.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
