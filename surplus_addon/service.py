"""Independent monitor with one evaluation in flight and a separate research ledger."""

import asyncio
from datetime import timedelta
from pathlib import Path
from time import monotonic

from hedge.models import utcnow
from hedge.supervisor import Supervisor

from .controller import evaluate
from .ledger import Ledger
from .sources import AppSource, DemoSource


class Advisor(Supervisor):
    @staticmethod
    def context(state):
        result = {
            k: state.get(k)
            for k in (
                "snapshot_id",
                "evidence",
                "execution_enabled",
                "state",
                "target_estimated",
                "current_gap",
                "predicted_closing_gap",
                "buffer",
                "needed_reduction",
                "required_loss_per_second",
                "capacity",
                "budget",
                "completion_calibrated",
                "seconds_for_new_cycles",
            )
        }
        result["simulated_loss_per_second"] = (state.get("stats") or {}).get("observed_loss_per_second")
        result["modeled_loss_per_second"] = (state.get("proposal") or {}).get("modeled_loss_per_second")
        return result


class Service:
    def __init__(
        self,
        settings,
        ledger_path="data/surplus-addon/research.sqlite",
        demo=False,
        transport=None,
        ai_path="config/local.surplus-ai.json",
    ):
        self.settings = settings
        self.ledger = Ledger(ledger_path)
        self.reader = AppSource(settings, transport)
        self.demo = DemoSource() if demo else None
        self.enabled = True
        self.engaged = False
        self.packet = None
        self.decision = {
            "state": "WAITING",
            "reason": "Waiting for the first snapshot",
            "execution_enabled": False,
        }
        self.advisor = Advisor(settings_path=ai_path)
        if not Path(ai_path).exists():
            self.advisor.active = False  # Separate explicit opt-in; avoid unsolicited API expense.
        self.advice = None
        self.lock = asyncio.Lock()
        self.tasks = []

    async def start(self):
        await self.tick()
        self.tasks = [asyncio.create_task(self._loop()), asyncio.create_task(self._advice_loop())]

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.reader.close()
        self.ledger.close()

    async def _loop(self):
        while True:
            started = monotonic()
            await self.tick()
            await asyncio.sleep(max(0.1, self.settings.monitor_seconds - (monotonic() - started)))

    async def tick(self):
        async with self.lock:
            return await self._tick()

    async def _tick(self):
        if not self.enabled:
            self.decision = {
                "state": "OFF",
                "reason": "Add-on monitoring paused; main app unaffected",
                "execution_enabled": False,
                "proposal": None,
            }
            return self.decision
        try:
            packet = await (self.demo or self.reader).fetch()
            now = self.demo.now if self.demo else utcnow()
            evidence = "synthetic" if packet.snapshot.source == "synthetic" else "shadow"
            day = packet.manifest.active_session(now)
            stats = self.ledger.stats(day, packet.snapshot.account, evidence)
            decision = await asyncio.to_thread(evaluate, self.settings, packet, stats, now, self.engaged)
            self.ledger.record(now, evidence, decision)
            self.packet, self.decision = packet, decision
            self.engaged = decision["engaged"]
            return decision
        except Exception as exc:
            # Never keep a previous eligible proposal after an unavailable/invalid input or ledger failure.
            self.packet, self.engaged = None, False
            self.decision = {
                "state": "WAITING",
                "reason": "Snapshot, valuation or research ledger unavailable/invalid",
                "execution_enabled": False,
                "proposal": None,
                "diagnostic_type": type(exc).__name__,
            }
            return self.decision

    async def control(self, action):
        async with self.lock:
            if action == "pause":
                self.enabled, self.engaged = False, False
            elif action == "start":
                self.enabled = True
            elif action in ("main_feed", "new_demo"):
                self.demo = DemoSource() if action == "new_demo" else None
                self.enabled, self.engaged, self.advice = True, False, None
            elif action == "advance_demo":
                if self.demo is None:
                    raise ValueError("Only the fictional rehearsal clock can be advanced")
                self.demo.now += timedelta(minutes=1)
            elif action == "simulate_cycle":
                if not self.enabled or self.demo is None:
                    raise ValueError("Simulation requires an active fictional rehearsal")
                await (
                    self._tick()
                )  # Revalidate current budgets, risk and forecast, not the browser's proposal.
                if self.decision["state"] != "PROPOSED":
                    raise ValueError(self.decision["reason"])
                cycle = self.demo.simulate(self.packet, self.decision["proposal"])
                if self.ledger.cycle(cycle):
                    self.demo.apply(cycle)
            else:
                raise ValueError("Unknown research control; broker trading is unavailable")
            await self._tick()
            return self.state()

    async def _advice_loop(self):
        while True:
            try:
                if self.enabled:
                    decision = dict(self.decision)
                    advice = await self.advisor.review(decision)
                    if advice and self.enabled:
                        self.advice = {**advice, "input_hash": decision.get("input_hash")}
            except ValueError:
                pass
            await asyncio.sleep(30)

    def state(self):
        return {
            "source": "synthetic rehearsal" if self.demo else "main app read-only feed",
            "enabled": self.enabled,
            "execution_enabled": False,
            "decision": self.decision,
            "history": self.ledger.history(),
            "cycles": self.ledger.cycles(),
            "ai": self.advisor.status(),
            "advice": self.advice,
            "advice_stale": bool(
                self.advice and self.advice["input_hash"] != self.decision.get("input_hash")
            ),
        }
