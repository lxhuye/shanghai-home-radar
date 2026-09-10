# 每日自动更新系统配置指南

## 概述

本指南介绍如何配置每日自动数据更新系统，实现：
- 每天凌晨自动拉取全量数据
- 系统自动识别新增、变化、下架
- 自动执行 P3 → P4 → P5 → P5.5
- 持久化当天的排名结果和运行状态

## 架构

```
daily-runner / cron / systemd
  ↓
daily_update_pipeline.py
  ↓
├─ 1. CollectorService（采集数据）
├─ 2. home-radar-market-enqueue (P3 市场基线)
├─ 3. home-radar-valuation-enqueue (P4 估值)
├─ 4. home-radar-future-enqueue (P5 未来评估)
├─ 5. home-radar-decision-enqueue (P5.5 决策)
└─ 6. 生成每日报告与 Top 10 快照
```

流水线使用 Redis 单例锁。手动调用、cron 和 Compose 调度器不会并发执行同一数据模式。
每次运行会在 `data/exports/daily` 写入不可覆盖的运行文件，并原子更新 `latest.json`。

## 前置条件

1. **授权挂牌数据源**
   - 已对接授权 API，或已取得合作方 CSV 导出
   - API 具备认证、分页和全量范围契约；CSV 具备导出总数和使用授权记录

2. **环境配置**
   - Docker Compose 服务正常运行
   - 数据库和 Redis 可访问
   - Workers 正常启动

## 配置步骤

### Step 1: 配置授权数据源

编辑 `.env` 文件：

```bash
# 数据源配置
SHR_COLLECTOR_SOURCE=authorized_api
SHR_COLLECTOR_ENDPOINT=https://api.partner.com/v1/listings
SHR_COLLECTOR_CITY=shanghai

# 认证配置
SHR_COLLECTOR_AUTH_MODE=BEARER_TOKEN
SHR_COLLECTOR_BEARER_TOKEN=your_api_key_here

# 或者使用 Custom Header 认证
# SHR_COLLECTOR_AUTH_MODE=CUSTOM_HEADER
# SHR_COLLECTOR_CUSTOM_HEADER_NAME=X-API-Key
# SHR_COLLECTOR_CUSTOM_HEADER_VALUE=your_api_key_here

# 或者使用 Basic Auth
# SHR_COLLECTOR_AUTH_MODE=BASIC_AUTH
# SHR_COLLECTOR_BASIC_USERNAME=your_username
# SHR_COLLECTOR_BASIC_PASSWORD=your_password

# 数据模式（升级到 LIVE）
SHR_MARKET_DATA_MODE=live

# 请求配置
SHR_COLLECTOR_REQUEST_TIMEOUT_SECONDS=30
SHR_COLLECTOR_MAX_ATTEMPTS=4

# LIVE 完整性安全门
SHR_COLLECTOR_MIN_COMPLETE_ITEMS=1
SHR_COLLECTOR_MIN_COMPLETE_COUNT_RATIO=0.5
```

如果合作方把本次结果声明为完整，但数量低于绝对下限，或不足上一轮完整结果的 50%，
Collector 会把本轮降级为 `partial`。LIVE 流水线随即失败关闭，且不会执行消失和下架协调。

如果合作方暂时只能每日提供 CSV，可改用文件投递入口：

```bash
SHR_COLLECTOR_SOURCE=partner_csv
SHR_COLLECTOR_ENDPOINT=data/inbox/shanghai_listings.csv
SHR_COLLECTOR_AUTH_MODE=NONE
SHR_MARKET_DATA_MODE=live
```

先按 `docs/DATA_SOURCE_INTEGRATION.md` 生成并复核 companion manifest。没有 manifest 的 CSV
只能作为 partial 导入，不会触发下架协调，也不会让 LIVE 每日流水线继续计算。

### LIVE 上线门禁

先为即将构建的源码生成发布身份。该哈希覆盖后端镜像实际复制的源码、配置、迁移和
非运行时数据；`data/raw`、`data/inbox`、`data/exports` 与凭据不会进入哈希。

```bash
python scripts/release_fingerprint.py \
  --write-env .env.release \
  --write-json data/exports/release_identity.json

set -a
source .env.release
set +a
```

工作区有未提交代码时，审计 JSON 会明确记录 `working_tree_dirty=true`，内容哈希仍对应
实际构建内容。门禁会在容器内重新计算哈希并与配置值比较，不接受只填非空占位值。
配置定时任务前，再运行只读门禁：

```bash
python scripts/live_readiness.py
python scripts/live_readiness.py --probe-source
```

第一条命令只检查配置、授权输入、P5.7 结论、投递地址、调度时区和发布指纹。即使这些
静态条件全部满足，它也只返回 `PROBE_REQUIRED`。第二条命令还会读取一次数据源并逐条
标准化，但不会创建 crawl run 或写数据库。只有所有检查和数据源探测都通过时才返回
`READY` 和退出码 0；其他状态均返回退出码 2，不应启用无人值守调度。

`daily-runner` 在 LIVE 模式启动时会自动执行同一只读探测。门禁未通过时，调度器在运行
任何采集或评估前退出；SAMPLE 模式不会触发该上线门禁。

Compose 构建和启动时应显式加载同一份发布身份：

```bash
docker compose --env-file .env --env-file .env.release build
docker compose --env-file .env --env-file .env.release up -d
```

### Step 2: 测试手动执行

在配置定时任务前，先手动测试一次完整流程：

```bash
cd /path/to/HouseRadar

# 激活虚拟环境
source .venv/bin/activate

# 测试执行
python scripts/daily_update_pipeline.py

# 观察输出，确保所有步骤成功
```

预期输出：
```
2026-09-02 02:00:00 - root - INFO - 开始每日更新流程: 2026-09-02 02:00:00
2026-09-02 02:00:05 - root - INFO - Step 1/5: 采集房源数据
2026-09-02 02:01:30 - root - INFO - 采集完成: {...}
2026-09-02 02:01:30 - root - INFO - Step 2/5: 更新市场基线
...
2026-09-02 02:10:00 - root - INFO - 每日更新完成，耗时 600 秒
```

### Step 3: 配置定时任务

推荐直接使用 Compose 常驻调度器：

```bash
SHR_DAILY_RUN_ON_START=true docker compose up -d \
  backup-runner api market-worker valuation-worker future-worker decision-worker daily-runner

docker compose logs -f daily-runner
```

默认使用 `Asia/Shanghai` 时区，每天 `02:00` 执行。首次验收时可设置
`SHR_DAILY_RUN_ON_START=true`，确认成功后改回 `false`。默认开启
`SHR_DAILY_CATCH_UP_ON_START=true`：如果容器在当天计划时间后启动，且今天尚无任何运行记录，
会立即补跑一次；今天已有成功或失败记录时不会再次补跑，避免重启扫描风暴。

采集、P3、P4、P5、P5.5、报告生成或输出健康检查失败时，调度器默认最多尝试 4 次，
间隔依次为 15、30、60 分钟。结果 webhook 自己已使用同一运行 ID 重试；若它最终失败，
调度器不会重跑整条流水线，避免接收方已成功处理但响应丢失时重复推送。

```dotenv
SHR_DAILY_MAX_ATTEMPTS_PER_DAY=4
SHR_DAILY_RETRY_INITIAL_DELAY_SECONDS=900
SHR_DAILY_RETRY_MAX_DELAY_SECONDS=3600
```

也可选择以下主机调度方式之一：

#### 方式 A: 使用 cron（推荐用于 Linux/macOS）

```bash
# 编辑 crontab
crontab -e

# 添加以下行（每天凌晨 2:00 执行）
0 2 * * * cd /path/to/HouseRadar && /path/to/.venv/bin/python scripts/daily_update_pipeline.py >> /var/log/homeradar/daily_update.log 2>&1

# 保存并退出
```

验证 cron 任务：
```bash
# 查看当前用户的 crontab
crontab -l

# 查看 cron 日志（可能需要 root 权限）
tail -f /var/log/homeradar/daily_update.log
```

#### 方式 B: 使用 systemd timer（推荐用于生产环境）

创建 service 文件：
```bash
sudo nano /etc/systemd/system/homeradar-daily-update.service
```

内容：
```ini
[Unit]
Description=Home Radar Daily Update Pipeline
After=network.target docker.service

[Service]
Type=oneshot
User=homeradar
Group=homeradar
WorkingDirectory=/path/to/HouseRadar
Environment="PATH=/path/to/HouseRadar/.venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/path/to/HouseRadar/.venv/bin/python scripts/daily_update_pipeline.py
StandardOutput=journal
StandardError=journal
SyslogIdentifier=homeradar-daily

[Install]
WantedBy=multi-user.target
```

创建 timer 文件：
```bash
sudo nano /etc/systemd/system/homeradar-daily-update.timer
```

内容：
```ini
[Unit]
Description=Home Radar Daily Update Timer
Requires=homeradar-daily-update.service

[Timer]
OnCalendar=daily
OnCalendar=02:00:00
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
```

启用并启动 timer：
```bash
# 重新加载 systemd 配置
sudo systemctl daemon-reload

# 启用 timer（开机自启）
sudo systemctl enable homeradar-daily-update.timer

# 启动 timer
sudo systemctl start homeradar-daily-update.timer

# 查看 timer 状态
sudo systemctl status homeradar-daily-update.timer

# 查看所有 timers
sudo systemctl list-timers
```

查看日志：
```bash
# 查看最近的日志
sudo journalctl -u homeradar-daily-update.service -n 100

# 实时查看日志
sudo journalctl -u homeradar-daily-update.service -f
```

#### 方式 C: 使用主机 cron 触发 Docker Compose

先启动 API 和四个计算 worker，再让主机 cron 在 API 容器内运行流水线：

```bash
docker compose up -d api market-worker valuation-worker future-worker decision-worker

# crontab -e
0 2 * * * cd /path/to/HouseRadar && docker compose exec -T api python scripts/daily_update_pipeline.py >> /var/log/homeradar/daily_update.log 2>&1
```

## 监控和告警

### 每日榜单主动投递

配置通用结果 webhook 后，每次成功运行会发送当日摘要、Top 房源、原始链接、分数和 Why
Ranked 正负理由。投递使用运行 ID 作为幂等键，临时错误最多重试三次。已配置 webhook
但投递失败时，本轮状态和健康检查都会失败，不会静默漏报。

```dotenv
SHR_DAILY_RESULT_WEBHOOK_URL=https://notify.example.com/home-radar/daily
SHR_DAILY_RESULT_WEBHOOK_BEARER_TOKEN=
SHR_DAILY_RESULT_WEBHOOK_TIMEOUT_SECONDS=10
SHR_DAILY_RESULT_WEBHOOK_MAX_ATTEMPTS=3
SHR_DAILY_ADVISORY_STATUS=RESEARCH_ONLY
```

在 P5.7 通过并正式批准切换前，`SHR_DAILY_ADVISORY_STATUS` 必须保持
`RESEARCH_ONLY`。投递内容明确标记为工作流候选，不构成买入建议。

### 数据新鲜度监控

创建监控 SQL 视图：

```sql
-- 连接到数据库
docker compose exec db psql -U radar -d shanghai_home_radar

-- 创建监控视图
CREATE OR REPLACE VIEW data_freshness AS
SELECT 
  'listings' as table_name,
  MAX(updated_at) as last_update,
  EXTRACT(EPOCH FROM (NOW() - MAX(updated_at)))/3600 as hours_since_update,
  COUNT(*) as total_records
FROM listing
UNION ALL
SELECT 
  'listing_snapshots',
  MAX(snapshot_at),
  EXTRACT(EPOCH FROM (NOW() - MAX(snapshot_at)))/3600,
  COUNT(*)
FROM listing_snapshot
UNION ALL
SELECT 
  'valuation_results',
  MAX(created_at),
  EXTRACT(EPOCH FROM (NOW() - MAX(created_at)))/3600,
  COUNT(*)
FROM valuation_result;

-- 查询新鲜度
SELECT * FROM data_freshness;
```

### 每日摘要报告

脚本已内置每日摘要功能，会自动记录：
- 本次采集的新上架、价格变化、重新挂牌和下架数量
- 当前机会列表大小
- 决策输出健康状态；LIVE 当前决策批次覆盖率低于 95% 或全部为数据不足时失败关闭，而不是让旧评分掩盖新批次故障
- 按现有 P5.5 排名规则生成的活跃房源 Top 10
- 保持相同排名顺序的当日变化候选，范围仅含新增、降价和重新挂牌
- 每套房的估值、三项评分、风险、置信度和 Why Ranked 证据
- 可直接阅读的 `digest`，即使未配置外部 webhook 也会随成功运行一起冻结

所有变化均按本次 `crawl_run_id` 统计，不使用滚动 24 小时窗口。每套榜单房源带
`activity_signal` 和 `activity_signals`。降价或涨价时还会带前后价格与变化百分比。
当日变化候选是原有合格房源排名的过滤视图，不改变 P5.5 权重、门槛或总榜顺序。

查看最新运行与排名：

```bash
curl http://localhost:8000/api/v1/operations/daily/latest
curl http://localhost:8000/api/v1/operations/daily/digest
curl http://localhost:8000/api/v1/operations/daily/health
```

历史文件保存在 Docker 的 `daily_reports` 命名卷中。日志可通过
`docker compose logs daily-runner` 查看。

### 告警集成（可选）

失败通知支持通用 JSON webhook：

```dotenv
SHR_DAILY_ALERT_WEBHOOK_URL=https://alerts.example.com/home-radar
# 可选
SHR_DAILY_ALERT_WEBHOOK_BEARER_TOKEN=replace-me
```

请求体固定为 `event` 和 `message` 两个字段。未配置 webhook 时只写错误日志。

## 故障排查

### 问题 1: 定时任务没有执行

**检查 cron 服务**：
```bash
# 检查 cron 是否运行
systemctl status cron  # Debian/Ubuntu
systemctl status crond # CentOS/RHEL

# 查看 cron 日志
grep CRON /var/log/syslog  # Debian/Ubuntu
tail -f /var/log/cron      # CentOS/RHEL
```

**检查 systemd timer**：
```bash
# 查看 timer 状态
systemctl status homeradar-daily-update.timer

# 查看下次执行时间
systemctl list-timers | grep homeradar

# 手动触发一次（测试）
sudo systemctl start homeradar-daily-update.service
```

### 问题 2: 数据采集失败

**检查日志**：
```bash
# 查看采集日志
tail -f /var/log/homeradar/daily_update.log

# 或 systemd 日志
sudo journalctl -u homeradar-daily-update.service -n 100
```

**常见原因**：
- API 认证失败 → 检查 API Key 是否有效
- 网络超时 → 增加 `SHR_COLLECTOR_REQUEST_TIMEOUT_SECONDS`
- API 限流 → 降低请求频率或联系数据提供方

**手动测试采集**：
```bash
cd /path/to/HouseRadar
source .venv/bin/activate
home-radar-collect
```

### 问题 3: Workers 处理缓慢

**检查队列堆积**：
```bash
docker compose exec redis redis-cli
> LLEN market
> LLEN valuation
> LLEN future
> LLEN decision
> QUIT
```

**检查 worker 状态**：
```bash
docker compose ps
docker compose logs --tail=50 market-worker
docker compose logs --tail=50 valuation-worker
```

**解决方案**：
- 增加 worker 实例数（编辑 `docker-compose.yml`）
- 优化数据库查询性能
- 检查 worker 是否有错误

### 问题 4: 数据库连接失败

**检查数据库服务**：
```bash
docker compose ps db
docker compose exec db pg_isready -U radar
```

**检查连接配置**：
```bash
# 检查环境变量
env | grep DATABASE

# 测试连接
docker compose exec api python -c "
from home_radar_shared.database import get_session_factory
from sqlalchemy import text
SessionLocal = get_session_factory()
with SessionLocal() as session:
    result = session.execute(text('SELECT 1')).scalar()
    print(f'Database OK: {result}')
"
```

## 性能优化

### 1. 数据库索引

确保关键字段有索引：
```sql
-- 检查现有索引
\di

-- 如果需要，添加索引
CREATE INDEX CONCURRENTLY idx_listing_updated_at ON listing(updated_at);
CREATE INDEX CONCURRENTLY idx_listing_event_occurred_at ON listing_event(occurred_at);
```

### 2. Worker 并发度

编辑 `docker-compose.yml`，增加 worker 实例：
```yaml
  valuation-worker:
    # ... 其他配置 ...
    deploy:
      replicas: 3  # 运行 3 个实例
```

### 3. 批处理优化

如果数据量很大，考虑分批处理：
- 按区域分批估值
- 按时间窗口分批更新
- 使用 `LIMIT` 和 `OFFSET` 分页处理

## 数据备份

Compose 中的 `backup-runner` 会在上海时间每天 01:00 生成 PostgreSQL custom-format
备份，先用 `pg_restore --list` 验证结构，再原子发布备份、SHA-256 和审计 manifest。
默认保留 14 天，并在当天错过计划时间后启动时补跑一次。

```bash
# 启动并查看备份服务
docker compose up -d backup-runner
docker compose logs backup-runner

# 用最新备份创建临时数据库、恢复、检查核心表，再自动删除临时库
docker compose --profile operations run --rm restore-drill
```

恢复演练的 JSON 报告与备份保存在 `database_backups` 命名卷。它验证数据库级恢复，
不会覆盖当前数据库。生产环境仍应把该卷定期复制到独立主机或对象存储。

## 总结

配置完成后，系统将：
1. ✅ 每天凌晨 2:00 自动执行
2. ✅ 从授权 API 拉取全量数据
3. ✅ 自动识别新增、变化、下架
4. ✅ 完成 P3-P5.5 完整处理流程
5. ✅ 生成每日摘要报告
6. ✅ 每日扫描前生成并校验数据库备份
7. ✅ 遇到错误时发送告警

**下一步**：
- 监控首次自动运行结果
- 根据数据量调整等待时间
- 配置告警通知渠道
- 定期检查数据新鲜度
