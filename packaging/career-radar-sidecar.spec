from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("pydantic_ai") + collect_submodules("langgraph")

a = Analysis(
    ["sidecar_entry.py"],
    pathex=[".."],
    binaries=[],
    datas=[
        ("../career_radar/templates", "career_radar/templates"),
        ("../career_radar/static", "career_radar/static"),
        ("../browser-extension", "browser-extension"),
    ],
    hiddenimports=hidden,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name="career-radar-sidecar", console=False, onefile=True,
)
