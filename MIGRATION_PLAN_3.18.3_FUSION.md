# 迁移到官方 3.18.3 + 融合课改造实施方案

> 当前分支 `feature/fusion-shared-course` 的 `fusion.py` 是基于**旧版 3.x 架构**（`Autocar.py` 内部 `main()`/`working_loop`）编写的，而官方 3.18.3 已将主循环重构进 `modules/course_runner.py` 的 `run_course()`，并内置了 `FUSION_CATALOG`。本方案给出把融合课改造平滑迁移到官方新架构的完整做法。

---

## 1. 背景与目标

### 1.1 为什么迁移有价值
- **环境门槛被官方系统性解决**：cv2 / opencv 依赖按 Python 版本选择、`python3.dll` 打包修复、`pyinstaller` PATH 引导、`uv`/macOS 一键部署——都是本地 exe 在真实机上反复踩的坑。
- **架构对齐**：官方 `run_course()` 已是标准主循环（目录检测 → 课时过滤 → 学习/复习 → 结果日志）。把融合课嵌入这套，代码不再与官方"分叉死支"。
- **官方已有 `FUSION_CATALOG`**：目录识别部分官方已经支持，融合课改造只需**补齐播放 + AI 随堂**部分。

### 1.2 官方已支持到什么程度（实测结论）
| 能力 | 官方 3.18.3 | 说明 |
|---|---|---|
| 融合目录识别 | ✅ `FUSION_CATALOG` | `.chapter-content`、`.finish-icon`、`.item-name` |
| 自动点下一课 | ✅ `run_course()` 循环 `lesson.click()` | 通用课时循环 |
| **AI 随堂练习弹窗自动选答** | ❌ **缺失** | 你 `fusion.py` 的 `skip_ai_interactive()` 正是补这个 |
| 融合页自适应稳定（`_expand_all` 折叠展开） | ⚠️ 部分 | 官方通用循环不含"展开折叠目录" |
| 播放推进稳健性（`_ensure_playing` + `99.5%判完成`） | ⚠️ 通用 | 官方 `learn_lesson` 是旧版 video 判稳 |

---

## 2. 迁移策略（二选一，取 A）

### 策略 A：织入官方 `run_course`（推荐）
把 fusion 课作为官方一套 Catalog 接入，**`run_course` 不变**，仅替换/增补"目录获取 + 课时进度判断 + 弹窗处理"。
- 优点：官方架构不打洞，后续官方升级易跟随；可直接走官方 `learn_lesson/completion` 度量。
- 改动面小、语义干净；你的 fusion 逻辑变成"官方上课的融合增强层"。

### 策略 B：官方旁路独立 `FusionLoop`（不推荐，已在旧版）
- 保留 `fusion.py` 整体独立循环，官方 `run_course` 对 fusion URL 直接走 `FusionLoop`（类似现 `Autocar.py` 里的 if/else）。
- 缺点：复制一套"目录/跳过/通信"逻辑，与官方 `FUSION_CATALOG` 并存但分叉，后续上游改动难跟随 → 维护税高，且违背"重构到官方最新版"的初衷。

**本方案按策略 A 编写**。

---

## 2. 官方架构概览（迁移要对接的界面）

```
run_course(page, catalog, config, logger, playback_enabled)  # course_runner.py
  ├─ get_filtered_class(page, catalog)          → List[Locator] 去已完成节之
  ├─ for lesson in lessons:
  │    lesson.click()                 -> 点开小节
  │    wait_for_lesson_active(lesson, catalog)
  │    get_lesson_name(page, lesson, catalog)
  │    lesson_progress(lesson, catalog)          # 0..100
  │    learn_lesson(page, ...)                   # course_playback.py 主播放
  │      ├─ skip / AI 弹窗处理（你补）
  │      └─ video 完成判定
  └─ 返回 CourseOutcome.COMPLETED / TIME_LIMIT / FAILED
```

核心对接面：
1. `lesson_navigation.lesson_progress()` —— 已读 `.finish-icon`（融合） → 迁移改用它统一进度
2. `course_playback.learn_lesson()` —— 主播放循环；要在此接入"AI 随堂跳过块"
3. `course_runner.run_course()` —— 不必改结构
4. `lesson_navigation.detect_catalog()` —— 已含 `FUSION_CATALOG`，无需改

---

## 3. 具体改造点清单

### 3.1 目录展开（`_expand_all` → 官方课程启动阶段）
**文件**：`course_runner.detect_catalog_after_verification` 或 `run_course` 开头
**问题**：融合目录的小节默认是**折叠**的（`.el-collapse-item`），不展开则 `get_filtered_class` 拿不全课时。
**改**：在 `run_course()` 首段、`wait_for_selector(catalog.item)` 前后，若 `catalog.name == "fusion"`，对页面折叠头 `await _expand_all()`。

### 3.2 `skip_ai_interactive` —— 接入官方 learn 循环
**：`course_playback.learn_lesson()`
**改**：在官方"每次循环等待完成"的 while 里调用 `adapter.skip_ai_interactive(page, config)`（把 fusion 的 `skip_ai_interactive()` 提升为**通用弹窗适配器**，`FusionAdaptor`）。
> 关键：智慧树 AI 随堂弹窗若未清除，视频会暂停卡死。官方 `learn_lesson` 每次等待前检测到弹窗自动选 A 提交。

### 3.3 完成判定（`_item_done` / `video_percent`）
**现状**：融合用 `_video_percent = cur/dur`，≥99.5 判完成。
**改**：保留官方 `lesson_progress()`（看 `.finish-icon`）为主判，辅以视频比例兜底。**两者并列，避免"视频已播完但平台未回写 finish" 时误判。**

### 3.4 `get_lesson_name`/`course_title`（fusion 不存在的 `. .source-name`）
**改**：官方 `get_lesson_name(page, lesson, catalog)` 已按 `catalog.title`（fusion 为 `.item-name`）取值，不涉及 `.source-name`，已兼容。-。

### 3.5 `Autocar.py` 入口（CLI → run_course）
**改**：官方入口 `Autoca.py` 已经是极简 CLI 调 `run_course`。**移除**你旧版的分支（`fusion_loop` 调点），让 `catalog_candidates` > `detect_catalog` 自动判定 fusion 目录。

---

## 4. 推荐落地（新增的适配模块）

新建 **`modules/fusion_adapter.py`**（复用现有 fusion.py 的测试正确逻辑）：
```python
class FusionAdapter:
    """融合课适配层: 只做官方 run_course 没覆盖的部分。"""
    @staticmethod
    async def expand_catalog(page, catalog): ...   # 折叠目录展开
    @staticmethod
    async def skip_ai_interactive(page, config): ...  # 升迁你的 skip_ai_interactive()
    @staticmethod
    async def completion_check(page, catalog, video):  # 99.5% 兜底判定
```
接入点：`run_course`（展开 + 完成兜底）与 `learn_lesson`（AI 弹窗）。

---

## 5. 工作量与风险

| 项 | 评估 |
|---|---|
| 新增代码 | `fusion_adapter.py`（~80-120 行，主要迁 `_expand_all`/`skip_ai`/`_ensure_playing`/`completion`）|
| 修改官方文件 | `course_playback.py`（接 skip）、`course_runner.py`（fusion 展开+完成兜微信）——**纯属"读 + 插入回调"，不动主流程** |
| 删除 | 旧 `Autocar.py` 手工 fusion 分支（@3.5） |
| 风险 | 中。需真站验证 AI 随堂弹窗与 `_ensure_playing`在当前融合页的稳定性（你已在你 `fusion.py` 验证的逻辑搬过来，稳） |
| 阻断点 | 无（不依赖官方 fork 主 main 重构） |

---

## 6. 验收标准（Done 定义）
后端（不依赖官方新版结构）：
1. `python -m py_compile` 全绿
2. 真实智慧树融合课：首次自动 `run_course` 识别出 `FUSION_CATALOG`，自动点开折叠目录 → 逐节推进
3. 播放中途弹 AI 随堂 → 自动选 A 提交，继续播完
4. 学完全部待学小节 → `COMPLETED`，课程 index 推进到最后一课
5. `configs.ini` 无 `enableAutoCaptcha` 时也能全自动（手动扫码一次后走 cookie）

---

## 7. 负责人与工作分流（可选，供你排期）
- **阶段 1（半天）**：新建 `fusion_adapter.py`，移植 `skip_ai_interactive`/`expand`/`completion`（纯搬运+改造）
- **阶段 2（半天）**：在 `run_course` 与 `learn_lesson` 插入 3~5 行接入；删除旧分支
- **阶段 3（半天）**：真站端到端（真机登录一次），跑验收 1–5
- **总预计**：1.5 个工作日（含真机验证）

---

## 8. 附：选择哪个基线
- 若你要"最小改动、最快上手" → 直接以官方当前 `fork` main（`409f8f`，已是 3.18.3）为基线，在其上方实现本方案。
- 若你要"保所有官方新功能"→ 同样用 `origin/main`（4096f3）基线，**不要把旧 3.x 的交付物硬 rebase**。

reb旧版融合分支的取舍：**保留 `feature/fusion`（已存为 `feature/` 分支）作为历史，本方案在新分支上重建**，避免无谓 rebase 冲突。