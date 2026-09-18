# 隐私说明

CareerRadar 当前以本地网页方式运行。SQLite、导出文件、日志和非敏感设置默认保存在项目的 `data/` 目录（Windows 环境若设置了 `APP_DATA_DIR` 或 `%LOCALAPPDATA%`，则使用对应目录）。模型密钥不会写入 SQLite、`settings.json`、日志、导出包或模型请求转储；Windows 源码运行时可使用系统凭据存储。

只有在生成画像、解释、定向简历、面试准备或打招呼语时，相关简历证据与岗位证据才会发送给用户自己配置的模型服务。联系方式保存在独立表中，不提供给 Agent 或模型；仅在用户导出简历时于本地合并。

Chrome 助手只连接 `127.0.0.1` 或 `localhost`，只读取当前任务白名单内的公开搜索页与岗位详情页。上传前移除输入框、文本框、iframe、非 JSON-LD 脚本和 noscript。助手不使用 Cookie API，不读取或导出 Cookie，不绕过验证码，不自动发送申请。

`GET /api/local-data/export` 可导出全部业务数据、导出文件和本地日志；模型密钥永不包含在内。`DELETE /api/local-data?confirmation=DELETE_ALL_LOCAL_DATA` 会清空业务数据、导出文件、日志、非敏感设置和本地凭据存储中的模型密钥。当前没有安装器或卸载器流程。

错误诊断默认只写本地。当前版本没有遥测上传功能，因此不会自动上传统计；未来若增加，必须由用户主动同意并只发送脱敏聚合指标。
