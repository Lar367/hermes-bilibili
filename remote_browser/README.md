# 电脑远程接管B站登录

这是独立运维辅助入口，不自动启用B站插件，不解验证码，不自动发布。

## 在你的电脑连接

使用平常连接服务器的SSH主机名/IP、密钥及端口。以下SERVER替换成你的现有SSH目标；不是浏览器地址中的127.0.0.1。

    ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:6080:127.0.0.1:6080 admin@SERVER

如平时用-p或-i参数，照常添加。保留此终端运行，再用电脑浏览器打开：

    http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale

这是服务器上新建的专用Chrome，不是你电脑的浏览器，也不是之前临时headless会话。请自行在画面中登录和完成验证；密码不需要发到聊天。不要选择保存密码，登录会话本身保存在专用profile。完成后告诉Hermes“远程浏览器已登录”。目前未自动连接Hermes浏览器工具，也未将Cookie导入B站插件。

无需额外VNC密码：入口认证由你的SSH负责；网络传输经SSH加密。HTTP只在两端loopback，绝不能把6080/9223或VNC映射到公网。不建议改本地转发端口，因为WebSocket Origin当前仅允许6080。

## 当前部署

- user transient unit: bili-browser-takeover.service，不开机自启。
- 入口127.0.0.1:6080；CDP127.0.0.1:9223（不转发给电脑）。
- x11vnc无TCP监听，使用/run/user/1001/bili-browser-takeover/vnc.sock，目录700/socket600。
- Xvfb :93，MIT cookie认证，禁TCP；无其他应用/终端桌面。
- 专用profile：/home/admin/.local/share/hermes-bilibili-browser，目录700，不自动删除。登录态仍是敏感数据，权限不是加密。
- 剪贴板同步和VNC远程命令关闭，文件传输未启用。
- 浏览器sandbox保留。限制扩展/GPU/后台功能和renderer数量以降低此小内存主机压力，不是完整OS沙箱。
- 当前unit MemoryHigh=450M、MemoryMax=650M；达到内存压力可能导致页面/会话退出，不能保证永不OOM。
- 脚本1小时后退出，systemd最迟3700秒关闭整个进程组；超时不是清除登录态。关闭你的SSH隧道不自动停服务器浏览器。

停止：

    systemctl --user stop bili-browser-takeover

重新启动（服务器终端，当前Hermes Python可用时）：

    systemd-run --user --unit=bili-browser-takeover --collect --property=RuntimeMaxSec=3700 --property=TimeoutStopSec=35 --property=UMask=0077 --property=KillMode=control-group --property=MemoryHigh=450M --property=MemoryMax=650M "$(python -c 'import sys; print(sys.executable)')" "$PWD/remote_browser/session.py"

上面从当前仓库根执行，不再指向早期原开发副本。Chromium探测路径、Linux显示/UID目录和固定端口等限制仍在，迁移前看 [接力运维说明](../docs/OPERATIONS.md)。`verify_live.py`额外依赖websockets，不属于插件stdlib依赖；会请求当前画面，不要在输入密码/验证码时运行。

服务的active状态是运行时事实，不随Git保存。本节“当前部署”为原机已验证配置，不表示克隆后服务自动启动；2026-10-30交接开始时两项辅助浏览器服务均已停止。

## 真实验证

- 2项启动策略测试通过，原B站插件144项测试通过。
- verify_live.py实际验证HTTP200、WebSocket/RFB初始化1280x800、拒绝错误Origin403、loopback边界及Unix socket权限；未发送键盘/鼠标事件。
- 通过RFB读取实际画面，确认B站登录页和中文正常，无登录提交。
- 首次试运行发现x11vnc尽管-rfbport0/-no6仍监听IPv6 5900，立即停止并修正-rfbportv6 0/-noipv6，现ss无VNC TCP。不能把初始配置视为通过。
- 试运行遇内存OOM杀renderer，实核对内核记录后减少浏览器后台负担并加unit内存边界；已重启并验证实际页面。不是删除他人进程或关闭安全沙箱来维持运行。
- 未开放防火墙、未改SSH配置、未改默认Hermes配置或凭据、未读取Cookie。
- 独立review.json通过的是内存优化前的启动脚本与当时真实端口边界；其哈希和当前版本不同，不标成当前精确版本独立认证。后续差异仅为浏览器资源参数及测试断言。父agent对当前版本重跑2项单测、两允许Origin101/恶意及缺失Origin403、真实端口与权限、unit内存/超时参数均通过，见parent-final-verification.json。未从用户电脑测试SSH隧道，也未等待一小时实测退出。

仅same-OS-user/SSH授权用户可信；其他本机用户仍可能访问loopback网页，因此这不是多租户隔离服务。跨站Origin检查不是独立登录认证。不要在不可信共享服务器部署此版本。
