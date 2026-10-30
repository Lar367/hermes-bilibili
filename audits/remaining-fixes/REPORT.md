# 字幕与播放签名接口：本轮修复（限定独立审查通过）

## 范围与结论

此前137项离线测试全绿，本轮继续处理审计CLIENT-06的代码路径差异：字幕从旧player/v2改成player/wbi/v2，播放从旧player/playurl改成player/wbi/playurl。没有真实B站或账号访问，因此CLIENT-06的线上兼容性仍未关闭，不把换端点等同线上验收。

源码参考为已固定上游dsh-bili-whale 0.4.6 / 8b0009f8ce2ebdc421afcb2e727b19ac18912ec6：tools/commands/subtitles.mjs 58–70说明签名字幕路径与空字幕备用接口；tools/lib/bili-api.mjs 148–158说明播放签名路径。引用用于实现对齐，不是当前服务契约的独立证明。

## 实现

- client.py：抽出搜索已有WBI签名逻辑为_signed_request，搜索行为不变；字幕元数据走/x/player/wbi/v2。
- 只在主接口成功且subtitle.subtitles为合法空list时，尝试一次/x/v2/subtitle/web/view。缺字段、错类型、HTTP/API风险、鉴权、非JSON或传输异常直接失败，不重试、不绕过暂停。
- 字幕保留完整auth_key URL，仍做精确HTTPS/CDN验证；CDN下载无Cookie。新增source字段，不落库签名URL。
- media.py：改用签名播放接口，低清/单文件MP4、大小、时长、mov白名单、目录与清理保护保持不变。
- 所有新增nav和备用请求使用原dispatch，每个请求独立计预算、等待后复查暂停，不能把一轮多请求看成一次预算。

## 实际TDD证据

1. test_subtitles_use_signed_player_metadata：原实现仅调用/x/player/v2，对预期nav+/x/player/wbi/v2断言失败；抽出签名请求并切换后通过。
2. test_valid_empty_primary_uses_subtitle_fallback：available=False断言失败；加入一次受限备用请求后通过。
3. test_malformed_primary_metadata_never_triggers_fallback：{}和subtitle:{}被当成合法空结果两次断言失败；去掉缺字段的[]默认值后通过。
4. test_media中真实本地ffmpeg抽帧：对/x/player/wbi/playurl断言失败（实际旧路径）；切换后通过。
5. 额外防回归覆盖：风险/鉴权/HTML/传输异常不备用；备用等待中暂停不发请求；真实SQLite read_daily=2在nav和主元数据后阻止第三个备用请求；真实BiliClient签名播放并以本地ffmpeg生成PNG。

既有字幕fixture追加nav响应与新请求数量，原无Cookie/外域拒绝/时间戳拒绝断言不放松。旧暂停测试从第2次等待改为第3次，确保测的仍是CDN派发而非新nav后元数据派发。

## 父agent验证

执行：python audits/remaining-fixes/verify-offline.py，全部exit0。
- 完整144项unittest通过，无跳过；测试父进程禁DNS及socket连接。原始full-tests.txt。
- 新增7项compat测试全部通过，包含真实本地ffmpeg/ffprobe；原已有Node编码向量仍通过。
- 临时HOME/HERMES_HOME隔离安装、doctor、validate、enable、公开CLI list、已安装副本status均通过。status明确writes_enabled=false。
- 实际安装路径：/home/admin/.hermes/cache/scratch/bili-compat-host-rc4fsmio/plugins/hermes-bilibili；已比对该副本client/media/README与当前源码逐字节一致。
- verification.json记录7条执行结果，post-fix-hashes.json记录7份变更源码/测试/README指纹。
- git diff --check无错误。有限shell=True/eval/exec/pickle/动态f-string SQL扫描无匹配，不是全面静态证明。

默认profile、真实凭据及Git暂存未改；未提交或推送。运行时仍stdlib-only，测试PyYAML来自项目.venv，不修改Hermes管理环境。

## 保留边界

真实签名端点/AI字幕凭据需求/CDN头与区域兼容性仍待明确授权的只读线上验收。OS沙箱、全局跨目录/跨并发磁盘配额、DNS/connect/TLS硬墙钟中断、强杀在途guard人工恢复等边界没有在本轮偷偷放宽或宣称完成。

## 独立审查

限定独立审查通过（compat-review.json，passed=true、security_concerns=[]、logic_errors=[]）。独立执行全套144项通过，并新增真实SQLite探针5项/30个子场景通过：未缓存完整字幕链路预算0–5、五个等待阶段由独立SQLite连接施加暂停、14种主接口失败不备用、备用只尝试一次、完整auth_key URL与无Cookie边界。

父agent已亲自重跑同一compat-review-repro.py，5项/30子场景全部通过（parent-compat-repro.txt），核对8份审阅源码/测试/README的SHA256与当前文件全部一致；再次解析完整144项日志与隔离宿主7条命令日志，均通过且外发关闭。未修改被审查源码。

非阻断建议：把独立SQLite探针中的更多阶段案例并入正式tests，并为新端点补冻结时钟的精确签名断言；现有Node编码向量已覆盖签名原语。局部FakeStore/替换媒体下载的fixture不单独证明生产全栈安全。结论仅适用于本轮签名接线与受限备用流程，不认证全仓；CLIENT-06线上兼容性仍明确未关闭。
