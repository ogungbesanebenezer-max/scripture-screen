"""Build ScriptureScreen-Setup.exe: one file a church double-clicks, with no internet and no admin rights needed.

    python3 installer/build_installer.py --windows-app C:/Users/me/church-verse [--version 2026.11.22]

Run from WSL (or Windows) on a machine that has a working Windows install of the app (its .venv and models). It:
  1. stages build/stage/: a private copy of Python 3.12 with the app's libraries, the app code, the free Bibles,
     the hymn library, the speech + paraphrase models and the video player;
  2. compiles installer/ScriptureScreen.iss with Inno Setup (ISCC.exe) into build/ScriptureScreen-Setup.exe.

Never staged: licensed Bibles (my_bibles: NKJV, Bibeli Mimo), settings.json, logs, media, the phone-mic key
(data/phone_mic, made fresh on each laptop), the standby picture (each church sets its own logo in OUR CHURCH),
the unused small.en model, tests and benchmarks.
"""
import argparse
import datetime
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
MODELS = ["models--Systran--faster-whisper-tiny.en", "models--Systran--faster-whisper-base.en",
          "models--Qdrant--all-MiniLM-L6-v2-onnx"]
APP_FILES = ["accent_corrections.json", "README.md", "requirements.txt", "constraints.txt", "LICENSE"]
DATA = ["data/bibles", "data/hymns", "data/verse_vectors.npy"]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


def windows_path(path):
    """C:/... for Windows tools when running under WSL."""
    text = str(path)
    if text.startswith("/mnt/") and len(text) > 6:
        return f"{text[5].upper()}:\\" + text[7:].replace("/", "\\")
    return text


def local_path(path):
    """/mnt/c/... under WSL for a C:\\... path."""
    text = str(path).replace("\\", "/")
    if sys.platform != "win32" and len(text) > 2 and text[1] == ":":
        return Path(f"/mnt/{text[0].lower()}{text[2:]}")
    return Path(text)


def base_python(app):
    """The Python install the app's .venv was made from (pyvenv.cfg 'home')."""
    for line in (app / ".venv" / "pyvenv.cfg").read_text().splitlines():
        if line.startswith("home"):
            return local_path(line.split("=", 1)[1].strip())
    raise SystemExit("No Windows .venv found: run setup.bat on the Windows copy first.")


def stage_python(app, stage):
    source = base_python(app)
    target = stage / "python"
    print(f"Python runtime from {source}")
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", "site-packages", "test", "tests", "idlelib", "Doc", "Tools", "Scripts", "*.chm"))
    packages = target / "Lib" / "site-packages"
    print("Libraries from the app's .venv")
    shutil.copytree(app / ".venv" / "Lib" / "site-packages", packages,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "pytest*", "_pytest", "pip", "pip-*"))
    for name in ("pythonw.exe", "python.exe"):
        if not (target / name).exists():
            raise SystemExit(f"{name} missing from the Python runtime")
    if not (target / "Lib" / "tkinter").exists() or not (target / "tcl").exists():
        raise SystemExit("This Python has no tkinter (tcl/tk): reinstall it with tcl/tk ticked")


def stage_models(app, stage):
    """Hugging Face caches use links into blobs/; copy real files so nothing depends on links on the church laptop."""
    target = stage / "models"
    target.mkdir(parents=True)
    for name in MODELS:
        source = app / "models" / name
        if not source.exists():
            source = REPO / "models" / name
        print(f"  {name}")
        shutil.copytree(source, target / name, symlinks=False, ignore=shutil.ignore_patterns(".no_exist", "blobs"))
    shutil.copytree(app / "models" / "parakeet-tdt-0.6b-v2", target / "parakeet-tdt-0.6b-v2")
    (target / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")


def stage_app(app, stage):
    shutil.copytree(REPO / "verse_screen", stage / "verse_screen", ignore=SKIP)
    for name in APP_FILES:
        if (REPO / name).exists():
            shutil.copy2(REPO / name, stage / name)
    for name in DATA:
        source = REPO / name
        (shutil.copytree(source, stage / name, ignore=SKIP) if source.is_dir()
         else shutil.copy2(source, stage / name))
    (stage / "my_bibles").mkdir()
    shutil.copy2(REPO / "my_bibles" / "README.txt", stage / "my_bibles" / "README.txt")
    shutil.copytree(app / "mpv", stage / "mpv")
    for name in ("Start Scripture Screen (show problems).bat",):
        shutil.copy2(HERE / name, stage / name)
    leaks = [p for p in stage.rglob("*") if p.name in ("settings.json", "key.pem", "cert.pem", "NKJV.json", "BM.json",
                                                         "church_bible.db", "standby.jpg")]
    if leaks:
        raise SystemExit(f"Refusing to build: private files staged: {leaks}")


def find_iscc():
    for root in (os.environ.get("LOCALAPPDATA", ""), "C:/Program Files (x86)", "C:/Program Files"):
        for folder in ("Programs/Inno Setup 6", "Inno Setup 6"):
            path = local_path(f"{root}/{folder}/ISCC.exe") if root else None
            if path and path.exists():
                return path
    for user in Path("/mnt/c/Users").glob("*") if Path("/mnt/c/Users").exists() else []:
        path = user / "AppData/Local/Programs/Inno Setup 6/ISCC.exe"
        if path.exists():
            return path
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows-app", required=True, help="the Windows copy of the app, e.g. C:/Users/me/church-verse")
    parser.add_argument("--build", default=None, help="staging folder (default: <windows-app>/../faithapp-build)")
    parser.add_argument("--version", default=datetime.date.today().strftime("%Y.%m.%d"))
    parser.add_argument("--stage-only", action="store_true")
    args = parser.parse_args()
    app = local_path(args.windows_app)
    build = local_path(args.build) if args.build else app.parent / "faithapp-build"
    stage = build / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    stage_python(app, stage)
    print("App and data")
    stage_app(app, stage)
    print("Models")
    stage_models(app, stage)
    size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file()) / 1e9
    print(f"Staged {size:.2f} GB in {stage}")
    if args.stage_only:
        return
    iscc = find_iscc()
    if not iscc:
        raise SystemExit("Inno Setup 6 (ISCC.exe) not found: install it from jrsoftware.org, then run this again.")
    script = build / "ScriptureScreen.iss"
    shutil.copy2(HERE / "ScriptureScreen.iss", script)
    subprocess.check_call([str(iscc), f"/DAppVersion={args.version}", f"/DStage={windows_path(stage)}",
                           f"/O{windows_path(build)}", windows_path(script)])
    print(f"Built {build / 'ScriptureScreen-Setup.exe'}")


if __name__ == "__main__":
    main()
