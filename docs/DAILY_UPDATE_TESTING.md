# 每日自动更新系统 - 测试检查清单

## ⚠️ 重要提醒

**当前状态**: Set A 校准集完整流水线已通过；真实授权 API
调用仍待合作方提供地址和凭据后执行。

**在配置 cron 或接入真实凭据之前，必须完成以下检查。**

---

## 已修正的问题

### ✅ 1. 导入错误修正
- ❌ 原问题: `from home_radar_collector.cli import collect_main` (不存在)
- ✅ 已修正: 流水线直接构造适配器并异步调用 `CollectorService.collect()`

### ✅ 2. 数据库函数修正
- ❌ 原问题: `from home_radar_shared.database import get_session_local` (不存在)
- ✅ 已修正: `from home_radar_shared.database import get_session_factory`

### ✅ 3. 配置变量名修正
- ❌ 原问题: `.env` 中使用 `SHR_DATA_MODE=LIVE`
- ✅ 已修正: 改为 `SHR_MARKET_DATA_MODE=live`（小写）

### ✅ 4. 认证模式修正
- ❌ 原问题: 文档中使用 `API_KEY`、`BASIC`（不支持）
- ✅ 已修正: 改为 `CUSTOM_HEADER`、`BASIC_AUTH`

### ✅ 5. 任务完成验证
- ❌ 原问题: P3-P5.5 仅等待固定秒数
- ✅ 已修正: 使用 RQ Job 状态轮询，确认任务成功完成或失败

### ✅ 6. 完整性检查增强
- ❌ 原问题: API 提前返回空页时仍标记为 COMPLETE
- ✅ 已修正: 
  - 第一页空 → `UNKNOWN`
  - 后续页空 → 停止拉取，检查 reported_total
  - reported_total 不匹配 → `PARTIAL`

### ✅ 7. 单元测试
- ❌ 原问题: 无测试
- ✅ 已添加: `test_authorized_api_adapter.py` 包含 11 个测试场景

---

## 端到端测试计划

### 阶段 1: 单元测试（本地）

```bash
cd /path/to/HouseRadar

# 1. 运行适配器单元测试
pytest services/collector/tests/test_authorized_api_adapter.py -v

# 预期: 所有测试通过
```

### 阶段 2: 集成测试（Docker 环境）

```bash
# 2. 启动测试环境
docker compose up -d db redis

# 3. 应用数据库迁移
docker compose exec api alembic upgrade head

# 4. 使用校准数据集测试完整流程
# 配置 .env 使用本地 JSON 文件
cat >> .env << 'EOF'
SHR_COLLECTOR_SOURCE=sample_json
SHR_COLLECTOR_ENDPOINT=data/raw/calibration_set_a/market_pool.canonical.json
SHR_COLLECTOR_CITY=shanghai
SHR_MARKET_DATA_MODE=sample
EOF

# 5. 测试数据采集（Compose 会将 data/raw 只读挂载到 API 容器）
docker compose exec -T api home-radar-collect
# 预期: 成功导入 500 条记录，无错误

# 6. 测试流水线脚本（不连接真实 API）
python scripts/daily_update_pipeline.py
# 预期: 
# - Step 1/5 采集完成
# - Step 2/5 市场基线任务成功
# - Step 3/5 估值任务成功
# - Step 4/5 未来评估任务成功
# - Step 5/5 决策编排任务成功
# - 生成每日报告
# - 个别证据不足房源显示为 skipped_insufficient_data，不计为系统失败
```

Set A is intentionally marked `partial` because it is a frozen research sample, not complete Shanghai
market coverage. The daily pipeline may continue with a non-empty partial feed only in `sample` or
`demo` mode. `live` mode still fails closed, and partial runs never reconcile missing listings.

### 阶段 3: 模拟 API 测试

```bash
# 7. 创建模拟 API 服务器（可选）
# 使用 Python Flask/FastAPI 创建一个简单的分页 API
# 返回测试数据，验证适配器行为

# 8. 配置适配器连接模拟 API
cat >> .env << 'EOF'
SHR_COLLECTOR_SOURCE=authorized_api
SHR_COLLECTOR_ENDPOINT=http://host.docker.internal:9000/mock-api/listings
SHR_COLLECTOR_AUTH_MODE=BEARER_TOKEN
SHR_COLLECTOR_BEARER_TOKEN=mock_token_for_testing
SHR_MARKET_DATA_MODE=sample
EOF

# 9. 测试分页行为
docker compose exec api home-radar-collect
# 验证:
# - 正确处理多页数据
# - 正确识别 total_pages 和 reported_total
# - 空页处理正确
# - 认证头正确发送
```

### 阶段 3B: 合作方 CSV 测试

```bash
mkdir -p data/inbox
# 将脱敏的合作方文件原子放到 data/inbox/shanghai_listings.csv
python scripts/prepare_partner_csv_manifest.py \
  --csv data/inbox/shanghai_listings.csv \
  --provider "合作方名称" \
  --license-reference "授权合同或导出工单编号" \
  --exported-at "2026-09-04T01:00:00+08:00" \
  --declare-complete \
  --reported-total 500

SHR_COLLECTOR_SOURCE=partner_csv \
SHR_COLLECTOR_ENDPOINT=data/inbox/shanghai_listings.csv \
SHR_COLLECTOR_AUTH_MODE=NONE \
SHR_MARKET_DATA_MODE=live \
docker compose run --rm daily-runner python scripts/daily_update_pipeline.py
```

预期：清单、范围、总数和 SHA-256 全部通过；缺失或被替换的清单使运行保守失败，且不协调
下架。首次 LIVE 运行还会受绝对数量和上一轮完整数量安全门保护。

### 阶段 4: 生产环境准备检查

在接入真实 API 前，确认：

```bash
# 10. 检查配置
# ✅ SHR_COLLECTOR_SOURCE=authorized_api
# ✅ SHR_COLLECTOR_ENDPOINT 使用 HTTPS
# ✅ SHR_COLLECTOR_AUTH_MODE 正确
# ✅ 凭据已正确配置
# ✅ SHR_MARKET_DATA_MODE=live

# 11. 检查数据库容量
# ✅ 磁盘空间充足（预估每天 100-500 MB 新数据）
# ✅ PostgreSQL 连接池配置合理

# 12. 检查 Workers 运行状态
docker compose ps
# ✅ market-worker, valuation-worker, future-worker, decision-worker 都在运行

# 13. 检查 Redis 队列
docker compose exec redis redis-cli
> INFO stats
> QUIT
# ✅ Redis 正常运行，内存充足

# 14. 手动执行一次完整流程（白天测试）
python scripts/daily_update_pipeline.py
# ✅ 所有步骤成功
# ✅ 没有超时
# ✅ 生成有效的每日报告

# 15. 验证数据质量
docker compose exec db psql -U radar -d shanghai_home_radar
-- 检查新导入的数据
SELECT COUNT(*) FROM listing WHERE created_at > NOW() - INTERVAL '1 hour';
SELECT COUNT(*) FROM valuation_result WHERE created_at > NOW() - INTERVAL '1 hour';
-- ✅ 数据完整
-- ✅ 没有重复
```

---

## 已知限制和待优化项

### 1. 任务超时时间固定
当前超时设置：
- P3 市场基线: 5 分钟
- P4 估值: 30 分钟
- P5 未来评估: 15 分钟
- P5.5 决策: 10 分钟

**建议**: 根据实际数据量调整超时时间。

### 2. 批处理失败策略
已知的数据不足会单独记录为 `skipped_insufficient_data`，不会阻断其余房源。
未知异常和任何 `failed > 0` 仍会立即停止流水线。

### 3. 并发控制
已使用按 `data_mode` 隔离的 Redis 单例锁。重复触发会生成独立的
`skipped_already_running` 审计文件，但不会覆盖 `latest.json`。

### 4. 监控和告警
当前有结构化运行文件、最新结果 API、容器日志和可选 webhook 失败通知。

**建议**: 
- 集成 Prometheus + Grafana 监控
- 配置并验证告警 webhook
- 添加数据新鲜度检查

### 5. 回滚机制
当前没有数据回滚能力。

**建议**: 
- 每日自动备份数据库
- 实现快照回滚功能

---

## 上线前最终检查清单

- [x] 单元测试全部通过
- [x] 使用校准数据集测试完整流水线
- [x] 验证所有 5 个步骤成功完成
- [x] 验证任务状态轮询正确工作
- [x] 验证每日报告生成
- [x] 验证 Redis 单例锁
- [x] 验证 Top 10 快照和最新结果 API
- [ ] 手动执行一次真实 API 调用（白天）
- [x] 确认校准集数据完整性和落库数量
- [ ] 配置备份策略
- [ ] 配置告警通知
- [x] 文档已更新
- [ ] 团队培训完成

---

## 上线后监控重点

### 第一周（密切监控）

1. **每天检查执行日志**
   ```bash
   tail -f /var/log/homeradar/daily_update.log
   # 或
   sudo journalctl -u homeradar-daily-update.service -f
   ```

2. **每天检查数据新鲜度**
   ```sql
   SELECT * FROM data_freshness;
   ```

3. **每天检查队列堆积**
   ```bash
   docker compose exec redis redis-cli LLEN valuation
   ```

4. **每天检查磁盘空间**
   ```bash
   df -h
   ```

### 第一月（定期检查）

- 每周检查一次执行日志
- 每周检查一次数据质量
- 每周检查一次系统资源使用
- 优化超时时间和并发配置

---

## 联系和支持

如果在测试过程中遇到问题：

1. **查看日志**: 检查错误堆栈和上下文
2. **检查配置**: 验证所有环境变量正确
3. **隔离测试**: 单独测试失败的步骤
4. **回退方案**: 保留原有手动流程作为备份

---

**重要**: 在完成上述测试并确认所有步骤正常工作之前，请勿配置 cron 定时任务，也不要接入生产环境凭据。
