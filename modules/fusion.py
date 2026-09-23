# encoding=utf-8
"""智慧树【新版融合共享课】适配器 (方案B)。

背景
----
Autorisor 原版"共享课"支持基于旧版拆分播放页(studyvideoh5)，其选择器为
`.clearfix.video` / `.current_play` / `.time_icofinish` / `.progress-num`。
智慧树新版融合课(URL 形如 `wisdom-mooc.zhihuishu.com/study/index?...`)用了
Element-UI + video.js 的完全不同的结构，旧适配器匹配不到 → 表现为
"只刷一个 URL、不切小一、反复重播"。

本模块为该新版融合课提供独立适配器：
- 小节集合 = `.chapter-item`(含二级 `.chapter-content-second`)
- 当前小节 = `.chapter-item.current`
- 已完成   = 小节内存在 `img.finish-icon`
- 进度权威 = 页面上 `video.vjs-tech` 的 currentTime/duration
- 切集     = 点击下一个小节元素

用法
----
识别到新融合页时由 Autorisor.py 调用 FusionLoop(page, config).run()。
AI 随堂练习弹窗以 `skip_ai_interactive` 预留占位，最后阶段再接。
"""
import time as _time
from playwright.async_api import Page
from modules.logger import Logger
from modules.configs import Config
from modules.progress import show_course_progress

logger = Logger()

FUSION_URL_HINTS = ("zhihuishu.com/study/index", "wisdom-mooc.zhihuishu.com/study")


def is_new_fusion_url(url: str) -> bool:
    """URL 是否为新版融合共享课。"""
    return any(hint in (url or "") for hint in FUSION_URL_HINTS)


async def is_new_fusion_page(page) -> bool:
    """页面 DOM 是否带新版融合课结构特征。"""
    try:
        return await page.evaluate(
            "()=>!!(document.querySelector('.chapter-item')||document.querySelector('.el-collapse-item'))"
        )
    except Exception:
        return False


class FusionLoop:
    """新版融合共享课自动学习主循环。"""

    def __init__(self, page, config: Config):
        self.page: Page = page
        self.config = config if config is not None else Config()
        self.start_time = 0.0
        self.paused_time = 0.0

    # ---------- 读取/点击 ----------
    async def read_video(self):
        try:
            return await self.page.evaluate("""()=>{
                const v=document.querySelector('video');
                if(!v) return null;
                return {dur: v.duration||0, cur: v.currentTime||0, paused: v.paused};
            }""")
        except Exception:
            return None

    async def _expand_all(self):
        try:
            await self.page.evaluate("""()=>{
                let n=0;
                document.querySelectorAll('.el-collapse-item').forEach(it=>{
                    if((it.className||'').indexOf('is-active')<0){
                        const h=it.querySelector('.el-collapse-item__header');
                        if(h){ h.click(); n++; }
                    }
                });
                return n;
            }""")
            logger.debug("已尝试展开折叠章节")
        except Exception as e:
            logger.debug(f"展开章节异常: {e}")

    async def _list_items(self, include_all: bool = False):
        items = []
        for loc in (".chapter-item", ".chapter-content-second"):
            try:
                items += await self.page.locator(loc).all()
            except Exception:
                continue
        if include_all or not items:
            return items
        todo = []
        for it in items:
            if not await self._item_done(it):
                todo.append(it)
        return todo if todo else items

    async def _item_done(self, it) -> bool:
        try:
            return await it.locator(".finish-icon, .icon-finish").count() > 0
        except Exception:
            return False

    async def _attr(self, it, name):
        try:
            return await it.get_attribute(name) or ""
        except Exception:
            return ""

    async def _click(self, it):
        try:
            await it.click()
        except Exception as e:
            logger.debug(f"点击小节失败: {e}")

    async def _ensure_playing(self):
        try:
            await self.page.evaluate("""()=>{
                const v=document.querySelector('video');
                if(v){ v.muted=true; v.play().catch(()=>{}); }
                else { const c=document.querySelector('.chapter-item.current'); if(c) c.click(); }
            }""")
        except Exception:
            pass

    # ---------- 进度 ----------
    def _video_percent(self, v) -> float:
        if v and v.get("dur", 0) > 0:
            return min(100.0, v["cur"] / v["dur"] * 100.0)
        return 0.0

    # ---------- AI随堂练习弹窗自动跳过 ----------
    async def skip_ai_interactive(self):
        """检测并跳过 AI 随堂练习弹窗。

        触发机制: 视频 currentTime 到达 nav-item 里的 AI随堂练习时间戳时,
        页面自动暂停视频并弹出 ``.ai-class-exercise-dialog`` 弹窗。
        弹窗内为判断题/选择题, 无关闭/跳过按钮, 但"选任意选项 + 点提交作答"
        即可关闭弹窗(已真站验证)。关闭后需恢复视频播放(由主循环 _ensure_playing 处理)。
        """
        try:
            visible = await self.page.evaluate("""()=>{
              var dlg=document.querySelector('.ai-class-exercise-dialog');
              if(!dlg) return false;
              var p=dlg.parentElement;
              while(p && p!==document.body){
                var st=getComputedStyle(p);
                if(st.display==='none'||st.visibility==='hidden') return false;
                p=p.parentElement;
              }
              return dlg.offsetWidth>0 && getComputedStyle(dlg).display!=='none';
            }""")
        except Exception:
            return
        if not visible:
            return
        logger.info("检测到 AI随堂练习弹窗, 自动选 A 并提交.")
        try:
            await self.page.evaluate("""()=>{
              var opt=document.querySelector('.ai-class-exercise-dialog .option');
              if(opt) opt.click();
            }""")
            await self.page.wait_for_timeout(600)
            await self.page.evaluate("""()=>{
              var btn=document.querySelector('.ai-class-exercise-dialog .el-dialog__footer button.el-button');
              if(btn) btn.click();
            }""")
            await self.page.wait_for_timeout(1000)
            logger.info("AI随堂练习已自动提交关闭.")
            # 弹窗关闭后立即恢复视频播放（弹窗会把视频暂停）
            await self._ensure_playing()
        except Exception as e:
            logger.debug(f"AI随堂练习自动提交失败: {e}")

    # ---------- 主循环 ----------
    async def run(self):
        self.start_time = _time.time()
        self.paused_time = 0.0
        await self._expand_all()
        items = await self._list_items(include_all=False)
        total = len(items)
        logger.info(f"检测到新版融合共享课，Fusion 待学小节数: {total}")
        if total == 0:
            logger.warn("未采集到任何小节，可能是页面未正常加载。")
            return

        limit_max = getattr(self.config, "limitMaxTime", 0)
        limit_s = (limit_max * 60) if limit_max > 0 else 0

        cur_idx = 0
        for i, it in enumerate(items):
            if "current" in await self._attr(it, "class"):
                cur_idx = i
                break

        for i in range(cur_idx, total):
            it = items[i]
            cls = await self._attr(it, "class")
            if await self._item_done(it) and "current" not in cls:
                logger.info(f"小节已完成，跳过: {i+1}/{total}")
                continue
            if "current" not in cls:
                logger.info(f"正在学习第 {i+1}/{total} 个小节…")
                await self._click(it)
                await self.page.wait_for_timeout(1500)
            else:
                logger.info(f"正在学习当前小节 {i+1}/{total}…")

            elapsed = 0.0
            finished = False
            while not finished:
                await self.skip_ai_interactive()
                await self._ensure_playing()
                v = await self.read_video()
                if v is None:
                    finished = True
                    break
                pct = self._video_percent(v)
                if pct >= 99.5:
                    finished = True
                    break
                elapsed += 2.0
                await self.page.wait_for_timeout(2000)
                if limit_s and elapsed >= limit_s:
                    logger.info(f"已达单课限时 {limit_max} min，切换下一门课。")
                    return
                show_course_progress(desc="进度:", cur_time=int(pct))

        logger.info("本门融合共享课全部小节处理完成，提交下一门课逻辑。")


async def fusion_loop(page, config: Config):
    """供 Autovisor.py 调用：检测到新融合 URL 时跑本循环。"""
    loop = FusionLoop(page, config)
    await loop.run()