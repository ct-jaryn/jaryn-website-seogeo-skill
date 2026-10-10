# Jaryn Website SEO/GEO Skill

让网站优化形成可执行、可验证、可持续迭代的工作流。

An evidence-based SEO and AI-search skill by Jaryn: audit the right problems, improve the source, verify delivery, and measure real outcomes.

## 能做什么

- **技术 SEO**：URL 策略、canonical、robots、sitemap、渲染、缓存、结构化数据与性能。
- **内容与架构**：用户需求、原创价值、事实维护、内链、程序化页面与多语言。
- **AI 搜索**：区分搜索、训练和用户触发抓取；检查实体一致性、事实错误与引用传播。
- **迭代与测量**：把诊断落实到源码和模板，分别验证本地、生产、索引、引用与业务结果。
- **官方核对**：规则过期、区域引擎、查询、外链、地图结果或账号字段未知时，打开当前官方入口；打不开就标未知，不用记忆或第三方文章填空。

它面向真实网站和实际任务，不以固定字数、标签数量或没有校准依据的“GEO 总分”作为成功标准。

## 安装

本仓库根目录就是技能目录，包含 `SKILL.md`、按需加载的指南以及可选脚本，不需要 npm 或 pip 安装。

### 交给技能安装器

在支持技能安装器的 Codex 环境中输入：

```text
$skill-installer 从 https://github.com/ct-jaryn/jaryn-website-seogeo-skill 安装这个技能
```

### 手动安装

将整个仓库克隆或解压到当前客户端使用的技能目录，最终目录名为 `jaryn-website-seogeo-skill`，不要只复制 `SKILL.md`。

例如，在采用个人 `.agents/skills` 目录的环境中：

```bash
git clone https://github.com/ct-jaryn/jaryn-website-seogeo-skill.git \
  "$HOME/.agents/skills/jaryn-website-seogeo-skill"
```

项目级安装可放在项目的 `.agents/skills/jaryn-website-seogeo-skill`。已经使用 `.codex/skills` 的本地环境可沿用其实际识别的目录。避免在多个扫描目录重复安装同名技能；若目录已存在，先检查现有内容，不直接覆盖。安装后没有出现时，刷新或重启客户端。

## 使用

局部答疑和小改动按问题取证，不默认启动全站审计；代码、预览与已发布任务分别在相应交付层验收。已有授权和站点约定会复用，报告深度按任务决定。

```text
使用 $jaryn-website-seogeo-skill 审计 https://example.com，
区分已证实问题与假设，给出影响范围和优先级。暂不修改网站。
```

```text
使用 $jaryn-website-seogeo-skill 迭代当前仓库的网站，
落实高价值 SEO/GEO 改进，完成本地回归并说明生产验收方式。
```

```text
使用 $jaryn-website-seogeo-skill 排查 AI 回答中的旧价格，
检查正文、结构化数据和缓存，并设计可比的复测面板。
```

尽量提供域名或仓库、业务目标、目标市场/语言，以及是否允许修改和发布。没有私有分析数据时仍可进行公开页面与源码检查。索引、引用、查询量和区域引擎规则以当前官方入口或已验证账号为准；入口打不开时标未知，不会声称已经证明效果。

## 两个可独立使用的工具

需要 Python 3.9+，仅使用标准库。以下命令从仓库根目录运行。

### 保存网页响应证据

```bash
python3 scripts/capture_page.py \
  --url https://example.com/ \
  --out-dir ./artifacts/page-001
```

输出请求与响应元数据、正文字节，以及可解析 HTML 中的标题、索引声明、canonical、链接和 JSON-LD。输出目录必须是新目录。它不会执行 JavaScript，也不负责判断真实收录或排名。

### 汇总 AI 回答观测

```bash
python3 scripts/summarize_observations.py assets/observations.example.jsonl \
  --brand-domain example.com
```

上面使用的是明确标记的教学样本，不是实际效果。对真实观测，它会按引擎、产品界面、模型、语言、区域等条件分组，区分失败、无回答、未知状态与有效分母。不会调用模型或自动判断答案真假。

完整输入输出和限制见 [工具使用指南](guides/tools-evidence.md)。

## 项目结构

```text
SKILL.md       技能入口与任务分流
agents/        客户端展示元信息
guides/        8 份按任务加载的执行指南
  lookup.md    规则、区域引擎、查询/外链/地图和账号字段的官方入口
assets/        站点约定、实验计划和观测格式模板
scripts/       两个辅助工具及回归测试
```

## 验证与贡献

```bash
python3 -m unittest discover -s scripts -p 'test_*.py' -v
```

测试使用临时文件和 localhost HTTP 服务，不访问公网。部分沙箱需要允许本机端口。当前包含 51 项脚本测试；测试通过不代表某个网站已获得排名或引用。

修改技能指令时，另用局部答疑、未发布的代码补丁、无历史数据的内容实验和完整迁移等真实形态任务验证行为。检查任务是否完成、证据是否充分、是否出现额外发布或不必要停顿；不要只匹配回复中的标题或关键词。若比较技能版本，保持模型设置、输入、工具与权限一致，重复运行并记录质量、耗时和工具调用；单次场景检查不证明模型效果提升。

提交问题时请附复现步骤、工具版本、预期和实际结果，移除账号、Cookie、密钥及客户数据。修改脚本时补充对应测试；修改平台规则时记录适用日期与验证方式，不添加无法复核的增长承诺。

## 使用边界

只处理你有权访问和修改的站点。生产发布、外联、付费服务和持续监控需要对应授权。公开分享采集结果前检查正文、URL 参数和响应头中的敏感信息。

搜索引擎与 AI 产品自行决定抓取、索引和引用；本项目不保证排名、流量、推荐或收入，也不代表任何平台的官方产品或背书。

## 作者

Jaryn · [ct-jaryn](https://github.com/ct-jaryn)

## 许可证

[MIT License](LICENSE) · Copyright (c) 2026 Jaryn
