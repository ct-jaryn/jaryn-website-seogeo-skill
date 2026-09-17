# AI 搜索、实体与引用

阅读条件：AI 搜索准备度、品牌可见性、错误引用、机器人访问、补充 Markdown 或 llms 文件。下文归纳平台公开规则，核验日期为 2026-09-17；实施时重新查询相关平台的官方文档与账号设置。

## 产品、抓取和许可分别处理

| 平台/用途 | 当前需要区分的控制 | 对实施的意义 |
|---|---|---|
| Google Search 的 AI Overviews / AI Mode | Googlebot、索引/摘要指令，以及 Search Console 的 Search generative AI 设置 | 技术资格之外核对属性及父属性的 Include/Exclude；无账号访问就标未知 |
| Google-Extended | robots 用途控制 token，并非独立 HTTP UA；影响相关训练及 Gemini Apps/Vertex 的 grounding 用途 | 与 Google Search 控制分开；不是把 `User-Agent: Google-Extended` 请求成功就证明引用权限 |
| ChatGPT 搜索 | OAI-SearchBot | 与官方 IP/WAF 准入一起检查；开放不能保证引用 |
| OpenAI 潜在训练 | GPTBot | 与搜索独立，保持站点所有者偏好 |
| ChatGPT 用户请求 | ChatGPT-User | 用户触发访问，不决定搜索资格；robots 不一定适用 |
| Claude 搜索/用户请求/训练 | Claude-SearchBot / Claude-User / ClaudeBot | 三者分开，不把 ClaudeBot 当搜索索引机器人 |
| Perplexity 搜索/用户请求 | PerplexityBot / Perplexity-User | Bot 用于搜索而非基础模型训练；User 属用户请求，规则适用性不同 |
| Bing / Copilot | Bingbot、索引指令及 Bing 对 noarchive/nocache 的 AI 使用说明 | 检查旧缓存指令是否有非预期 AI 使用后果；核对当前官方规则 |

不要按未经核验的机器人长名单批量 Allow。新的中国/地区性引擎、产品或 UA 需查各自官方规则；没有资料就保留未知，不把 DeepSeek API 回答等同于某个联网产品的搜索结果。

## Google 的额外属性控制

当前官方说明：Search Console → Settings → Search generative AI 可以 Include、Exclude 或继承父属性。根属性默认 Include，子属性可能继承上层选择。它影响搜索生成式体验中的链接和 grounding；不改变普通 Search 排名、不控制训练。

核对当前属性范围和继承来源，特别是子域和路径属性。不要仅因为某一属性显示继承就判断允许。变更属于发布方内容使用决策，沿用用户既有偏好与授权。页面满足条件仍不保证索引或被选为来源。

## 引用内容和实体一致性

- 为实体建立稳定、公开、事实准确的主要页面，说明是什么、适用谁、提供什么、边界、价格/版本和来源。
- 重要事实集中由可靠来源维护，并在页面、schema、产品 feed、文档、公开 API/Markdown 和语言版本中同步。
- 真实原始资料、测试结果、规范和方法比空泛“权威语气”更可核验。作者与组织信息支持读者判断来源，不承诺其必然增加引用概率。
- 引用链接出现不代表模型推荐品牌；提到品牌也不代表引用你的域名。链接可能不支持模型旁边那句话，需抽样打开来源核实。
- 竞争对手被引的页面可反向分析信息缺口，但不复制内容或批量伪造同类“第三方推荐”。

## llms.txt、Markdown 和代理可用性

Google 明确说明 llms.txt 不改变 Search 可见性。其他产品是否消费此文件，需要当前证据。把它当可选文档导航/集成资产，满足以下条件再做：已有明确读者或工具、维护成本合理、从同一事实来源生成、关键页面链接有效。

对于技术文档，稳定 Markdown、复制代码、版本信息、示例和机器接口可能显著改善代理完成任务的能力；这不自动证明搜索排名提升。对普通营销站，先解决正文、技术交付和真实需求。

检查新增接口是否造成内容版本冲突、缓存协商错误、重复发现或敏感信息暴露。不要无证据要求 `/ai-summary.json`、`/.well-known/llms.txt`、多个语言 llms 文件、隐藏 facts block 或 llms-full 全量副本。

浏览器代理的页面目标是实际可操作：标签清楚、链接/按钮语义正确、输入有 label、动作有反馈、布局稳定。WebMCP、UCP 或其他新协议仅在具体用户任务和技术栈支持时评估，不能成为 GEO 合格门槛。

## 修复过期或错误 AI 回答

1. 保存错误回答原文、日期、问题、产品界面/API、模型、搜索模式、语言/地域和引用 URL。
2. 判断错误来自本站、旧版本/缓存、第三方来源还是模型无来源推断。先核对它实际引用的页面，而非只改首页。
3. 检查本站规范页、schema、feed、文档镜像和语言版是否仍有旧事实。修拥有事实的数据源，再验证公开结果。
4. 按已授权流程通知支持的搜索平台内容变化；通知收到不能标为索引更新。
5. 在可比条件下重复查询，保留失败和无答案样本，按 [测量指南](measurement.md) 判断。

来源状态使用 `source-unverified`、`source-not-fixed`、`site-fixed`、`crawl-signals-updated`。回答状态使用 `unable-to-verify`、`engine-stale`、`current-observation`、`propagated`。只有可比的“旧错误 → 新正确”记录才能写 propagated；一次正确答案只能证明当次观察。不要承诺刷新期限。

## 站外因素的处理边界

网站迭代可提供值得引用的原始资料、公开纠错渠道、作者/组织事实和可分享工具。站外编辑采纳、真实口碑、市场需求、品牌历史、平台检索选择与算法波动无法从仓库直接修复。

对这些依赖给具体证据与动作：如“引用方仍展示旧价格，已准备准确更正材料”。没有 Wikipedia/Wikidata 条目不是技术缺陷；只有符合社区规则与事实重要性才考虑。禁止把互链站群、虚构用户推荐、购买提及或伪装讨论作为默认增长手段。
