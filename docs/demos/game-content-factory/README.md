# Game Content Factory

自然语言搭建游戏内容生产 Agent 流水线。

一个"什么都会"的 Agent 理想丰满，实践脆弱。工具多了选不准，逻辑一复杂牵一发动全身，出了问题难以定位具体环节。这和单体应用的困境如出一辙：拆分、专注、协作才是出路。业务团队通常面临两个选择：忍受单体 Agent 复杂度膨胀带来的脆弱，或者等工程团队逐个开发、联调、部署垂类 Agent。

我们做了一套自然语言 Agent 编排方案来验证第三条路，构建了一条游戏内容生产流水线：运营团队批量产出 NPC 对白、活动文案、版本公告、多语言翻译，同时确保不泄露未发布版本信息、不违反角色设定、不触碰合规红线。拆成多个单职责 Agent，创作、审核、翻译、校对各司其职，通过对话创建，不写代码。Agent 挂载知识库按需检索，判定有据可查；上下游通过 A2A（Agent-to-Agent）协议串联，未通过时自动修订重新提交，多轮仍未通过则移交人工。Agent 还可挂载用自然语言创建的 Skill 脚本做固定规则精确校验，或设置定时任务无人值守运行。每个 Agent 只做好一件事；扩展时不动现有环节，用自然语言创建新 Agent 接入即可；出了问题可追溯到具体环节和规则。

这套模式不限于游戏，任何需要多环节协作、质量把关的业务流程均可复用，基于 Amazon Bedrock AgentCore 构建。

---

多 Agent 协作的游戏内容生产 + 审核流水线。3 个独立知识库分别由叙事组、法务组、本地化组维护，Agent 按需绑定多源 KB 交叉验证。

## 目录结构

```
game-content-factory/
├── kb/
│   ├── worldview/         # KB① 世界观设定（叙事组维护）
│   │   ├── world/         # 世界观基础设定
│   │   ├── characters/    # 角色设定卡 × 6
│   │   ├── locations/     # 地点设定 × 6
│   │   ├── timeline/      # 时间线与事件
│   │   ├── 审核红线总表.md
│   │   └── 版本公开信息总表.md
│   ├── compliance/        # KB② 合规规则（法务组维护）
│   │   ├── 合规审核细则.md
│   │   ├── 文化敏感词禁忌表.md
│   │   └── 全球概率型道具监管总表.md
│   └── localization/      # KB③ 本地化规范（本地化组维护）
│       ├── 术语表.md
│       ├── 翻译语感对照表.md
│       ├── 对白风格指南.md
│       └── 本地化技术规范.md
├── script.md              # 对 Meta-Agent 的完整对话脚本
└── README.md
```

## 架构

```
         ┌─ KB①世界观 ─┐
创作 Agent ─┼─ KB③本地化 ─┼─→ 世界观审核(KB①+KB②)
         └─────────────┘  → 语感评审(KB①+KB③)
                           → 合规审核(KB②)

🔴BLOCK → 自动修改 → 重新送审（最多 3 轮）
🟢PASS  → 输出成品 → 本地化翻译(KB③) → 译文校对(KB③+KB②)
```

## 9 个 Agent

| Agent | 角色 | 绑定 KB | Link 目标 |
|---|---|---|---|
| worldview-reviewer | 世界观审核 | ①+② | — |
| emotion-reviewer | 角色语感评审 | ①+③ | — |
| compliance-reviewer | 合规审核 | ② | — |
| dialog-writer | 对白创作 | ①+③ | → 三审 |
| event-copywriter | 活动文案 | ② | → 世界观审核, 合规, 翻译 |
| localizer | 本地化翻译 | ③ | → 译审 |
| translation-reviewer | 译文校对 | ③+② | — |
| community-responder | 社区回复 | ①+② | → 世界观审核 |
| patch-notes-writer | 版本更新说明 | ② | → 世界观审核, 合规 |

## 连接关系（10 条 link）

```
dialog-writer       → worldview-reviewer, emotion-reviewer, compliance-reviewer
event-copywriter    → worldview-reviewer, localizer, compliance-reviewer
localizer           → translation-reviewer
community-responder → worldview-reviewer
patch-notes-writer  → worldview-reviewer, compliance-reviewer
```

## 工厂特质

- **专业分工** — 每个 Agent 精而专，prompt 短而聚焦
- **独立演进** — 法务/叙事/本地化各自更新知识库，互不干扰
- **多源检索** — 单个 Agent 可同时查询多个知识库交叉验证
- **自动流转** — 创作完成即送审，无需人工中转
- **有据可查** — 审核报告引用 KB 原文段落作为依据
- **可组合** — 新增审核维度 = 新增 Agent + link
- **可追溯** — 每步独立输出，问题定位到具体环节和规则

## 独立演进

3-KB 分治的核心收益：

- **各团队自治** — 叙事组更新世界观、法务组更新合规规则、本地化组更新术语表，各自上传文档即可，互不干扰
- **热更新生效** — 合规规则更新后，所有绑定 KB② 的 Agent 下次检索立即使用新规则，无需重新部署
- **无需 Agent 重建** — 知识库内容变更不影响 Agent 代码，上传文档 → ingestion 完成 → 即时生效

## 使用

打开 [script.md](script.md)，按顺序对 Meta-Agent 输入即可。script.md 分三部分：Part 1 搭建工厂（KB + Agent + 连接），Part 2 工厂运转（实际使用 + 热更新），Part 3 深度验证（边界案例）。

## 录制准备

- 提前 ingest 3 个知识库，确认 ingestion 状态全部 COMPLETE
- 提前创建 6 个 Agent（剩余 3 个留到录制时 live 创建）
- 准备"热更新"文档（Part 2 演示 KB 即时生效用）
- Pre-warm 所有已创建的 Agent（各调用一次避免冷启动）
- Dry-run Part 2 和 Part 3 prompts，确认输出符合预期

## 虚构游戏

**《幻夜之刃》** — 和风暗幻 RPG，当前版本 3.1。七曜星神坠落形成的暮色异世界"夜见"，6 个核心角色各有严格的说话风格规范和版本信息红线。
