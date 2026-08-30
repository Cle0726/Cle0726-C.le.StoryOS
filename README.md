<div align="center">

# C.le. StoryOS

### 面向长篇小说的确定性故事操作系统与叙事状态引擎

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Architecture](https://img.shields.io/badge/Architecture-Event%20Sourcing-purple)]()
[![Status](https://img.shields.io/badge/Status-Core%20Standalone-brightgreen)]()

</div>

---

## 💡 为什么长篇创作需要 StoryOS？

传统 AI 辅助写作在面对数十万至百万字体量时，几乎必然出现 **“设定漂移、全知视角剧透、时间线矛盾、幻觉篡改设定”** 等根本性问题。

`C.le. StoryOS` 借鉴了现代操作系统的内核设计哲学：**将故事创作视为确定性状态机，人类作者是唯一的最高仲裁者。**

本项目为独立拆分后的 StoryOS 核心仓库，从早期探索分支中彻底解耦，专注于提供高内聚、高确定性的长篇创作底层基础设施。

---

## ✨ 核心原则 (Core Principles)

* **📜 人类可读为唯一正典 (Canonical Truth)**：手稿（Manuscript）与设定文件（Canon）以人类可读的结构化纯文本为唯一真理源。
* **⏳ 事件溯源状态机 (Event Sourcing)**：故事状态由追加式的「故事事件（Story Events）」动态投影计算生成，支持时间轴严格溯源与分支推演。
* **🛡️ 角色认知与防剧透门控 (Knowledge Gate)**：在语义检索前，前置强制执行时空坐标、视角权威度与知识范围校验，杜绝角色出现超前认知或视角穿帮。
* **✍️ 候选提案与作者终审 (Author Approval Flow)**：AI 仅具备「候选事实提案（Candidate Claims）」权限，绝不能直接修改正典，必须经人类作者显式审批。
* **🗄️ 索引随时可重建**：SQLite 索引、缓存与向量 Embedding 均为衍生运行时数据，随时可从纯文本正典中无损重建。

---

## 🛠️ 主链路架构

```text
Human Author (Final Authority)
       ↓ Approval
+-------------------------------------------------------------+
|                     StoryOS Core Engine                     |
|                                                             |
|   [Canon Plain Text]  <--- Event Sourcing ---> [Story Events]|
|           ↓                                                 |
|   [Knowledge & Spoiler Gate] (Strict Timeline Check)        |
|           ↓                                                 |
|   [Context Compilation]                                     |
|           ↓                                                 |
|   [AI Assistant] ---> Proposes [Candidate Claims] (Pending) |
+-------------------------------------------------------------+
```

---

## 📄 许可证 / License

本项目代码目前公开可见供技术交流。暂未选择开源许可证，未经授权不得用于商业闭源分发。
