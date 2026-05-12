# Game Content Factory Demo

游戏内容工厂：多 Agent 协作的内容生产 + 审核流水线。策划对话造 Agent，Agent 写完自动交给审核 Agent 守门——创作放飞，审核严格。

## 目录结构

```
game-content-factory/
├── kb/                    # 知识库文档（上传到 Bedrock KB）
│   ├── world/             # 世界观基础设定
│   ├── characters/        # 角色设定卡
│   ├── locations/         # 地点设定
│   ├── timeline/          # 时间线与事件
│   └── guidelines/        # 版本控制 + 审核规范
├── prompts/               # Agent system prompt
└── README.md
```

## 核心 Demo 链路

```
对白创作 Agent ──A2A──▶ 世界观审核 Agent (挂知识库)
   生成对白              kb_retrieve 检索设定集
                         逐条判定 PASS/WARN/BLOCK
                         BLOCK → 创作 Agent 自动修改 → 再审
```

## 使用步骤

1. 创建知识库：`kb_create(name="幻夜之刃世界观")`
2. 上传 `kb/` 下所有文档
3. 创建审核 Agent（绑定知识库）
4. 创建创作 Agent
5. `link_agent(创作Agent, 审核Agent)`
6. 预热两个 Agent
7. 对创作 Agent 说"帮月见写 30 条 idle 对白"

## 9 个 Agent

| Prompt 文件 | 角色 | 谁造 |
|---|---|---|
| dialog-writer.md | NPC 对白创作 | 策划 |
| worldview-reviewer.md | 世界观审核（挂 KB） | 主策 Lead |
| event-copywriter.md | 活动文案 | 运营 |
| localizer.md | 本地化翻译 | 本地化主管 |
| translation-reviewer.md | 译文校对 | 外部译者 |
| emotion-reviewer.md | 角色语感评审 | 叙事总监 |
| community-responder.md | 社区回复 | 社区运营 |
| patch-notes-writer.md | 版本日志 | 运营 |
| compliance-reviewer.md | 敏感合规 | 法务 |
