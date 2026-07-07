"""System monitoring — CPU/RAM/disk/battery/network via psutil.

On-demand summaries only. Threshold alerts (low battery, high CPU) surface as
DASHBOARD NOTIFICATIONS ONLY (never spoken unprompted) via the scheduler.
"""
from __future__ import annotations

from core import notifications
from core.skill_registry import register_skill
from skills.base import Skill, prop, tool


@register_skill
class SystemMonitorSkill(Skill):
    name = "system_monitor"
    description = "Report CPU, memory, disk, battery, and network status on request."

    def tools(self) -> list[dict]:
        return [
            tool("system_status", "Get a summary of CPU, RAM, disk, and battery usage."),
            tool("resource_detail", "Get detail on one resource: cpu, memory, disk, battery, or network.",
                 {"resource": prop("string", "One of: cpu, memory, disk, battery, network")}, ["resource"]),
        ]

    def execute(self, tool: str, args: dict) -> str:
        import psutil  # lazy
        try:
            if tool == "system_status":
                return self._status(psutil)
            return self._detail(psutil, str(args.get("resource", "")).lower())
        except Exception as exc:
            return f"Couldn't read system stats: {exc}"

    def _status(self, psutil) -> str:
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\")
        parts = [f"CPU {cpu:.0f}%", f"RAM {mem.percent:.0f}% ({mem.used // 2**30}/{mem.total // 2**30} GB)",
                 f"Disk C: {disk.percent:.0f}% used"]
        batt = psutil.sensors_battery()
        if batt:
            parts.append(f"Battery {batt.percent:.0f}%{' (charging)' if batt.power_plugged else ''}")
        self.log("system_status")
        return "System status: " + ", ".join(parts) + "."

    def _detail(self, psutil, resource: str) -> str:
        self.log("resource_detail", {"resource": resource})
        if resource == "cpu":
            per = psutil.cpu_percent(interval=0.5, percpu=True)
            return f"CPU {psutil.cpu_percent()}% overall across {len(per)} cores: " + \
                   ", ".join(f"{p:.0f}%" for p in per)
        if resource == "memory":
            m = psutil.virtual_memory()
            return f"RAM: {m.used // 2**30} GB used of {m.total // 2**30} GB ({m.percent:.0f}%), {m.available // 2**30} GB free."
        if resource == "disk":
            out = []
            for part in psutil.disk_partitions():
                try:
                    u = psutil.disk_usage(part.mountpoint)
                    out.append(f"{part.device} {u.percent:.0f}% ({u.free // 2**30} GB free)")
                except PermissionError:
                    continue
            return "Disks: " + "; ".join(out)
        if resource == "battery":
            b = psutil.sensors_battery()
            if not b:
                return "No battery detected (desktop?)."
            mins = b.secsleft // 60 if b.secsleft > 0 else None
            return f"Battery {b.percent:.0f}%, {'charging' if b.power_plugged else 'on battery'}" + \
                   (f", ~{mins} min left." if mins else ".")
        if resource == "network":
            n = psutil.net_io_counters()
            return f"Network: {n.bytes_sent // 2**20} MB sent, {n.bytes_recv // 2**20} MB received since boot."
        return "Ask about cpu, memory, disk, battery, or network."

    # -- background threshold check (scheduler wires this; dashboard-only) --
    def check_thresholds(self) -> None:
        import psutil
        try:
            batt = psutil.sensors_battery()
            if batt and not batt.power_plugged and batt.percent <= 15:
                notifications.notify("Low battery", f"Battery at {batt.percent:.0f}% and unplugged.", "system_monitor")
        except Exception:
            pass
