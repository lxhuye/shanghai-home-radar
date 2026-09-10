# 浏览器房源研究闭环

本机入口：http://127.0.0.1:5057/ 。当前范围为安居客徐汇 250–300 万元的单个筛选页。

## 自动流程

专用浏览器检测页面 → 保留原始证据与部分房源观测 → 对比上次挂牌价格 →
调用现有 P4 估值 → P5 未来评估 → P5.5 资格判断与排序 → 保存分析批次 → 页面与下载报告。

每六小时到期检查一次。验证码出现、来源失败或解析失败时暂停访问，保留上次成功结果。
恢复只检查当前页面。服务启动会分析已有证据，不访问来源；来源访问仍需确认恢复。
同一房源批次与相同模型配置不会重复计算，重启不会重写原观测时间。分析失败时保留原始
观测并重试本地计算；无需为重算刷新来源。损坏的派生报告可以由保留的观测重新生成。

`scripts/research_pipeline.py` 直接调用原有引擎，不创建第二套评分公式。
它不依赖生产数据库，不读取旧样例池、不修改冻结校准材料。
只有当前页的房源进入候选与可比挂牌集合，价格历史只读取同一来源和同一范围的既有观测。
目标房源自身及相同小区、房型、楼层、朝向且面积相近的疑似重复广告排除出其可比集合。
此规则属于保守排除，不证明已完成跨平台或物理房源去重。

## 页面与接口

- 首页显示观测时间、下一次检查时间、分析摘要、挂牌价、挂牌参考区间和比较依据。
- 支持全部、降价、首次见到、有参考区间、通过推荐门槛筛选。
- 点击查看依据，展示实际参与估值的挂牌、调整后的参考价、置信度及缺口。
- 下载本次分析得到完整 JSON，包含原引擎版本、配置和输入指纹、数据时间与局限。
- `GET /analysis` 只返回与当前观测匹配的分析，过期或暂停时标明历史状态。
- `GET /health` 检查可采集状态、观测新鲜度与分析完成状态。资料不足是分析结果，
  不会伪装成故障，也不会因为服务可访问就宣称已经可以推荐购买。
- `GET /observations` 和 `GET /feed` 保持原协议；所有读取均不触发来源访问。

分析存储在 `data/exports/browser_handoff/analysis/<输入指纹>.json`，原始记录和历次
Canonical JSON 仍存于 `observations/`。导出目录由 Docker 绑定到本机，服务重建不会删除。

## 证据边界

- 全部数据保留 `sample / partial / private_research_only` 标记，具体指公开挂牌研究数据，
  并非把页面数据当作生成的示例。没有升级为已核实的 LIVE 数据。
- 未核实挂牌使用固定 0.5 来源置信权重；挂牌来源不等于成交证据。
- 参考区间受价格筛选与单页覆盖偏差影响，不能解释成全市场公平成交价。
- 没有足够可比挂牌时，不输出价格。缺少市场基线、成交校准、就业交通等因子时，
  沿用原引擎的资料不足判断，不导入样例因子或虚构流动性。
- P5 原始研究输出留在下载报告内供复核，但页面不展示基于缺失因子的默认情景预测。
- 没有通过推荐门槛不意味着房源不好，也不意味着市场没有机会。
- 历史中的天数是本监控观察到的天数，不是真实挂牌天数。

## 2026-09-08 验收

真实页面观测时间为 15:12:25（上海时间），共 61 条；55 条完成挂牌参考区间、未来评估
与决策判断，6 条因无可比挂牌明确保留为资料不足。通过推荐门槛为 0。

351 项非数据库集成测试通过；真实 Chromium 对本机测试页验证采集、验证检测、暂停、
停止重试、恢复、降价记录和自动分析通过。严格类型检查和 Ruff 通过。
实际部署后已检查摘要、详情与空推荐榜。37 项数据库集成测试未重跑，本次没有改数据库逻辑。

## 尚需外部证据的产品能力

研究软件链路已经可以持续运行。可用于真实购房决策的完整产品仍需要更广的有效来源覆盖、
可验证的成交数据、未来因子更新、房源真实性核对，以及人工盲标与校准验收。
这些条件不能通过降低门槛、改写数据模式、复用旧样例证据来代替。
原先数据库日更继续独立使用原 SAMPLE 输入；本次闭环的结果在监控页面查看。

## 2026-09-08 evidence wiring repair

Browser research now passes an actual P3 partial-listing baseline to P4. It uses only
current same-source peers in the same configured area bucket and layout, excludes the
target and suspected duplicate ads, and follows the existing geography sample minimums.
Coverage remains partial and confidence remains capped by P3. No liquidity is inferred
from a single observation.

P5 receives district yearbook factors with their original low confidence, formulas and
public provenance. These are population and housing-stock proxies, not observed buyers
or forecasts of new supply. The rough planning-center weights are deliberately not used
as measured employment. Existing OSM stations are linked to exact cached residential
address matches. `browser_geocodes.json` contains only matching geocoding provider
answers, no historic listing prices. Original listing timestamps remain unchanged.

Evidence retrieval and publication dates must precede the listing observation. Synthetic
records are rejected, expired records are excluded, and all evidence files participate
in report cache invalidation. Per-listing reports expose baseline and location evidence
and distinguish remaining evidence gaps. Recommendation thresholds are unchanged.

See [精准推荐实施记录](PRECISE_RECOMMENDATION_DELIVERY.md) for buyer constraints,
human-reviewed transaction/property/future evidence intake and the isolated blind-review
workspace. Supplemental evidence participates in analysis fingerprints and has a distinct
knowledge cutoff; it never rewrites the original source observation timestamp.
