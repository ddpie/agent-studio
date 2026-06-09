# Art Style Guide Template

本文档定义项目的视觉风格设定。Agent 在调用 `generate_image` 或 `create_storyboard` 时，应先检索本文档，将"style_context 字符串"部分作为 `style_context` 参数传入，确保生成的图像符合项目统一的美术方向。

---

## style_context 字符串

> 将下面这段英文关键词直接作为 `style_context` 参数传给 generate_image / create_storyboard。

```
[在此填写，示例: Japanese dark fantasy, twilight palette, muted purples and golds, soft volumetric lighting, melancholic atmosphere, ink-wash texture accents, cinematic composition]
```

---

## 各维度详细设定

### 1. 画风 / 技法 (Art Style)

描述整体绘画风格和技法基底。

- **说明**: 选择一个主导画风，可叠加 1-2 个辅助技法
- **示例**: concept art / anime cel-shading / watercolor / oil painting / pixel art / ink-wash painting
- **英文关键词**: `[填写]`

### 2. 色调 / 配色 (Color Palette)

定义主色调、辅助色、禁用色。

- **主色调**: [如 muted purples, deep indigo, twilight gold]
- **辅助色**: [如 cherry blossom pink, moonlight silver]
- **禁用色**: [如 neon colors, pure white backgrounds]
- **英文关键词**: `[填写]`

### 3. 光影 (Lighting)

定义光源类型和光影氛围。

- **主光源**: [如 soft volumetric lighting, twilight rim light]
- **辅助光**: [如 warm lantern glow, cool moonbeam fill]
- **阴影风格**: [如 soft gradient shadows, sharp dramatic shadows]
- **英文关键词**: `[填写]`

### 4. 构图倾向 (Composition)

定义默认的构图偏好。

- **视角**: [如 slightly low angle, eye-level, bird's eye]
- **景深**: [如 shallow depth of field, deep focus]
- **画幅**: [如 cinematic widescreen, square portrait]
- **英文关键词**: `[填写]`

### 5. 氛围关键词 (Atmosphere / Mood)

定义情绪和整体感觉。

- **核心氛围**: [如 melancholic, mysterious, serene, oppressive]
- **叙事基调**: [如 restrained, poetic, bittersweet]
- **英文关键词**: `[填写]`

### 6. 负面提示 / 全局禁忌 (Negative Prompt)

定义所有生成中必须排除的元素。这些会附加到 generate_image 的 negative_prompt 参数中。

```
[在此填写，示例: modern clothing, smartphones, cars, neon signs, text overlays, watermarks, chibi style, overly bright saturated colors]
```

### 7. 角色视觉锚点 (Character Visual Anchors, 可选)

为核心角色定义外观关键词，Agent 在生成特定角色的图片时应附加到 prompt。

| 角色 | 视觉关键词 (English) |
|------|---------------------|
| [角色名] | [如 silver-haired young woman, flowing white kimono, cherry blossom motif, soft pink eyes] |
| [角色名] | [如 dark-armored swordsman, crimson scarf, single visible eye, jagged blade] |

---

## 使用指引

1. Agent 的 system prompt 中应包含类似指引："在调用 generate_image 或 create_storyboard 前，先从知识库检索美术风格设定文档，将其中的 style_context 字符串作为 style_context 参数传入。"
2. 当 style_context 被传入时，工具会跳过默认的 style 预设（如 concept-art），直接使用项目级的美术方向。
3. 如需生成与项目风格无关的图片（如 UI 图标、图表），不传 style_context 即可恢复默认行为。
4. 负面提示（第 6 节）建议同时传入 generate_image 的 negative_prompt 参数，与工具默认的质量禁忌叠加。
