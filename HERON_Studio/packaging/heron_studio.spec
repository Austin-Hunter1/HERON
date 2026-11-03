# PyInstaller build of the Studio backend for the Electron app (one folder, not one file, so it starts fast).
#   cd HERON_Studio && pyinstaller packaging/heron_studio.spec --noconfirm --distpath electron/backend-dist
# Build on the OS you ship for (macOS build on a Mac, Windows build on Windows).
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("../studio/static", "studio/static"), ("../engine", "engine"), ("../lens_profiles", "lens_profiles"), ("../sample", "sample")]
binaries, hidden = [], []
for pkg in ("pycolmap", "rasterio", "shapely", "pymavlink", "sgp4", "cv2"):
    d, b, h = collect_all(pkg); datas += d; binaries += b; hidden += h
hidden += collect_submodules("scipy.signal") + collect_submodules("scipy.spatial") + ["pandas", "matplotlib", "matplotlib.path", "PIL", "flask", "shapefile"]

a = Analysis(["../run.py"], pathex=[".."], datas=datas, binaries=binaries, hiddenimports=hidden + ["studio.engine_tasks"],
             excludes=["tkinter", "IPython", "jupyter"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="heron-studio", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="heron-studio")
