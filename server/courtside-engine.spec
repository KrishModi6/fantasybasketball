# PyInstaller build of the desktop engine (engine.py): a folder with courtside-engine(.exe),
# the projection model and the historical dataset. The desktop shell serves the web build itself.
#
#   cd server && pyinstaller --noconfirm courtside-engine.spec   ->  server/dist/courtside-engine/
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

root = Path(SPECPATH)

hidden = (
    collect_submodules("app")
    + collect_submodules("fbb")
    + collect_submodules("uvicorn")
    # The model file is a joblib pickle of LightGBM boosters, so nothing imports lightgbm statically.
    + collect_submodules("lightgbm", filter=lambda name: "dask" not in name)
    + ["websockets.legacy.server", "websockets.asyncio.server"]
)

a = Analysis(
    [str(root / "engine.py")],
    pathex=[str(root)],
    binaries=collect_dynamic_libs("lightgbm"),
    datas=[
        (str(root / "data" / "history"), "data/history"),
        (str(root / "data" / "model"), "data/model"),
        *collect_data_files("lightgbm"),
    ],
    hiddenimports=hidden,
    # Hosted-only or dev-only packages the engine never imports. LightGBM imports scikit-learn and pandas
    # when they happen to be installed; sklearn then pulls in scipy.stats, which fails to import frozen
    # on Python 3.12.0.
    excludes=["psycopg", "psycopg_binary", "pytest", "pgserver", "tkinter", "matplotlib", "IPython",
              "sklearn", "pandas", "pyarrow"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="courtside-engine",
    console=True,  # no window appears: the desktop shell starts it hidden and reads its output
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="courtside-engine", upx=False)
