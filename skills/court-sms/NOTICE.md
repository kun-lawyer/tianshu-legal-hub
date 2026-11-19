# 来源与改动说明

本技能改自杨卫薪律师及贡献者的 [cat-xierluo/legal-skills](https://github.com/cat-xierluo/legal-skills) 中的 `skills/court-sms`（MIT 许可，原文见 `LICENSE.txt`，未改动）。感谢原作者的开放分享。上游对 FachuanHybridSystem 的参考与致谢见 `references/ATTRIBUTION.md`。

本版改动要点：

- 运行环境改写为 Windows + PowerShell 7：命令一律用 `python`（不用 `python3`），其他平台的 HTTP 请求改用 `Invoke-RestMethod` / `Invoke-WebRequest`，不依赖 curl、jq；暂存目录改为 `$env:TMP\court-sms-staging\`。
- 增加安全约束：不得代用户绕过验证，遇交互式登录、滑块或图形验证码时引导用户在可见浏览器中自行完成；下载前核对链接域名与最终跳转目标，未知域名、证书异常或要求额外身份凭证时停止并提示人工核验。
- 浏览器自动化只使用宿主当前实际提供的工具，不绑定特定 MCP 配置。
- 开源整理：只分发 `config/*.example.json`，真实配置与 `archive/` 已列入 `.gitignore`；示例案号改为占位符；说明文字改为工具中立写法。
