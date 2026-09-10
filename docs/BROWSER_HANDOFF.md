# 专用浏览器与人工接管

后续已打通同页研究分析，自动调用原有估值、未来评估和决策引擎。
当前能力和验收见 [浏览器房源研究闭环](BROWSER_RESEARCH_PIPELINE.md)。下方部署记录保留当时状态。

## 2026-09-08 检测与监控闭环

新增独立实现的被动 DOM / 资源检测，借鉴
[Scrapfly Antibot-Detector](https://github.com/scrapfly/Antibot-Detector) 的分层检测思路，
没有嵌入或复制上游扩展及规则库。检测范围包括 reCAPTCHA、hCaptcha、Turnstile、
GeeTest、网易易盾、腾讯、阿里云验证码、DataDome 和 Cloudflare 的部分页面信号。
这里只输出供应商名称和信号类别，不采集 Cookie、令牌或完整资源 URL。
它不是完整上游引擎，也不能绕过验证码；未知机制仍可能无法识别。

仅加载 SDK 不会阻断房源页；可见的已知验证组件或验证提示会暂停采集。
部分网站保留可见验证组件时仍需人工核对，不能把没有检测结果当作访问许可。
恢复仅检查原页面，成功后以最近成功观测为基线计算首次见到、降价、涨价和不变。
重复确认同一个页面不会制造新观测或抹掉降价记录。失败不覆盖历史记录。

接管首页现在直接显示房源及变化，支持降价与首次见到筛选。`GET /observations`
提供历史房源、变化、过期标识及当前是否可用，读取不触发来源访问。
后台检查由接管服务自身负责，每分钟检查是否到期，来源访问间隔仍为六小时。
不再依赖打开网页或 changedetection.io 触发检查；进程关闭或电脑休眠时不会工作。

### 不依赖 Docker 的本机启动

```bash
uv pip install --python .venv/bin/python -e '.[browser]'
.venv/bin/python -m scripts.local_browser_handoff
```

打开 <http://127.0.0.1:5057/>。默认使用已安装的 Chrome；也可通过
`SHR_BROWSER_EXECUTABLE` 指定 Chrome/Chromium 可执行文件。
点击连接或打开来源页后才会创建浏览器。专用窗口显示在本机，不使用 noVNC。
独立配置和状态位于被 Git 忽略的 `data/local_browser/`；观测仍写入
`data/exports/browser_handoff/`。退出服务会关闭专用窗口；重启后必须重新确认恢复。
与 Docker 接管服务共用 5057 端口，二者只启动一个。

### 本次验证

- 341 项非集成测试通过；本次没有修改数据库逻辑，37 项数据库集成测试未重跑。
- 真实 Chromium 访问自有本机测试页，检测隐藏组件与可见易盾组件、暂停、停止重试、
  人工恢复、280 万到 270 万的价格变化及重复恢复均通过。
- 新增检测及本机浏览器模块、接管模块通过严格类型检查与 Ruff 检查。
- 测试页成功不代表真实房源网站验证成功，也不代表 LIVE 估值与校准已经完成。

### 实际部署验收

2026-09-08 已恢复 Docker 并重建 `handoff` 服务，原浏览器和数据卷保留。
当日 15:12:25（上海时间）读取指定安居客徐汇页面，实际保存 61 条挂牌，
状态为 `READY`。本次页面直接可读，没有完成或绕过任何验证码。
首次观测建立基线，不能把 61 条全部解释为当天新增；后续到期检查再比较价格变化。
房源页位于 <http://127.0.0.1:5057/>，数据范围仍是单页 250–300 万元，
数据契约维持 `sample / partial / private_research_only`，不是已核实 LIVE 市场覆盖。
原有日更流水线继续使用原 SAMPLE 输入，尚未将本次网页观测接入真实估值和校准。
恢复后的 SAMPLE 日更已于 15:14:05 完成补跑，运行
`c1fb752d-ba7c-49c4-be9b-2cb7b72f818f`，健康端点返回 `healthy`，
499 条完成评估，1 条因数据不足跳过。下次计划为 2026-09-09 02:00（上海时间）。
这次补跑读取原有 500 条 SAMPLE 输入，不是上面新采集的 61 条挂牌。

Docker 接入口复用 Selenium 官方可视化 Chromium 和 changedetection.io，不自动解答验证码、不导出 Cookie，也不读取日常 Chrome 的个人配置。仅供本机个人研究。

## 入口

- 接管页：<http://127.0.0.1:5057/>
- 专用浏览器画面：<http://127.0.0.1:7900/?autoconnect=1&resize=scale>
- 变化历史：<http://127.0.0.1:5055/>

在接管页连接浏览器、打开来源页，再进入专用浏览器人工完成网站要求的验证。看到房源列表后，回到接管页点击“验证完成并恢复”。恢复按钮只检查当前页面，不刷新或操作验证页面。

首次来源仅为安居客徐汇 250–300 万的一个筛选页，不代表全市覆盖或完整房源清单。网页条目是真实性未核实的公开挂牌，不是成交信息。

## 状态和会话

- `PAUSED`：不访问来源，包括服务启动及人工暂停时。
- `NEEDS_HUMAN`：网站要求验证、页面不符、卡片不足、解析失败或浏览器不可用。保持原页面，不自动重试来源。
- `READY`：人工确认后的会话允许最多每六小时刷新一次。changedetection.io 只检查本地 `http://handoff:8000/snapshot`，不直接访问来源网站。

控制服务重启会重新暂停，但可重新连接原来的 Selenium 会话。浏览器关闭再开启会使用专用配置卷；网站仍可能让验证过期，不能保证一次验证永久有效。人工刷新后可以再次检查当前页面。超过一小时的旧页面不能被恢复按钮重新盖成新观测。

浏览器面板和应用只发布到 `127.0.0.1`。浏览器画面通过本地代理检查 WebSocket 来源并禁止外站嵌入；WebDriver 端口不发布到主机。不要将这些接口反向代理到公网。页面通知仅在接管页打开且获得浏览器通知权限时有效，尚无离线短信或邮件提醒。

## 存储与边界

- `house-radar-monitor_browser_profile`：专用浏览器配置及网站会话，位于 Docker 命名卷，不进 Git。
- `house-radar-monitor_handoff_state`：状态和私有 WebDriver 会话引用，文件权限 `0600`。
- `data/exports/browser_handoff/observations/`：带观测时间的原始卡片与 Canonical JSON。
- `data/exports/browser_handoff/latest.canonical.json`：最近一次通过校验的研究观测。

`GET /feed` 只提供可用且未过期的最近观测，不触发来源访问；暂停或超过 36 小时返回失败，不返回伪造的空成功结果。数据始终标记 `sample`、`partial`、`private_research_only`。不因未出现在一个页面就判断下架。

变化快照只比较稳定的房源字段，忽略观测时间、排序和跟踪参数，避免每次检查都误报。当前接入口不自动导入生产房源库、不计算“值得看”排名，也不改动 P3/P4/P5/P5.5 或 P5.7 冻结材料。

## 启动

在项目根目录执行：

```bash
docker compose -f docker-compose.monitor.yml build handoff browser
docker compose -f docker-compose.monitor.yml up -d
```

使用已有的 `shanghai-home-radar-api:latest` 提供依赖，浏览器基于官方 `selenium/standalone-chromium`。命名卷要保留；不要用 `down -v`，否则会清除专用会话及监控历史。生产服务使用另一套 Compose 和网络。

在 changedetection.io 中仅配置本地 `/snapshot`，抓取方式使用 HTML/Requests，检查间隔六小时。暂停期间该任务显示 HTTP 503 是预期行为，不会再请求来源。全局暂停及该任务暂停需关闭，计时器才能工作；是否允许访问来源仍由接管页状态决定。

## 验证

```bash
.venv/bin/python -m pytest -q tests/unit/test_browser_handoff.py tests/unit/test_public_monitor_pilot.py
.venv/bin/ruff check scripts/browser_handoff.py tests/unit/test_browser_handoff.py
```

单元测试涵盖人工接管、同会话复用、服务重启、字段失败、旧页面防重盖时间、来源失败停止重试、暂停竞争和本机操作校验。真实浏览器端到端验证必须使用自有本地测试页，不能把本地测试成功当作真实网站验证已通过。

### 本机验证记录 · 2026-09-05

- 23 项接管与研究数据桥接测试通过；完整非集成测试 335 项通过。
- 新文件 Ruff 检查及格式检查通过；接管脚本 strict 类型检查通过，使用 `--follow-imports silent`。不静默检查依赖时，原有 `build_calibration_set.py` 第 419/426 行存在两个类型推断错误，本次未修改该旧脚本。
- 实际 Selenium Chromium 连接自有本地 HTTP 测试页，确认原会话复用、控制器重连、浏览器重启后保留测试 Cookie、测试访问许可失效后停止重试，全部通过。测试未访问真实房源网站，未处理真实验证码。
- 本机 5055/5057/7900 页面均返回 200。外站 Origin 请求浏览器代理返回 403。WebDriver 端口未发布。
- changedetection.io 已配置唯一的本地快照任务 `4baa3978-9063-408e-a52a-af7220994835`，间隔六小时。接管暂停时实际收到 HTTP 503，历史快照数量保持零。
- 四份模型配置及 P5.7 R2 冻结运行清单的 SHA-256 与操作前一致。没有导入生产数据库。

真实来源仍需首次人工打开、验证及恢复。以上结果证明接管机制可用，不证明已开始持续收到新房源或每日机会排名。

## 复用来源

- [Selenium 官方 Docker 浏览器与 noVNC](https://github.com/SeleniumHQ/docker-selenium)
- [changedetection.io](https://github.com/dgtlmoon/changedetection.io)
- 房源提取复用项目现有 `build_calibration_set.normalize_record` 和 `prepare_public_monitor_pilot.prepare`，没有另建估值或筛选模型。
