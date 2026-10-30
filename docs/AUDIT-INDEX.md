# 审计、验证与历史证据索引

仓库发布保留Markdown报告和可复现Python脚本，不包含原始JSON/txt日志、profile、数据库、截图或凭据。因此旧文档提到的旁边JSON/txt可能只在原机存在，不是Git克隆损坏。限定结论须与相应阶段/源码版本对应，不能无限继承到新修改。

## 阶段地图

| 阶段 | 仓库内资料 | 当时结果与适用范围 |
| --- | --- | --- |
| 初始实现 | [TDD-CORE](../TDD-CORE.md)、[TDD-CLIENT](../TDD-CLIENT.md)、[TDD-PARENT](../TDD-PARENT.md) | 初版接口/RED-GREEN记录，可能被后续实现替代 |
| 第一轮发送竞态 | [VERIFICATION历史部分](../VERIFICATION.md) | 当时102项；只覆盖当时发送路径，不是当前总数 |
| 全面重新审计 | [AUDIT-REPORT](../audits/20261029-080759/AUDIT-REPORT.md) | 固定上游0.4.6/8b0009f...，本地102项/上游选定43项通过仍发现新问题 |
| 核心/客户端/适配修复 | [FIX-REPORT](../audits/fix-20261029-125129/FIX-REPORT.md)、[TDD-FIX-CORE](../TDD-FIX-CORE.md)、[TDD-FIX-CLIENT](../TDD-FIX-CLIENT.md)、[TDD-FIX-PARENT](../TDD-FIX-PARENT.md) | 一度134项，复审指出两项deadline后修到137；限定复核通过 |
| 签名字幕/播放与安全备用 | [REPORT](../audits/remaining-fixes/REPORT.md)、[compat-review-repro](../audits/remaining-fixes/compat-review-repro.py) | 当前插件144项；独立真实SQLite5项/30子场景；线上兼容仍未验 |
| 人工远程浏览器 | [README](../remote_browser/README.md)、test_session.py、verify_live.py | 2项参数策略测试；真实HTTP/WS/RFB/Origin/端口及中文曾验证，用户端SSH未验 |
| 私有仓库发布 | [HANDOFF](../HANDOFF.md)、[VERIFICATION](../VERIFICATION.md) | 功能代码64d2ed0，55文件快照卫生独立审查通过，逐blob读回并重新克隆核验；不包含账号秘密 |

上游固定引用：https://github.com/Alterssss/dsh-bili-whale/tree/8b0009f8ce2ebdc421afcb2e727b19ac18912ec6 。本地是独立实现，不自动继承上游全部能力。

## 脚本分类：不要把历史复现当全量测试

### 当前适用的补充回归

- [compat-review-repro.py](../audits/remaining-fixes/compat-review-repro.py)：真实SQLite完整字幕预算、各阶段暂停、主错误不备用、一次备用、完整URL和无Cookie；预期5项/30子场景。
- [media-repro.py](../audits/fix-20261029-125129/media-repro.py)：当时复审媒体4项；仅有当时通过记录，接力文档本轮未重跑，使用前核对环境与版本。

### 历史缺陷探针

- [replay-fixed.py](../audits/fix-20261029-125129/replay-fixed.py)曾对修复期有效；本轮执行发现其字幕暂停fixture没有新增nav签名响应，先报WBI signing unavailable，导致预期paused断言失败。它不再是当前全绿验收入口，需单独更新fixture并确认仍测CDN阶段；本次只修正文档分类，不改脚本/生产保护。
- audits/20261029-080759/core-repros.py、client-repros.py、parent-repros.py复现当时旧行为；某些断言期望漏洞存在，修复后异常/失败可能正说明保护生效。
- core-client-review-repro.py还含无socket的自定义响应deadline演示；其38秒模拟不能否定当前标准库HTTPResponse每次read1前剩余socket设置。不要为了让旧演示返回20秒而伪造非生产对象能力。

### 写入与环境依赖

- verify-offline.py在当前仓库.venv/Hermes解释器/Linux/TMPDIR前提下重跑全套和隔离安装，会写被忽略日志。
- 部分历史审计脚本使用原机绝对路径/原布局，运行前完整阅读，别把“所有.py”一键执行当验证；不要让旧缺陷探针触及真实profile。
- verify_live.py要求运行中的本地远程浏览器和websockets，读取画面/写截图，不在密码输入期间运行。

## 未入Git的审查证据

当时的JSON报告包含源码SHA256、命令、范围及exclusions，原始证据保留本机开发目录或发布scratch；scratch可能清理。Markdown中保留的是当时报告摘要，不应在缺JSON时冒充可独立验证精确哈希。接力者可以从当前commit自行重跑标准tests/上述补充探针，生成新的本地证据，并在VERIFICATION记录来源。

尤其远程浏览器独立报告早于最后内存优化参数，最后参数版本仅由父执行者复核，不能表述为“最新精确版本独立审查”。本次文档仅整理既有证据，不扩大范围。

## 后续记录模板

    基线commit / 工作树：
    请求范围 / 明确不做：
    缺陷最小复现与预期：
    RED命令 / 实际错误：
    GREEN命令 / 实际结果：
    全量数量 / skip / 依赖：
    真实外部动作与读回目标（如有授权）：
    独立审查文件哈希 / 范围 / 父重放：
    未验证与下一步：

不记录secret、OTP、Cookie、完整临时签名URL。给出“未联网/未外发”比把fixture伪装线上结果更重要。
