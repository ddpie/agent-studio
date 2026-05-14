# Game Content Factory

多 Agent 协作的游戏内容生产 + 审核流水线。策划用自然语言造 Agent，Agent 写完自动交给审核 Agent 守门。

## 目录结构

```
game-content-factory/
├── kb/            # 知识库文档（上传到 Bedrock KB）
│   ├── world/     # 世界观基础设定
│   ├── characters/# 角色设定卡 × 6
│   ├── locations/ # 地点设定 × 6
│   ├── timeline/  # 时间线与事件
│   └── guidelines/# 审核红线 + 对白风格 + 版本公开信息 + 术语表 + 翻译语感对照
├── script.md      # 对 Meta-Agent 的完整对话脚本
└── README.md
```

## 架构

```
         ┌─→ worldview-reviewer (挂 KB，检索设定做判定)
创作 Agent ─┼─→ emotion-reviewer  (语感打分)
         └─→ compliance-reviewer (法律合规)

🔴BLOCK → 自动修改 → 重新送审（最多 3 轮）
🟢PASS  → 输出成品
3 轮未过 → 升级给人决策
```

## 9 个 Agent

| Agent | 角色 | 绑 KB | Link 目标 |
|---|---|---|---|
| worldview-reviewer | 世界观审核（三级判定 + KB 引用） | ✅ | — |
| emotion-reviewer | 角色语感评审（1-10 打分） | ✅ | — |
| compliance-reviewer | 法律合规审核 | — | — |
| dialog-writer | NPC 对白创作（idle/剧情/战斗） | ✅ | → 三审 |
| event-copywriter | 活动文案（公告/push/帖子） | — | → 世界观审核, 翻译, 合规 |
| localizer | 本地化翻译（中→日/英/韩） | ✅ | → 译审 |
| translation-reviewer | 译文校对 | ✅ | — |
| community-responder | 社区回复起草 | ✅ | → 世界观审核 |
| patch-notes-writer | 版本更新说明 | — | → 世界观审核, 合规 |

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
- **自动流转** — 创作完成即送审，无需人工中转
- **有据可查** — 审核报告引用 KB 原文段落作为依据
- **可组合** — 新增审核维度 = 新增 Agent + link
- **可追溯** — 每步独立输出，问题定位到具体环节和规则

## 使用

打开 [script.md](script.md)，按顺序对 Meta-Agent 输入即可。script.md 分三部分：Part 1 搭建工厂（KB + Agent + 连接），Part 2 工厂运转（实际使用），Part 3 深度验证（边界案例）。

## 录制准备

- Part 1 建议提前完成，录制时可快进或剪辑
- 确认所有 Agent 状态 READY、KB ingestion 完成
- Pre-warm all agents（各调用一次避免冷启动）
- Dry-run Part 2 和 Part 3 prompts，确认输出符合预期
- 审核类 Agent 设低 temperature 保证一致性

## 虚构游戏

**《幻夜之刃》** — 和风暗幻 RPG，当前版本 3.1。七曜星神坠落形成的暮色异世界"夜见"，6 个核心角色各有严格的说话风格规范和版本信息红线。
