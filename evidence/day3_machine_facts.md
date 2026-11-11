# 本机事实（2026-11-10 11:2x 实测，PowerShell）

## 进程 / 端口
- 微信客户端：D:\WeChat\Weixin\Weixin.exe（5 个进程），版本 4.1.15.56
- 微信小程序容器 WeChatAppEx 2.5.7.25797（9 个进程）——与本问题无关，别误判
- 监听端口（OwningProcess=微信主进程 1612）：
  127.0.0.1:14013 / 14016 / 14019 / 14022 / 14023

## SDK 实际探测的端口（浏览器抓包，见 probe_*.txt）
- SDK 依次 POST /api/check-login 到 14013、14014、14015、13013、13014、13015
- 只有 14013 返回 200；其余 5 个 net::ERR_CONNECTION_REFUSED
- 结论：**14016/14019/14022/14023 不在 SDK 探测链里**，别再拿它们当"通道在"的证据

## 代理
- ProxyEnable=0（系统代理当前关闭），ProxyServer=127.0.0.1:3067（历史配置）
- ProxyOverride 含 `<-loopback>`：一旦开代理，loopback 会走代理 → 会踩官方文档里的坑
- 只有 clash-verge-service 在跑（服务进程），**无 TUN/Wintun 网卡在线**，默认路由走 WLAN 192.168.31.1
- 结论：本次实验期间**不存在代理劫持 localhost**

## 非浏览器客户端探测（说明为什么 curl/python 探不通）
- 对 5 个端口做 TLS 握手（SNI=localhost.weixin.qq.com）：
  14013 → SSLEOFError；14016/14019/14022/14023 → WinError 10053（本机软件中止连接）
- 官方文档原文：「微信侧会检测发起对 http 请求的进程信息及其签名，如发起请求的进程无签名，会被拦截」
- 结论：**curl/python 探不通不是通道坏，是微信按进程签名/指纹拦掉了**。唯一判据是浏览器里有没有头像+快捷登录

## 浏览器
- Edge 157.0.4341.42