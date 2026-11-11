# 开源方案调研记录（2026-11-09 17:2x）

调研路径：`agent-reach` 的 `github` / `web` 渠道 + 自带网页搜索交叉核对。
`agent-reach doctor` 显示 github / web 均为 default 且 `unverified`（无登录要求）；
`github search` 走 `gh` 后端，`web read` 的 jina/exa 后端两次 TIMEOUT（按 skill 规则不循环重试，改用自带搜索补缺口）。

## 一、检索词与命中

| 检索 | 结果 |
|---|---|
| `github search "wechat quick login chrome local network access"` | 0 条 |
| `github search "wechat login chrome 142"` | 0 条 |
| `github search "local-network-access"` | 命中 WICG 规范仓库（278★），非工具类；其余为无关项目 |
| `github search "screen qrcode scanner desktop"` | 命中若干通用桌面扫码器 |
| 自带搜索 `site:github.com 微信快捷登录 Chrome 142 ...` | 主要命中微信开放社区帖子、博客园技术文、Greasy Fork 脚本 |
| 自带搜索 `GitHub 开源 微信扫码登录 电脑端 不用手机 ...` | 命中 SnapScan / QRUtil / qr-pypass / QuickScan / V2EX 讨论串 |

**结论：针对「微信网站应用快速登录被 Chrome 142+ LNA 拦掉」这个细分问题，GitHub 上没有成熟的通用开源工具。** 公开实现只有：站点侧自己改 wxLogin.js、单站点用户脚本、以及讲原理的技术文章。

## 二、逐个评估

### 1. Greasy Fork「Chrome Local Network Access 自动修复」v1.4（白陀，MIT）
- 唯一直接同类的公开实现：给 iframe 补 `allow="local-network-access *"`。
- 但 `@match https://*.lkcoffee.com/*` —— **只匹配瑞幸咖啡一个站点**；`@run-at document-idle`，靠轮询等 `#wx` 元素，不覆盖异步挂载/任意站点。
- 检索到的源码片段里，`addLocalNetworkAccessToIframes()` 构造了 `attrs` 变量却未使用，`wrapper.innerHTML = iframe.innerHTML` 后 `replaceWith(wrapper.firstElementChild)`，看起来并没有真正把 allow 写回 iframe。**Greasy Fork 页面两次读取超时，没拿到完整源码，所以这条只记为「存疑」，不断定它有 bug。**
- `*` 是 Permissions Policy 的通配 allowlist；MDN 明确「allow 属性里特性名不带 origin 时默认就是 src」，本项目用的裸特性名更克制，不需要放行任意源。
- **不采纳实现**，但确认了「用户侧补 iframe allow」这条路是有人验证过的。

### 2. 博客园《是否开启本地网络访问权限的识别》（2026-11-08，作者标注 AIGC）
最有价值的一篇，指出四个本项目原先没处理好的点（**全部采纳**）：
1. `navigator.permissions.query` 的 prompt/denied 是浏览器明确状态，比探测可靠；弹窗打开时请求**挂起**，1~2 秒超时判「客户端没装」是典型误报。
2. `denied` 之后脚本无法挽回，只能引导去站点设置。
3. CORS 失败 ≠ 权限问题：请求侧要带 `targetAddressSpace: 'loopback'`，iframe 还得有 allow 委托。
4. **Chrome 授权弹窗只能由「用户手势内的一次真实请求」触发**，脚本不能主动弹；所以指引按钮必须可点、点击时重新 probe，成功后刷新 iframe。
另外：横幅只在 prompt/denied 显示、localStorage 记住关闭、`permissionStatus.onchange`、以及**微信快捷登录是遍历端口的，14013 不唯一**（社区有 13015 案例）。

### 3. 微信开放社区 / CNB issue 的事实补充
- 社区定位：`wxLogin.js` 和 `wxopensdk.js` 的**缓存旧版**导致 iframe 缺 `allow="local-network-access"` → 本项目 DNR 的 no-cache 规则正是对症。
- cnb.cool issue #2446（2025-11-19 结帖）：「**官方 SDK 已经修复了**」→ 很多站点升级 SDK 后本扩展并非必需；扩展定位应是「兼容没升级/缓存旧 SDK 的站点」。
- 也有人反馈 Edge 143 / Chrome 143 手动给了本地网址权限仍不行 → 不能只靠用户手动放行，用户侧 DOM 补丁有价值。

### 4. 规范核对（Chrome 145 / MDN）
- Chrome 145 把 `local-network-access` 拆成 `local-network` + `loopback-network`，**旧名保留为 alias，permissions.query 与 Permissions Policy 继续可用** → 本项目三个名字都下发是对的。
- MDN：「For allow attributes, the default behavior is always src」「A feature with no list gets 'src'」→ 裸特性名写法正确，`*` 不必要且更宽松。
- **MDN 还指出一个本项目原先的真 bug**：「Does a new allow value reach a page that has already loaded? **No**」——对已加载完成的 iframe 事后 setAttribute 不生效，必须重新装载文档。本项目原先只 setAttribute 不 reload，已修（`scheduleReload`）。
- Chrome 146 新增企业策略 `LocalNetworkAccessPermissionsPolicyDefaultEnabled` 可自动把权限委托给 iframe，但属企业策略，个人用户不适用，未改系统配置。

### 5. 桌面扫码类开源项目（对应本项目 `desktop-scan/` 兜底）
| 项目 | 亮点 | 不采纳整体替换的原因 |
|---|---|---|
| `yype/QRUtil` (2020, C++) | 腾讯 WeChatCV + ZBar 双引擎，自动检测多码、手动框选，反色/缺角/镜像都能识别 | 要 OpenCV 4.2 + ZBar + 手工编译 wechat_qrcode 模块 + VS2019，太重；且是纯通用扫码器 |
| `yoli-mi/quickscan` (2026, Tauri 2 / Rust) | 1.4MB 安装包、30MB 驻留、系统托盘、Ctrl+Alt+Q 框选、多屏、粘贴/拖拽、明暗模式 | 成熟度最高，但用 jsQR 解码且**不对接 PC 微信**，换掉会丢掉「送进微信→识别图中二维码→确认登录」这一段；需要 Rust 工具链或下载 release |
| `ginkorea/qr-pypass` (MIT, Python) | OpenCV WeChat QR detector、Flask HTTP 服务、分类 payload | 定位是 TOTP/安全工作流，与微信登录无关，且引入 Flask 等依赖 |
| `OliverNymann/SnapScan`、`ciaoly/ScreenBarScanner`、`404neko/DesktopBarcodeScanner`、`Tayasui-rainnya/rsqs` | 桌面截屏扫码通用工具 | 都比本项目 Python 热键脚本更「像产品」，但同样只解码、不对接 PC 微信 |
| `openwechat` / `Gewechat` / `BetterWx-UI` | 个人号协议登录 / 客户端 hook「免扫码」 | 方向完全不同（是登录微信客户端本身，不是登录第三方网站），且有封号/合规风险，**明确排除** |

**取舍：整体不换，只吸收识别引擎。** 本机 `opencv-contrib` 自带 `cv2.wechat_qrcode_WeChatQRCode()`（无参构造即可、无模型文件、零新增依赖），把它作为首选引擎、原 `QRCodeDetector` 降级为回落。这正是 QRUtil / qr-pypass 用的引擎，但保留了本项目「微信登录专用 + 可单独提取」的定位。

## 三、改了什么（本次修补）

| # | 来源 | 改动 | 文件 |
|---|---|---|---|
| 1 | MDN Permissions Policy | 补完 allow 后自动重载一次 iframe（原先对已加载 iframe 无效） | `extension/content.js` `scheduleReload` |
| 2 | 博客园 | `probeLocal` 带 `targetAddressSpace:'loopback'`、区分 pending/blocked | `content.js` / `diag.js` |
| 3 | 博客园 | `judgeLna` 组合判断；超时不再被当成「微信没装」 | `content.js` / `diag.js` |
| 4 | 博客园 | 「放行」按钮改为用户手势内发真实请求触发 Chrome 授权弹窗，成功后自动刷新 iframe | `content.js` |
| 5 | 博客园 | 状态条按 granted/prompt/denied/unknown 分级文案；localStorage 记住关闭；`onchange` 监听 | `content.js` |
| 6 | 社区 | 端口列表加入 13015 等，不再写死 14013 | `content.js` `PROBE_PORTS`、`diag.js` |
| 7 | QRUtil / qr-pypass | 识别引擎链：WeChatCV 优先 → QRCodeDetector 回落 | `desktop-scan/scan_helper.py` |
| 8 | 官方 SDK 已修复的事实 | README 明确扩展定位是「兼容没升级/缓存旧 SDK 的站点」，不夸大必要性 | `README.md` |

## 四、核实边界（不吹的部分）

- Greasy Fork 脚本**完整源码未取到**（两次 TIMEOUT），只依据检索到的片段评估，不断言其实现有缺陷。
- Chrome 官方 LNA 博客页面读取 TIMEOUT，规范细节以 MDN + Chrome 145 Release Notes + Chrome Platform Status 交叉核对。
- `quickscan` 的许可证未核实（仓库读取结果未返回 license 字段）。
- 没做真实第三方网站登录端到端实测；本次所有验证仍是本机 mock 页 + 真实浏览器加载扩展 + 本地识别基准。
- 本项目没有引入任何第三方代码，只借鉴了公开实现/规范里的方法与判断逻辑。
