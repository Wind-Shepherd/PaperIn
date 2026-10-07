from PyInstaller.utils.hooks import collect_delvewheel_libs_directory

# This wheel loads a renamed VC++ runtime from its sibling hyperscan.libs.
datas, binaries = collect_delvewheel_libs_directory("hyperscan")
