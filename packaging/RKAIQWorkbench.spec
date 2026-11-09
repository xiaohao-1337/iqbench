from pathlib import Path


ROOT = Path(SPECPATH).parent
ICON = ROOT / "rkaiq_desktop" / "assets" / "rkaiq.ico"
BOARD_FILES = ("bootstrap.py", "rkaiq_bridge.py", "rkaiq_controls.py", "rkaiq_gray.py", "rkaiq_media.py")
datas = [(str(ICON), "rkaiq_desktop/assets")]
datas.extend((str(ROOT / "board" / name), "board") for name in BOARD_FILES)

a = Analysis([str(ROOT / "launch.py")], pathex=[str(ROOT)], datas=datas, excludes=["tkinter"])
# Qt imports Windows' unversioned ICU API. A third-party ICU on PATH can shadow it.
binaries = [entry for entry in a.binaries
            if not Path(entry[0]).name.lower().startswith(("icuuc.", "icuin.", "icudt"))]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="RKAIQWorkbench",
          console=False, icon=str(ICON), upx=False)
coll = COLLECT(exe, binaries, a.datas, name="RKAIQWorkbench", upx=False)
