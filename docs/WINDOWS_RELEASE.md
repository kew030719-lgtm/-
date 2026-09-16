# Windows 发布与验收

在 GitHub Actions 手动运行 `windows-package`。流程使用 Python 3.12、Node 22、PyInstaller 和 Tauri 2，产出 MSI 与 NSIS 安装包并上传为 `CareerRadar-Windows` artifact。

本地 Windows 构建：

```powershell
pip install -e . pyinstaller
cd frontend
npm ci
npm run build
cd ..\packaging
pyinstaller --clean --noconfirm career-radar-sidecar.spec
Copy-Item dist\career-radar-sidecar.exe ..\frontend\src-tauri\binaries\career-radar-sidecar-x86_64-pc-windows-msvc.exe
cd ..\frontend
npm run tauri build
```

sidecar 只监听 `127.0.0.1:8000`。用户数据默认进入 `%LOCALAPPDATA%\CareerRadar`。

## 发布前必须在全新虚拟机完成

- 无 Python、Node、Rust 环境，从安装到首次排名不超过 15 分钟。
- 错误密钥、无网络、模型超时、服务重启、扩展断开、数据库升级均显示可操作错误。
- 三站点分别保存真实运行指标；无验证码时 20 条在 5–8 分钟完成。
- 卸载时验证“保留本地数据”和“删除本地数据”两个选项。
- 代码签名、安装包签名、自动更新签名与回滚均有效。
- Chrome Web Store 正式扩展已审核；不要求用户开启开发者模式。

当前仓库已提供 sidecar、Tauri 配置和 Windows 构建工作流，但本 Linux 环境没有产出或验收 Windows 安装包。Chrome Web Store 发布、代码签名、更新服务器和卸载保留数据交互需要发布账号与签名材料，不能由源码测试替代。

Chrome 助手发布包由 `python scripts/package_browser_extension.py` 生成。每次上传必须递增 `manifest.json` 版本，补齐商店隐私声明并提交审核；Windows 和 macOS 普通用户不能依赖本地 CRX 路径安装。
