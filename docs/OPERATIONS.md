# 安装、安全运维与恢复

先在 [DEVELOPMENT](DEVELOPMENT.md) 的临时profile验证；没有用户明确要求，不把生产默认profile启用作为测试步骤。以下路径以当前 `$HERMES_HOME` 为准，不混入其他profile。

## 原生插件安装与更新

- 源码仓库需要根__init__.py、plugin.yaml及skills，不是只有Python包。
- 从仓库用Hermes解释器执行 `python scripts/install_local.py`，复制到当前profile/plugins/hermes-bilibili；拒绝已有目录和symlink，不改配置、不启用、不登录。
- doctor/validate检查载荷，再由用户显式enable。工具变更通常新会话才加载，不在运行中篡改工具集。
- 更新前停止相关任务、保留源码版本和插件数据；安装器不提供原地覆盖/升级机制。不要删除state.db来“重新安装”。换版本目录/回滚方案需先检查schema兼容性。
- 使用 `hermes config set` 管理普通设置；凭据仅安全界面/secret scope。不要手改YAML或把Cookie加在CLI参数。

## 凭据与人工登录

BILIBILI_SESSDATA、BILIBILI_JCT、BILIBILI_BUVID3由宿主管理。插件不读取其他应用账号文件，也不支持邮箱密码直接登录。vault安全填网页密码不等于插件已获得Cookie；远程浏览器profile、插件secret scope、GitHub CLI授权各自独立。

遇CAPTCHA或邮箱/短信验证由用户在安全输入界面完成；不在聊天收集OTP、不自动解图、不反复重试风控登录。不要将带auth_key的短期URL写入日志。发现聊天泄露的凭据建议更换，不记录原值。

## 状态与安全开关

- status是本地只读诊断，但可能创建本地空数据库；应在正确profile运行。
- `writes_enabled=false`是默认，禁止外发，不意味着禁止读取或准备草稿。
- `/bili resume UID`仅解除人工标志，保留平台定时退避，不清global guard。`write_in_flight=true`可能是GET在途，不是已发评论证明。
- `sending/unknown/unverified`是未决，不是失败重试队列；`confirmed`才有精确评论回读。DM没有可靠自动送达证明。
- 账号身份漂移、timezone冲突、坏schema/存储错误须保留证据并停操作；不要换匿名账户/删identity/改预算绕过。

## 中断恢复流程

1. 先停止本项目任务/定时入口，确认是否仍有在途进程；不要停止无关服务。
2. 读取状态计数/operation/草稿记录，区分人工暂停、定时暂停、running周期、持久guard；诊断输出避免正文/私密记忆/凭据泄露。
3. 可核查remote_id的操作仅做verify，不send。没有remote_id时不推断请求一定没到服务端。
4. 仅“阅读周期”卡running时，用户人工核查后可 `/bili cycle-abort RUN_ID "人工核查原因"`；当天不会重跑，也不解除派发guard。
5. 强杀留下guard目前没有安全恢复工具。保持停止并走 [ROADMAP](ROADMAP.md) 的操作员设计，不执行通用删表/清锁SQL。
6. 调整代码/配置前备份；恢复后先隔离离线测试，再经授权做只读验证，外发独立批准。

这不是自动恢复保证。网络请求已在途后不能撤回，数据库事务不能和远程B站形成分布式原子事务。

## 备份与回滚

需要保留state.db和cycle.db、persona.md及相关schema/代码版本；frames按需求选择，不备份短暂下载。不要将数据库上传仓库，即使目录private也一样。

优先停止本项目写入，使用SQLite在线backup API或在所有连接关闭后复制数据库及仍存在的WAL/SHM/journal。仅拷贝活跃WAL数据库的主文件可能丢数据。state/cycle是两个库，要获得一致快照应停本项目全部任务后备份；单库backup不是跨库一致性。目录700、文件600，异机备份需额外访问控制/加密。

恢复前保存现库并在复制件做quick_check/schema检查；不要把version1库直接塞给不理解该schema的旧代码。身份指纹和审批/未决账本也要保留，否则可能失去暂停/去重关联。

## 远程浏览器

详细说明：[remote_browser/README.md](../remote_browser/README.md)。仅适合可信单用户Linux服务器+SSH；网页本身无额外密码，Origin不是身份认证。客户端SSH认证/隧道加密、loopback监听与同机用户信任构成边界；不适合多租户。

当前脚本限制：依赖Xvfb/xauth/x11vnc/websockify/noVNC、Chromium既有路径、Linux/run/user，display93和端口6080/9223固定。服务最长期限和内存属性来自systemd-run命令，不是只运行session.py就拥有全部限制。用户端SSH尚未实际验收；一小时退出也未等待实测。

每次启动实际执行ss验证：noVNC/CDP只127.0.0.1，VNC无TCP（尤其IPv6），Xauthority/socket权限600。若发现wildcard马上停自己服务。不要将CDP或VNC转发公网、不要关Chromium sandbox。浏览器profile存登录态，不上传、不自动删除；停止服务不等于登出。

`verify_live.py`会读取当前画面并保存截图，属于人工授权后的调试；不要在用户填密码或验证码时运行。它不自动输入、不登录。2项启动策略测试只检查命令，不替代真实监听/清理验收。

## GitHub协作

仓库私有，协作者需由owner授予访问。授权GitHub CLI/SSH属于独立权限；本机gh可能以600文件存明文token，不是密钥库加密。不要显示令牌、上传hosts.yml、把token放remote URL，离开机器前按用户要求撤销授权。

当前推荐工作副本在HANDOFF列出；旧原目录staging须保留。常规用feature分支/小提交、运行测试、精确stage，推送后读回目标。不要因为私有仓库就提交profile/log/密码。
