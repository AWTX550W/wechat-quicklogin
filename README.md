# 微信电脑确认 · QuickLogin 修复

> **实测成功演示**（2026-11-10，二维码已打码）：[`evidence/demo_20261110_quicklogin.mp4`](evidence/demo_20261110_quicklogin.mp4)

一句话：**你记忆里"打开网页自动用微信登录"不是幻觉，那是微信官方的「网站应用快速登录」，现在还在。它没坏，是被 Chrome/Edge 142+ 的新权限拦了，然后静默退回二维码。所以不用造一个"模拟手机扫一扫"的插件——把这条官方通道修回来，电脑上点一下确认就行。**

> **已做过开源方案调研（2026-11-09）**：核心问题在 GitHub 上没有更成熟的通用开源工具，所以**继续用自己这套**；但吸收了三处人家做得更好的地方，并修掉一个自己代码里的真 bug。完整检索记录、逐项评估和核实边界在 `evidence/oss_survey.md`。

## 为什么会变成"又要掏手机"

证据链（都可复核）：

1. **微信开放平台官方文档**（《网站应用授权登录》，最后编辑 2026-05-27）原文：

   > 为了简化用户登录流程，在微信 3.9.11 for Windows 及以上版本与微信 4.0.0 for Mac 及以上版本，用户可使用网站应用快速登录功能。
   > 快速登录**无需扫码，可直接在 Windows / Mac 设备上进行确认**。
   > 注意：Chrome 在 142 及以上版本增加了本地网络访问权限的提示……如授权框非使用 wxLogin.js 引入而是自行嵌入 iframe，则需要明确地在 iframe 增加 `allow="local-network-access"` 属性。

2. **底层机制**：登录 iframe 会请求 `https://localhost.weixin.qq.com:14013/api/check-login`，这个域名 DNS 指向 `127.0.0.1`，由已登录的 PC 微信客户端本地提供。请求成功 → 页面显示头像 +「快捷登录」；**失败 → 静默回退成二维码**。端口不唯一，社区有 13015 的案例，实际是遍历的。

3. **Chrome 142（2025-10）起**把"公网页面访问 loopback"纳入 Local Network Access (LNA) 权限，默认要用户点一次「允许」，且权限必须通过 iframe 的 `allow` 属性下放。微信开放社区里有确凿案例：Chrome 142.0.7444.176 上报 `Permission was denied for this request to access the unknown address space`，其他浏览器正常；社区定位到是 **wxLogin.js / wxopensdk.js 的缓存旧版生成的 iframe 没加 allow**。Edge 143+ 同样中招。Chrome 145 又把权限拆成 `local-network` / `loopback-network`，旧名保留为 alias。

4. **本机实测**（`python diagnose.py`，见 `evidence/diagnose_20261109_171452.txt`）：

   ```
   PC 微信   : 4.1.15.56  (要求 >= 3.9.11) -> 满足
   监听端口  : 127.0.0.1:14013, 14016, 14019, 14022, 14023
   14013 在监听 —— 正是 check-login 端口
   DNS       : localhost.weixin.qq.com -> 127.0.0.1
   浏览器    : Microsoft Edge 157.0.4341.42  LNA 风险: 是
   ```

   也就是说：**服务端（PC 微信）好端端的，坏在浏览器侧那道权限。**

   > 补充：用 Python/curl 直连这些端口，TCP 通但 TLS 握手被对端主动断开（`SSLEOFError`）。微信本地服务用内部 TLS / 客户端指纹校验，非浏览器上下文被拒属预期。**这一条不能当作"通道不可用"的证据**，判据只有一个：登录页有没有出现快捷登录头像。

## 交付了什么

```
wechat-quicklogin/
├── extension/            ← 主交付：浏览器扩展（可单独拷走，无需构建/依赖）
├── desktop-scan/         ← 兜底：老站点的桌面「扫一扫」热键工具
├── diagnose.py           ← 本机通道诊断（纯标准库，只读）
├── evidence/             ← 实测证据、可复跑的端到端脚本、开源调研记录
│   ├── oss_survey.md     ← 开源方案调研与取舍（含核实边界）
│   ├── e2e_headless.py   ← 真实浏览器加载扩展的端到端验证
│   └── ...
└── README.md
```

### 1) 浏览器扩展（主力）

它做这些事，全部在用户侧 DOM / 请求头层面，不改网站业务代码、不改 iframe 的 `src`：

- 用 MutationObserver 抓住同步和异步挂载的微信登录 iframe，补上
  `allow="local-network-access; loopback-network; local-network"`
  （覆盖 Chrome 142~145+ 的权限名拆分；按 Permissions Policy 规范，**裸特性名默认授予 iframe src 源**，不需要也不应该写 `*`）。
- **补完属性后自动重载一次 iframe**。MDN 明确「对已经加载完的 iframe 事后改 allow 不生效」，这是调研中发现并修掉的真 bug。
- 给 `res.wx.qq.com` / `open.weixin.qq.com` / `wwcdn.weixin.qq.com` 的登录 SDK 请求加 `no-cache`，绕开"缓存旧版 wxLogin.js 生成无 allow 的 iframe"这个根因。
- 状态条（Shadow DOM 隔离）：按 `granted / prompt / denied / unknown` 分级给不同文案，提供「重新检测」「放行浏览器权限」「诊断」。
- **「放行」按钮会在用户手势内发一次真实 loopback 请求**（带 `targetAddressSpace:'loopback'`），这才是 Chrome 授权弹窗的唯一触发方式；成功后自动刷新登录 iframe。不是只给一段文字让你自己去找设置。
- 探测时把**挂起 pending** 和**被拦 blocked** 分开；授权框打开时请求挂起属正常，绝不误报成"微信没装"。权限状态优先、探测只作辅助。
- 关闭状态记在 localStorage（带版本号），监听 `permissionStatus.onchange`。
- 自带诊断页，在扩展自己 origin 下探测本地端口（含 13015）+ 查 LNA 状态，一键复制诊断报告。

**安装**：Edge 打开 `edge://extensions`（Chrome 是 `chrome://extensions`）→ 打开「开发人员模式」→
「加载解压缩的扩展」→ 选 `extension/` 目录。装完会弹一次诊断页。

扩展申请了 `<all_urls>`，因为微信登录可能出现在任何站点；它只读取 iframe 的 `src`/`allow` 属性，不读取页面内容、Cookie 或表单。

> **一个诚实的前提**：微信官方 SDK 已在 2025-11-19 前后修复（cnb.cool issue #2446 结帖说明）。很多站点升级 SDK 后根本不需要这个扩展。它真正的定位是**兼容没升级、或缓存里还是旧版 wxLogin.js 的站点**，以及帮你把权限状态看清楚。

### 2) 必须配合的一步（扩展替不了）

浏览器如果已经把某个站点的「本地网络访问」记为**阻止**，扩展无法覆盖浏览器的拒绝决定。在**那个登录网站**上：

> 地址栏左侧锁/设置图标 →「网站设置 / 权限」→ 把「本地网络访问」改成**允许**
> 或直接点状态条的「放行浏览器权限」，让浏览器弹授权框，点「允许」。

然后刷新登录页，点「重新检测」。出现**头像 +「快捷登录」**就是修好了，电脑上点确认完成，手机全程不用动。

### 3) 兜底：桌面「扫一扫」

有些老站点压根没接快速登录（只丢一张二维码），或者网站自己传了 `fast_login=0` 主动关掉。这时用 `desktop-scan/`：

- `python desktop-scan/scan_helper.py` 常驻，按 `Alt+Q` 截屏 → 本地识别二维码 →
  自动裁切存 `out/` → **二维码图片进剪贴板** → 在 PC 微信「文件传输助手」Ctrl+V → 右键「识别图中二维码」→ 电脑上点确认。
- 识别引擎链：**腾讯 WeChatCV（`cv2.wechat_qrcode_WeChatQRCode`）优先 → `QRCodeDetector` 回落**。本机 opencv-contrib 内置模型，无参构造即可，零新增依赖。这是从 QRUtil / qr-pypass 借鉴的，对模糊、反色、小图明显更稳（对比数据见下）。
- 也可 `python desktop-scan/scan_helper.py --once` / `--image a.png` / `--bench` / `--selftest`。
- 双击 `desktop-scan/run.bat` 会先跑自测再进热键模式。
- 裁出的二维码若短边 < 400px，会整数倍最近邻放大后再存盘/进剪贴板（微信 PC 端识码对过小图容易漏检；放大不插值，二次解码内容不变）。

**桌面客户端场景（2026-11-09 实拍：腾讯 Marvis 登录弹窗）**：这类弹窗在独立客户端里，**浏览器扩展注入不到，完全无效**；但兜底工具是屏幕级的，与应用类型无关。实测截图解出的码是
`https://open.weixin.qq.com/connect/confirm?uuid=001kaLzs0b2Tml2o`（标准扫码登录确认码，不是 qrconnect 快速登录），
识别 → 裁切 261×261 → 放大 522×522 → 二次解码内容一致，见 `evidence/marvis_case.txt`。

> **成败判据（诚实边界）**：PC 微信能不能在电脑上直接确认，是**分站点**的。V2EX 实测 DZ 论坛 / 腾讯企业邮箱 / 企业微信后台可以，微信公众平台后台、微信网页版不行（后者要求必须用相机扫、不接受相册识图）。
> 弹出「授权 / 确认登录」页 → 点确认就成；只弹「前往 / 复制链接」或提示「请用手机微信扫码」→ 这条路走不通，只能手机扫。
> 这种站点若有「QQ 登录 / 账号密码登录」（Marvis 就有），换那个反而最省事，代价是账号体系不同、数据不互通。

> 微信 PC 版自带类似能力：3.6.0 起按 `Alt+A` 截图、点工具栏二维码图标就能识别屏幕上的码；左下角「扫一扫 → 相册」也能选本地图片。工具只是把它自动化、批处理化，并补上记录、分类和微信登录专用引导。

### 4) 桌面一键版（2026-11-09 晚重做，推荐）

用户反馈「双击文件夹 → 双击 bat → 黑窗口 → 按 Alt+Q → 切微信粘贴」比掏手机还麻烦，于是压成一步：

- **桌面直接一个 `扫码登录.bat`**，双击即用（用 `pythonw.exe` 启动，**没有黑框**）。
- 弹出置顶小窗，**启动即自动截屏识别**屏幕上的登录码并放大显示，图片同时进剪贴板；不用按热键、不用先跑自测、不用切来切去。
- 点「① 用微信扫这个码」→ 自动把 PC 微信拉到前台并 Ctrl+V；用户只需在微信里右键图片 →「识别图中二维码」→ 点确认。
- 还有「重新抓一次 / 选一张图 / 复制链接 / 置顶」。窗口里那张放大的码也方便微信 `Alt+A` 直接框选。
- `wxscan/` 是 ASCII 名的脚本目录（中文路径不写进 .bat，避免 cmd 代码页损坏）；`WXSCAN_OUT=%TEMP%\wxscan_out` 让桌面不冒出 `out/`。源码同步在 `desktop-scan/wxscan.py`，界面截图 `evidence/gui_preview.png`。

> **硬边界**：最后那下「确认登录」必须由已登录的微信客户端自己发出，第三方窗口不能代替点击，也不伪造授权。所以流程压到最少，但仍留微信里那一下右键 + 确认——这不是没做完，是微信的安全模型。

**高 DPI 踩坑**：本机 150% 缩放，给 tkinter 声明 `PER_MONITOR_AWARE_V2` 后 `geometry` 会被再除一次缩放（380×670 实际只给 295×451），文字挤爆、按钮被裁；**最终不声明 DPI 感知**，由系统整窗位图等比拉伸，布局比例保持正确，并把逻辑尺寸收到 380×670。二维码是黑白块，拉伸后反而更清楚。

## 开源方案调研与取舍（2026-11-09）

用 `agent-reach` 的 github / web 渠道 + 自带网页搜索交叉核对。`github search "wechat quick login chrome local network access"` 和 `"wechat login chrome 142"` **都是 0 条**；`local-network-access` 只命中 WICG 规范仓库（278★），不是工具。完整记录在 `evidence/oss_survey.md`，这里给结论：

| 候选 | 判断 |
|---|---|
| Greasy Fork「Chrome Local Network Access 自动修复」v1.4 | 唯一直接同类，但 `@match` 只匹配 `*.lkcoffee.com` 一个站点、`document-idle` 轮询、**不覆盖异步挂载**。检索到的源码片段里 allow 是否真写回 iframe 存疑（页面两次读取超时，没拿到完整源码，不断言）。`*` 比裸特性名更宽松，没必要。**不采纳** |
| 博客园《是否开启本地网络访问权限的识别》(2026-11-08) | 不是项目而是一篇技术文，但把 LNA 探测的坑讲得最透：**挂起≠失败、用户手势触发真实请求才能弹授权框、CORS 失败≠权限问题、横幅分级、端口遍历**。**全部采纳**（见上面扩展功能列表） |
| MDN Permissions Policy / Chrome 145 Release Notes | 规范级核对：确认裸 allow 特性名正确、旧权限名保留 alias、**「已加载 iframe 事后改 allow 不生效」**。**据此修掉自己的真 bug** |
| `yoli-mi/quickscan` (Tauri 2 / Rust) | 桌面扫码器里最成熟：1.4MB、30MB 驻留、托盘、框选、多屏、粘贴拖拽。但用 jsQR 且**不对接 PC 微信**，换掉会丢掉「送进微信→确认登录」这一段。**不整体替换**；如果你平时还要扫别的码，可以另外装它 |
| `yype/QRUtil` / `ginkorea/qr-pypass` | 都用腾讯 WeChatCV 引擎，识别退化图像更强。QRUtil 要手工编译 C++ 依赖太重，qr-pypass 定位是 TOTP 工具。**只吸收引擎**，正好本机 cv2 已有 |
| `openwechat` / `Gewechat` / `BetterWx-UI` | 个人号协议登录 / 客户端 hook「免扫码」，方向完全不同（是登录微信客户端本身），且有封号与合规风险。**明确排除** |
| 微信开放社区 / cnb.cool #2446 | 补充事实：缓存旧 SDK 是根因之一；**官方 SDK 已修复**；Edge 143 手动放行仍有人不生效 |

**决定：继续用自己的扩展 + 兜底工具，不换别人的。** 理由是这个细分场景没有现成的通用实现，而自己这套覆盖 `<all_urls>`、`document_start` + MutationObserver、三个权限名、DNR no-cache、诊断页和微信登录专用对接；换掉反而会丢能力。人家长处按上表吸收了 8 项，其中 1 项是修自己的 bug。

## 实测结果（都是本机真跑的）

| 验证 | 命令 | 结果 |
|---|---|---|
| JS 语法 / JSON | `node --check extension/*.js` | 5 个文件全 OK；manifest mv3、rules 3 条 |
| 补丁逻辑单测 | `node --test extension/test/patch.test.js` | **10/10 通过** |
| 修复前/后 DOM diff | 单测顶部输出 | `allow` 从 `(未设置)` → `local-network-access; loopback-network; local-network`；`src` 无变化 |
| 二维码识别自测 | `python desktop-scan/scan_helper.py --selftest` | 负例 0 码 → 正例解出原文 → 难例（反色+模糊）新引擎 OK / 旧引擎 FAIL → 真实登录码闭环（分类/裁切 90×91 → 放大 450×455 → 二次解码一致），**四轮全通过** |
| 引擎回归对比 | `python desktop-scan/scan_helper.py --bench` | **修复前 7/10 → 修复后 10/10**，净增 3 个场景（模糊5、反色、小图） |
| 本机通道诊断 | `python diagnose.py --save` | 微信 4.1.15.56 / Edge 157 / 14013 在监听 |
| **端到端（真实浏览器）** | `python evidence/e2e_headless.py` | **8/8 PASS**：Edge headless 加载扩展，同步+异步登录 iframe 都补 allow、自动重载完成且 src 恢复、普通 iframe 未误改、状态条注入 |
| 桌面一键窗自检 | `wxscan.py --selfcheck <截图>` | **5/5 PASS**：真实截图识别并渲染 / 二维码图 ≥400px / 微信主窗口可定位 / 剪贴板写入 / payload 分类；控件逐项检查零越界 |
| 桌面一键窗真实启动 | `pythonw wxscan.py --image <截图>` | 窗口出现、标题正确、内容完整（截图见 `evidence/gui_preview.png`），进程已清理 |

端到端 dump-dom 的修复前后对比（原文见 `evidence/e2e_result.txt`，mock 页面三个 iframe 起初都没有 allow）：

```
修复前: <iframe id="wxlogin" src="...qrconnect...">                         <- 无 allow
修复后: <iframe id="wxlogin" src="...qrconnect..."
         allow="local-network-access; loopback-network; local-network"
         data-wxql-patched="1">
        <iframe id="ad" src="https://example.com/ad.html">                  <- 对照组，未被触碰
```

单测过程中修掉两个真实缺陷（不是改测试迁就实现）：`hasFastLoginDisabled()` 误报普通页面、识别正则用 `fast_login=` 当特征太宽。诊断器也修过一次：初版依赖 `powershell` 命令，在 Git Bash 下不在 PATH，导致**把探测器失败错误写成了「微信未运行」的事实结论**；已改成纯 Win32 API 并对探测失败标"未知"。错误的旧报告保留在 `evidence/diagnose_20261109_171225.txt`（顶部已加说明，内容未篡改），正确报告是 `evidence/diagnose_20261109_171452.txt`。

## 边界（不吹的部分）

- 真实第三方网站的登录端到端实测 **2026-11-10 补做了一部分**：腾讯文档登录页已实测出现头像 +「微信快捷登录」（免扫码通道打通），但点击后的微信客户端确认未弹出、网页未跳转，登录**尚未完成**。详见文末「补记」。
- 桌面客户端（Marvis）那张截图**只做到「识别 → 裁切 → 放大 → 二次解码」闭环**。（2026-11-10 用户已自行登录 Marvis，桌面端不再需要处理；「PC 微信右键识别登录码后到底弹什么」本机仍未验证。）
- 扩展**不绕过微信安全模型**：不模拟手机、不伪造授权、不碰账号/Cookie/聊天内容。快速登录是微信官方能力，确认动作仍由"已登录的微信会话"发出，只不过这个会话在电脑上。
- 能不能用取决于三件事：网站是否接入（且 SDK 是否新版）、PC 微信是否登录且未锁定、浏览器权限是否放行。任一不满足仍会退回扫码。
- TLS 直连失败是微信内部协议所致，**不作为通道可用性判据**。
- Greasy Fork 脚本**完整源码未取到**（web read 两次 TIMEOUT），Chrome 官方 LNA 博客同样超时；规范细节是用 MDN + Chrome 145 Release Notes + Chrome Platform Status 交叉核对的。`quickscan` 的许可证未核实。
- 本项目没有引入任何第三方代码，只借鉴了公开实现/规范里的方法与判断逻辑。
- 不建议的极端做法：`edge://flags/#local-network-access-check` 设成 Disabled 可全局关掉检查，但会降低浏览器对所有本地设备访问的安全防护。优先按站点放行。企业环境可考虑 Chrome 146 的 `LocalNetworkAccessPermissionsPolicyDefaultEnabled` 策略，本项目没改任何系统配置。

## 依赖

- 扩展：零依赖，Edge/Chrome 直接加载。
- `diagnose.py`：纯 Python 标准库（Win32 API via ctypes）。
- `desktop-scan/scan_helper.py`：`opencv-python`(含 contrib 的 wechat_qrcode) / `Pillow` / `numpy`（本机已具备）。

全部本地运行，不联网上传任何数据。

---

## 补记：2026-11-10 真实登录实测（含两处纠错）

### 实测到的好消息：浏览器侧免扫码通道真的打通了

隔夜后现场全变（Edge 被关、微信/Marvis 缩在托盘、屏幕无码）。重新打开腾讯文档桌面版登录页
`https://docs.qq.com/desktop/`，走完两步前置交互：点「立即登录」→ 弹「服务协议和隐私政策」→ 点「同意」，
页面从**二维码 / 无码登录面板**变成：

```
头像「某昵称（已脱敏）」 + 绿色「微信快捷登录」按钮 + 「使用其他头像、昵称或账号」
扩展状态条：权限已放行 · 请核对微信客户端状态
```

这正是本项目要修出来的目标状态（官方「网站应用快速登录」）。证据：
`evidence/day2_login_refreshed.png`（面板，无码）→ `evidence/day2_after_click_login.png`（协议框）
→ `evidence/day2_after_agree.png`（头像 + 快捷登录）。

### 仍未完成的一步：点击后微信客户端没弹确认

官方文档（2026-05-27 修订）原文：「**快速登录无需扫码，可直接在 Windows / Mac 设备上进行确认**」，
前置是「用户此时在 Windows / Mac 设备已经登录了符合要求的微信客户端，且处于**非锁定状态**」。

本机实测：连点 4 次「微信快捷登录」，**微信主窗口确实被唤起**（从最小化变为可见，
rect 从 `(-32000,-32000) 237x39` 变为正常 `2427x1057`），但：

- 8 秒高频（0.6s/帧）顶层窗口枚举：**无任何新窗口**；
- 微信主窗口子窗口枚举（含 `MMUIRenderSubWindowHW`）：点击前后都是 2 个，**无新增**；
- `PrintWindow` 抓微信自身内容 + 全屏截图：均为普通聊天界面，**无确认浮层/卡片**；
- 网页始终停在「头像 + 快捷登录」，**未跳转**。

证据：`evidence/day2_wechat_confirm.png`、`day2_wechat_now.png`、
`day2_wechat_front_after_click.png`、`day2_page_now.png`、`day2_final_screen.png`。

排查方向（未逐项验证，不下结论）：本机有 Karing/代理类软件（开放社区同类案例有「关梯子就好了」）、
PC 微信是否处于锁定态、站点 `fast_login` 参数、微信 4.1.x 客户端对该能力的支持差异。

### 两处纠错（昨晚记忆里判重了）

1. **「腾讯文档状态条注入了却仍是二维码」不是扩展 bug**。今天走完「立即登录 → 同意协议」后
   就出现了头像 + 快捷登录；昨晚那个页面只是登录流程的**中间态**，撤回该 bug 结论。
2. **「微信最小化时工具拉不前台」没能复现失败**。`evidence/test_wechat_foreground.py`
   构造「微信最小化 + 宿主置顶占前台」干扰场景，内联旧实现与新实现各跑一轮，**两轮都成功抢到前台**。
   昨晚粘贴落空的主因是**跨多次工具调用之间前台被 WorkBuddy / Marvis 抢回**，不是工具单次调用缺陷。
   因此本次 `wxscan.py` 修改定位为**健壮性加固**，不宣称修好了可复现故障。

### 加固内容与回归

`wxscan.py` 新增 6 项校验：`IsIconic` 最小化检测、`GetForegroundWindow()` 结果校验、
临时 `SetWindowPos(TOPMOST)`、ALT 解锁前台锁定并重试、用完 `release_wechat_topmost()` 取消置顶、
失败如实返回 `False`（旧实现吞异常且恒返回 `True`）。`one_click()` 改为**先取消本窗置顶再拉微信**
（旧顺序会让置顶的小窗抢走键盘焦点），并在没抢到前台时不再盲目发 Ctrl+V。
两处副本已同步（桌面 `wxscan/wxscan.py` 与项目 `desktop-scan/wxscan.py`）。

回归实跑：扩展单测 **10/10**、扫码链路四轮自测全过、桌面一键窗自检 **5/5**、
真实 Edge 端到端 **8/8 PASS**、前台加固对比 **PASS（两轮均生效，6 项校验落地）**。
证据：`evidence/fix_foreground_diff.txt`。

### 本轮未做

- 没有代用户在微信客户端点「确认登录」（该动作属于账号授权，且本机根本没弹出确认界面）。
- 没有用用户账号做完整登录闭环，因此「腾讯文档已登录」**不成立**，页面仍在登录页。
- 没有验证「PC 微信右键识别 `connect/confirm` 登录码后弹授权页还是链接」这一决定性实验
  （今天页面不再显示二维码，改用快捷登录，故没有可用码图）。

---

## 补记二：2026-11-10 风鸟场景、一次操作事故与交接文档

- 风鸟登录弹窗的码解出为
  `https://m.riskbird.com/h5/pages/share/download/download?qrCode=fn-login-qrcode-7A18173092284F079617FD93EB35C567`，
  **不是微信登录码，是业务方自建 H5 落地页**。普通 Edge 打开只有推广页、无确认登录（伪装 MicroMessenger UA 的
  HTTP GET 同样只跳推广页）；该 H5 靠微信环境判断分支，浏览器侧路径不通。风鸟登录**未完成**。
- **操作事故（用户已明确禁止）**：曾试图「把链接发进微信文件传输助手，用微信内置浏览器打开＝等价扫码」，
  用固定相对坐标点会话列表时窗口 rect 已变，未切换会话，链接被误发到「某课程班群 (35)」，
  三次右键撤回均失败、2 分钟时限已过，**未能撤回**。证据 `evidence/day2_wx_link_sent.png`、
  `day2_recall_menu*.png`。现场已还原（窗口/置顶/用户剪贴板）。
- **硬规则**：❌ 不再用 GUI 自动化往微信聊天里发送任何消息/链接/图片（含文件传输助手）；
  ❌ 不自动点「发送 / 确认授权 / 同意」；✅ 只做截屏、解码、本地存图、显示窗口等只读动作；
  任何点击前重新 `GetWindowRect` + 截图核对，禁止复用坐标。
  这条错的不只是「坐标点歪」，而是**不可逆外发 + 聊天点链接≠扫一扫 + 授权不可代做**三层问题。
- 三个真实登录场景（腾讯文档 / Marvis / 风鸟）**目前均未由本项目完成登录**；Marvis 是用户自己登录的。
  工具全绿 ≠ 端到端可用，别把 10/10 单测、8/8 e2e 当成登录成功。

> **给其他 AI 的自包含交接文档：`HANDOFF.md`**（含全部经过、真实结论、事故复盘、未解卡点、
> Q1~Q5 思考题、已排除方案、可直接复制的提问模板与证据索引）。拷走这一个文件即可，无需历史对话。

### 12:24 更正（重要，旧结论留痕不改）

另一个 AI（豆包）在 11:29–11:41 做了真实浏览器实验，结论在 `复核答复-快捷登录已破案.md`，
我方逐行核对结果在 `HANDOFF.md` 末尾「附：对复核答复的核对」。**据此撤回上面「微信被唤起但无确认浮层」
这一条**：

- 之前按屏幕坐标点「微信快捷登录」**大概率没点中**（按钮在跨域 iframe 里，同名元素 3 个、2 个不可见），
  因此从未发出 `POST /api/authorize`，微信自然没卡片可弹；精确点中后请求立刻发出、微信窗口内
  弹出「视频号创作平台 申请使用 你的昵称、头像 / [允许][拒绝]」（`evidence/day3_wechat_authorize_card*.png`）。
- 卡片是**微信主窗口内部浮层**，不是新顶层窗口 → 原来的顶层窗口枚举 / `PrintWindow` 抓拍方法也不对口。
- 端口口径修正：SDK 只探测 `14013/14014/14015/13013/13014/13015`，仅 14013 返回 200；
  诊断器里的 14016/14019/14022/14023 虽在监听但**不在探测链**。
- 新增坑：**新 profile 第一次用必须有一次真实用户手势放行 LNA 权限**，否则页面回退纯二维码。
- `authorize` 发出后 pending 是正常态（等人点允许）；`ERR_SSL_PROTOCOL_ERROR` 才是异常。
- **登录仍未完成**：所有实验均超时、无人点「允许」。按验收标准，出现头像、弹卡、测试全绿都不算成功。
  唯一闭环是用户本人在日常 Edge 里点快捷登录、再在微信里点「允许」。

> 纠错过程按纪律写在 `HANDOFF.md` 附录，旧结论原文保留不删；证据文件 `evidence/day3_*`、
> `probe_20261110_11*.txt`、`complete_*.log` 全部保留原样。

## 补记三：2026-11-10 晚 闭环成功与一键 bat（Qoder）

- **牛客、百职帮均已真实登录成功**（截图见 `evidence/day4_loggedin_*_20261110_18*.png`）。
- 用法：双击桌面 `扫码登录.bat` → **不问话，直接扫屏幕上露出的第一个二维码**（任何网页都行）→
  微信弹结果窗；需确认的登录码由**本人**点；成功黑窗自动关闭，失败才停留看错误。
  牛客/百职帮只是验收测试站（CLI `--site nowcoder/baicizhan` 可回归）。
- 实现主本 `qoder/wx_quick_login.py`（桌面 `wxscan/` 为部署副本）。旧 `wxscan.py` 的剪贴板半外发链路
  不再被 bat 调用；保留作只读识别工具。
- 原理与坑详见 `HANDOFF.md`「2026-11-10 18:20 Qoder 闭环」。

## 补记四：2026-11-10 19:40 被"制裁"的码也闭环了·第二个桌面 bat（Qoder）

- 桌面双击入口只有一个：`扫码登录.bat`——屏幕上露出的码直接扫（`weixin.qq.com/q/` 公众号登录码等 type-A 码），不问话。
- type-B 网站应用码（`open.weixin.qq.com/connect/…`，如数控驿站）**不需要脚本**：这是微信原生功能——
  直接在网站登录弹窗里点「微信快捷登录」，微信弹授权卡，本人点「允许」即登录。
  曾做过桌面 `微信快捷登录.bat`，当日撤销（脚本只是破案/应急工具，主本留 `qoder/quick_login_flow.py`，
  自带 localhost.weixin 请求日志，按钮不出时可拿它定位是代理、LNA 还是应用级限制）。
- type-B 码在电脑上识码必跳"该二维码仅支持在扫一扫时,通过摄像头来识别"——这是微信服务端限制，
  不是工具坏了。电脑免手机的正路是弹窗里官方「微信快捷登录」按钮（LNA 握手），以前不出现是三个坑叠加：
  ① 系统代理（Karing）劫持了浏览器对 localhost 的探测；② Edge 142+ 静默拦截本地网络探测；
  ③ 微信本地接口只认浏览器进程直连（curl/Python 直探永远被拒）。
  脚本在**临时浏览器进程内**用 `--no-proxy-server` + LNA 放行参数一次解决，不改任何日常配置，
  **代理可以保持开启**。
- 唯一人工步骤：授权卡上的「允许」**本人点**，任何脚本不代点。
  若点完快捷登录后卡一直不出：重试一次；仍不出就临时把系统代理关掉再点一次（实测成功那次代理是关的），
  或用手机摄像头扫码。
- 证据：`evidence/day4_quick_loggedin_sk1z_20261110_192444.png`（数控驿站已登录态）、
  `evidence/day4_quick_sk1z.json`。机制与根因详见 `HANDOFF.md`「19:24 type-B 闭环」节。
- 红线不变：不往微信聊天发任何东西；不代点发送/允许；最终授权本人完成。
