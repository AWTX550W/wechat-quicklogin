# wechat-quicklogin

**电脑免手机完成微信登录确认** —— 两类微信二维码、两条已实测闭环的路径，全程只用微信官方能力，零外发、零代点授权。

![platform](https://img.shields.io/badge/platform-Windows-blue) ![python](https://img.shields.io/badge/python-3.13-yellow) ![tests](https://img.shields.io/badge/e2e%20tests-8%2F8%20PASS-brightgreen) ![license](https://img.shields.io/badge/license-MIT-green)

## ▶ 演示视频（实测成功，二维码已打码）

- 在线播放：[demo_20261110_quicklogin.mp4](https://github.com/AWTX550W/wechat-quicklogin/blob/main/evidence/demo_20261110_quicklogin.mp4)（GitHub 内置播放器）
- 下载观看：[Release v1.0 附件](https://github.com/AWTX550W/wechat-quicklogin/releases)

## TL;DR

网页上的微信登录二维码，以前必须掏手机扫。本项目在本机实测打通了两条免手机路径并真实登录成功：

| 码类型 | 特征 | 电脑侧解法 | 实测站点 |
|---|---|---|---|
| type-A 公众号登录码 | `weixin.qq.com/q/…` | `Alt+A` 截图 → 框选 → 本地「识别二维码」（纯本地扫一扫） | 牛客、百职帮 ✅ |
| type-B 网站应用码 | `open.weixin.qq.com/connect/…` | 弹窗里官方「微信快捷登录」按钮（LNA 本地握手） | 数控驿站 ✅ |

type-B 码被微信服务端限制为「仅摄像头识码」，电脑上识码必跳限制页——这不是 bug，正路是快捷登录按钮。按钮不出现通常是三个环境坑（见[排查](#排查快捷登录按钮不出现)）。

## 快速开始

环境：Windows + PC 微信已登录 + Python 3.10+（`pip install playwright opencv-python zxing-cpp pillow mss && playwright install msedge`）。

```bash
# type-A：屏幕上任何网页露出的公众号登录码，直接扫
python qoder/wx_quick_login.py --site screen

# type-B：网站登录页的快捷登录诊断/应急（默认数控驿站）
python qoder/quick_login_flow.py --url https://www.sk1z.com/login --entry 微信登录 --tag sk1z
```

日常使用无需脚本：type-A 用桌面 bat 双击即扫；type-B 直接在网站弹窗点「微信快捷登录」。

## 目录结构

```
qoder/            两套主脚本（识码 / 快捷登录流程 / 诊断）
desktop-scan/     识码工具与模板
extension/        浏览器扩展源码（给 iframe 补 allow 属性，历史方案）
evidence/         137 项实测证据：截图、日志、JSON、演示视频
docs/开发全记录.md  完整开发日志：破案过程、事故复盘、纠错留痕
HANDOFF.md        给接手者（人或 AI）的交接文档
diagnose.py       只读环境体检：端口监听 / LNA 拦截 / 代理劫持
```

## 排查：快捷登录按钮不出现

按命中概率排序（`diagnose.py` 可自动体检）：

1. **系统代理劫持 localhost 探测**：开代理（Clash/Karing 等）时浏览器对 `localhost.weixin.qq.com` 的探测被路由进代理，微信本地接口拒收 → 关代理即恢复。社区同例：[「关闭梯子就好了」](https://developers.weixin.qq.com/community/develop/doc/000ca41fdf8da0e799549ac0966800)。
2. **Chrome/Edge 142+ 静默拦截本地网络访问（LNA）**：无权限弹窗直接拦探测 → 需给 iframe 补 `allow="local-network-access"` 或以启动参数放行临时进程。参考：[Chrome 140+ 登录 API 异常帖](https://developers.weixin.qq.com/community/personal/oCJUsw-jihgdVBCnzrGqDp30JMkk/answer)。
3. **应用级限制**：check-login 返回 errcode 10057 =「此应用仅支持扫一扫登录」，开放平台配置所致，客户端无解。
4. 微信本地接口按**进程签名/指纹**拒绝非浏览器直连（curl/Python 探测必失败），别据此误判通道坏了。机制说明：[check-login 文档帖](https://developers.weixin.qq.com/community/develop/doc/0002ce58e04628c92ac1380e76b400)。

## 合规红线（本项目自我约束）

- 不向任何微信会话（含文件传输助手）发送消息、链接、图片。
- 不代点「发送 / 确认授权 / 同意 / 允许」；授权卡最后一步必须由账号本人完成。
- 不伪造授权、不碰 Cookie / 账号密码 / 聊天内容；快捷登录是微信官方能力，本项目只做环境排查与流程自动化。
- 仅供学习与个人设备自动化研究，使用者需自行遵守微信平台规则。

## 测试

- `evidence/e2e_headless.py`：扩展补 allow 属性端到端测试，8/8 PASS（本地 mock，无网络依赖）。
- `diagnose.py --json`：只读环境体检，输出端口/LNA/代理三项判定。
- 真实登录证据见 `evidence/day4_loggedin_*`、`evidence/day4_quick_loggedin_*` 与演示视频。

## License

[MIT](LICENSE)
