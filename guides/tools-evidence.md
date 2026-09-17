# 工具与证据使用约定

优先使用项目已有、可验证的工具。附带脚本只解决两个狭窄问题：保留原始响应、正确汇总已人工评估的回答。Python 3.9+ 标准库即可；不自动安装依赖、联网调用模型或花费 API 额度。

## 1. 单页证据采集

在技能目录运行，或把命令中的脚本路径改为技能绝对路径。输出必须是**尚不存在的新目录**。

```bash
python3 scripts/capture_page.py \
  --url 'https://example.com/page' \
  --out-dir /tmp/jaryn-page-evidence-001

python3 scripts/capture_page.py \
  --html-file /path/to/local-page.html \
  --base-url 'https://example.com/page' \
  --out-dir /tmp/jaryn-local-evidence-001
```

默认 `--user-agent JarynSEOAudit/1.0`、`--timeout 20` 秒、`--max-bytes 5242880`（5 MiB，允许调小，不允许调大）。请求 Accept-Encoding: identity；如果服务器仍发送压缩编码，保留原始字节并报告未解析，不假装解压成功。

输出：

- `metadata.json`：UTC 时间、请求身份、重定向顺序、最终 URL/status、同次最终 GET 的响应头（保留重复项，排除 Set-Cookie）、编码、错误、截断、已保存字节的 SHA256。中间跳只保存状态和 Location；若需研究中间响应头，另行补采。耗时是采集总时长，不是 TTFB。
- `response-body.bin`：受体积上限约束的响应正文字节（HTTP 库已处理 chunked 等传输分帧，不是原始网络报文）。hash 只覆盖已保存字节；截断不是完整网页。`truncated` 表示采集体积上限；`transfer_incomplete` 为 true 表示短传输/读取失败，false 表示按当前 framing 完整读至 EOF，null 表示因上限或不可信 framing 无法判断。冲突的 Content-Length/Transfer-Encoding 会显式记录，不作为完整 HTML 使用。
- `page.json`：当响应可按 HTML 解析时生成。记录非 inert template 范围的 title/canonical/meta/hreflang、base/lang、标题、main/article 文本、链接和 JSON-LD；模板原文仍保留于 body。JSON-LD 仅检查 JSON 语法，不是平台 rich-result 验证。声明式 Shadow DOM 和 JS 激活的模板仍需浏览器检查。

退出码 `0` 表示采集完成，包括成功保存 404/500；`2` 表示参数错误或采集存在网络、读取、编码、截断等问题。**退出成功不表示 SEO 通过。** 非 HTML 响应不强行做 HTML 缺失判断。

边界：不执行 JS、不模拟浏览器 DOM、不判断隐藏/可见文本、不算 robots.txt 匹配规则、不检查索引、不递归抓站、不验证真实机器人身份；离线模式的 status 为空。HTMLParser 在畸形 HTML 的行为可能不同于浏览器。需要浏览器、GSC 检查或真实日志时，另行取得相应证据。

只采集用户纳入范围的 URL；未知重定向、凭据、内网或敏感页面先核查范围。脚本不携带登录 Cookie，拒绝 URL 中的用户名密码；但 URL 查询参数和正文仍可能包含秘密，保存和分享前检查脱敏。不要以伪装机器人 UA 绕过访问限制，不高频重试挑战页。

## 2. AI 回答观察汇总

```bash
python3 scripts/summarize_observations.py observations.jsonl \
  --brand-domain example.com \
  --brand-domain example.org \
  --out new-summary.json
```

输入每行一个 JSON 对象；使用 [教学样本](../assets/observations.example.jsonl) 了解格式，**样本不是实际 AI 搜索结果**。最小记录如下：

```json
{
  "schema_version": 1,
  "run_id": "20260917-q1-chatgpt-1",
  "timestamp": "2026-09-17T15:00:00+08:00",
  "prompt_id": "q1",
  "panel_id": "purchase-intent-v1",
  "engine": "ChatGPT",
  "surface": "consumer-ui",
  "provider": "manual",
  "model": null,
  "locale": "zh-CN",
  "region": "CN",
  "search_enabled": true,
  "session_condition": "documented-session-v1",
  "status": "success",
  "raw_answer": "此处替换为真实采集的回答；不要将占位符当作证据。",
  "evidence_path": null,
  "brand_mentioned": true,
  "brand_recommended": false,
  "cited_urls": ["https://docs.example.com/guide"]
}
```

`run_id` 全局唯一；时间带时区。除 `raw_answer/evidence_path` 外上述字段均必填；成功回答至少有一个非空原文或证据引用。`model/locale/region/search_enabled` 未知时显式 null；未知条件相同不等于实验条件可比。`session_condition` 指向已记录的登录、历史记录、个性化和会话规则。

- `success` 是实际得到回答；拒答/没有答案为 `no_answer`，采集失败为 `error`。错误和无回答不能混进有效答案分母，但分别显示数量。
- 两项品牌判断是人工或另行审核的输入，脚本不自动猜测。未评估为 null；`cited_urls: []` 表示确认没有引用，null 表示引用收集不完整。
- 按 engine/surface/provider/model/locale/region/panel_id/search_enabled/session_condition 完整分组，不混合 UI 与 API。
- 每项比率 = 相应成功回答中的肯定数 ÷ 该项已评估数；分母为零输出 null。不是所有请求成功率，也不是市场占有率。
- 域名按精确域名及点分隔子域匹配，排除 `example.com.evil.test`；不跟随引用重定向，不证明引用支持相邻断言。拒绝含反斜杠或凭据的 URL；Unicode 主机采用保守、无损 IDNA 校验而非完整 UTS46 实现，ß、final sigma、joiners 等映射有歧义的主机需提供浏览器核验后的 ASCII/Punycode，不会强行并入另一域名。
- `evidence_path` 只是出处引用，脚本不打开或核实它。保管原始回答并人工检查事实/断言支持；不能用脚本校验替代证据审核。
- 同一个 prompt 重复次数多会更影响按运行统计的比率。输出包含各 prompt 运行次数与时间跨度；对比前检查样本组成，未知条件、版本变化和时间窗口。
- 畸形行、重复 run_id、无时区日期、非法 citation URL 报具体行号。`--out` 拒绝覆盖已有文件。不提供跨平台 GEO 总分。

品牌域名填自身真实控制的主机，不填公共后缀（如 com）或无关共享域名。网址引用到第三方关于你的文章与引用你的官网是不同指标，本脚本只自动统计后者。

## 3. 工具选择原则

| 需要回答的问题 | 合适证据/工具类别 | 不能由它证明 |
|---|---|---|
| 模板、链接、URL、指令的全站分布 | 受控爬虫、项目构建/路由检查、sitemap 对照 | 实际索引和 AI 推荐 |
| Google 看到什么、选了哪个规范页 | GSC URL Inspection、索引报告、实时测试并区分其口径 | 实时测试通过不等于已收录 |
| 用户加载和互动是否顺畅 | 真实用户 CWV/RUM + 浏览器/Lighthouse 定位 | 一次实验室分数不等于全体体验 |
| AI 是否引用、引用是否准确 | 官方可用报告 + 固定问题集 + 原始回答/链接 | 供应商总体覆盖不等于你的样本可比 |
| 业务价值是否变化 | 分析事件、实际转化、辅助归因与实验设计 | referrer 缺失不等于无人受 AI 影响 |

选择外部检测项目先看源码或当前文档、是否维护、采集来源、可导出证据、规则依据、数据外发、凭据权限、真实总成本和撤销方式。README、星数与“AI 推荐分”不是验收依据。未安装工具时先用已有能力，不为方便默认引入有费用/高权限依赖。

## 4. 本地验证

```bash
python3 -m unittest discover -s scripts -p 'test_*.py' -v
```

测试使用临时文件和 localhost HTTP 服务，不访问公网；某些沙箱需允许本地端口。测试通过仅证明覆盖的采集/汇总行为。真实网站还必须按 [执行标准](workflows.md) 验证。
