"""调度器：每个助手一条串行队列 + 随机拟人延迟 + 每日限额 + 安静时段 + 熔断。

回声模式：延迟取 echo 配置（秒级），限额/安静时段同样生效但无实际网络影响。
真实模式：延迟取平台配置（45~300 秒），由 main 注入各平台 daily_cap。
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta

logger = logging.getLogger("scheduler")


def _parse_hhmm(s: str):
    h, m = s.split(":")
    return int(h), int(m)


class Scheduler:
    def __init__(self, storage, broadcast, platform_cfg: dict[str, dict],
                 quiet_hours=None):
        self.storage = storage
        self.broadcast = broadcast          # async fn(dict) -> None
        self.platform_cfg = platform_cfg    # name -> {min_delay, max_delay, daily_cap}
        self.adapters: dict[str, object] = {}
        self.queues: dict[str, asyncio.Queue] = {
            name: asyncio.Queue() for name in platform_cfg}
        self._workers: list[asyncio.Task] = []
        self.state: dict[str, dict] = {
            name: {"paused": False, "reason": "", "sent_today": 0, "date": ""}
            for name in platform_cfg}
        self.cancelled: set[int] = set()   # 用户请求取消的消息 id
        self.quiet_hours = None
        if quiet_hours and len(quiet_hours) == 2:
            try:
                self.quiet_hours = (_parse_hhmm(quiet_hours[0]),
                                    _parse_hhmm(quiet_hours[1]))
            except Exception:
                self.quiet_hours = None

    # ---- 注册与生命周期 ----

    def register_adapter(self, name: str, adapter):
        self.adapters[name] = adapter

    def start(self):
        for name in self.queues:
            self._workers.append(asyncio.create_task(self._worker(name)))

    async def stop(self):
        for t in self._workers:
            t.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)

    async def enqueue(self, platform: str, item: dict):
        await self.queues[platform].put(item)

    # ---- 状态与熔断 ----

    def resume(self, platform: str):
        st = self.state.get(platform)
        if st:
            st["paused"] = False
            st["reason"] = ""
            logger.info("platform %s resumed", platform)

    def cancel(self, msg_id: int):
        """标记消息为取消；worker 在发送前检查，若尚未开始处理则跳过。"""
        self.cancelled.add(msg_id)

    def set_quiet_hours(self, quiet_hours):
        """设置页热更新安静时段。"""
        if quiet_hours and len(quiet_hours) == 2:
            try:
                self.quiet_hours = (_parse_hhmm(quiet_hours[0]),
                                    _parse_hhmm(quiet_hours[1]))
            except Exception:
                pass

    def snapshot(self) -> list[dict]:
        out = []
        for name, cfg in self.platform_cfg.items():
            st = self.state[name]
            out.append({"name": name, "paused": st["paused"], "reason": st["reason"],
                        "sent_today": st["sent_today"], "daily_cap": cfg.get("daily_cap"),
                        "queue_len": self.queues[name].qsize()})
        return out

    def _quiet_seconds(self) -> float:
        """当前处于安静时段则返回距时段结束的秒数，否则 0。支持跨零点窗口。"""
        if not self.quiet_hours:
            return 0
        now = datetime.now()
        (h1, m1), (h2, m2) = self.quiet_hours
        start = now.replace(hour=h1, minute=m1, second=0, microsecond=0)
        end = now.replace(hour=h2, minute=m2, second=0, microsecond=0)
        if start <= end:
            if start <= now < end:
                return (end - now).total_seconds()
        else:
            if now >= start:
                return (end + timedelta(days=1) - now).total_seconds()
            if now < end:
                return (end - now).total_seconds()
        return 0

    # ---- 工作循环 ----

    async def _worker(self, platform: str):
        cfg = self.platform_cfg.get(platform, {})
        st = self.state[platform]
        while True:
            item = await self.queues[platform].get()
            user_msg_id = item["msg_id"]
            reply_id = None

            async def mark_cancelled():
                self.cancelled.discard(user_msg_id)
                await self.storage.update_message(user_msg_id, status="cancelled")
                await self.broadcast({"type": "message_update", "id": user_msg_id,
                                      "status": "cancelled"})
                logger.info("msg %s cancelled before send on %s", user_msg_id, platform)

            async def fail(msg: str, fatal: bool = False):
                nonlocal reply_id
                logger.warning("msg %s on %s failed%s: %s",
                               user_msg_id, platform,
                               " (circuit)" if fatal else "", msg)
                if fatal:
                    st["paused"] = True
                    st["reason"] = msg
                    await self.broadcast({"type": "platform_state", "platform": platform,
                                          "paused": True, "reason": msg})
                if reply_id is not None:
                    content = f"[发送失败] {msg}"
                    await self.storage.update_message(reply_id, content=content,
                                                      status="failed")
                    await self.broadcast({"type": "message_update", "id": reply_id,
                                          "status": "failed", "content": content})
                await self.storage.update_message(user_msg_id, status="failed")
                await self.broadcast({"type": "message_update", "id": user_msg_id,
                                      "status": "failed"})

            try:
                # 取消检查（入队后、处理前）
                if user_msg_id in self.cancelled:
                    await mark_cancelled()
                    continue
                # 快速失败：熔断 / 日限额（不消耗队列时间）
                if st["paused"]:
                    await fail(f"渠道已熔断：{st['reason']}（侧栏点击该助手可恢复）")
                    continue
                today = datetime.now().strftime("%Y-%m-%d")
                if st["date"] != today:
                    st["date"] = today
                    st["sent_today"] = 0
                cap = cfg.get("daily_cap")
                if cap and st["sent_today"] >= cap:
                    await fail(f"已达今日发送上限（{cap} 条），明日自动恢复")
                    continue

                # 安静时段：等到时段结束再发送
                wait = self._quiet_seconds()
                if wait > 0:
                    end_dt = datetime.now() + timedelta(seconds=wait)
                    await self.broadcast({"type": "platform_info", "platform": platform,
                                          "text": f"安静时段，暂停发送至 {end_dt:%H:%M}"})
                    await asyncio.sleep(wait)

                adapter = self.adapters.get(platform)
                if adapter is None:
                    await fail(f"{platform} 适配器尚未实现")
                    continue

                delay = random.uniform(cfg.get("min_delay", 1.0),
                                       cfg.get("max_delay", 3.0))
                await asyncio.sleep(delay)

                # 取消检查（拟人延迟期间用户可能取消）
                if user_msg_id in self.cancelled:
                    await mark_cancelled()
                    continue

                await self.storage.update_message(user_msg_id, status="typing")
                await self.broadcast({"type": "message_update", "id": user_msg_id,
                                      "status": "typing"})

                reply_id = await self.storage.add_message(
                    item["conv_type"], platform, "assistant", platform,
                    "", status="streaming")
                await self.broadcast({"type": "new_message",
                                      "message": await self.storage.get_message(reply_id)})

                async def on_delta(content: str):
                    await self.storage.update_message(reply_id, content=content)
                    await self.broadcast({"type": "message_update", "id": reply_id,
                                          "content": content})

                async def on_done():
                    st["sent_today"] += 1
                    await self.storage.update_message(reply_id, status="done")
                    await self.storage.update_message(user_msg_id, status="done")
                    await self.broadcast({"type": "message_update", "id": reply_id,
                                          "status": "done"})
                    await self.broadcast({"type": "message_update", "id": user_msg_id,
                                          "status": "done"})

                async def on_error(err: str, fatal: bool = False):
                    await fail(err, fatal=fatal)

                async def is_cancelled():
                    return user_msg_id in self.cancelled

                async def on_cancel():
                    await mark_cancelled()

                await adapter.send(item["text"], on_delta, on_done, on_error,
                                   is_cancelled=is_cancelled, on_cancel=on_cancel)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # 任何异常转人工可见，绝不自动重试
                await fail(str(e))
            finally:
                self.queues[platform].task_done()
