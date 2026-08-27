import re
import threading
import time

from .netinfo import _ps_json

VIRTUAL_NIC_PAT = re.compile(r"clash|tun|tap|wintun|wireguard|openvpn|zerotier|radmin|vmware|vmnet|loopback|teredo", re.I)


class NicDiag:
    def __init__(self, monitor, interval=60.0):
        self.monitor = monitor
        self.interval = interval
        self.data = {}
        self._stop = threading.Event()
        self._last_err = None
        self._err_rate = 0.0

    def start(self):
        threading.Thread(target=self._run, name="nicdiag", daemon=True).start()

    def stop(self):
        self._stop.set()

    def get(self):
        return self.data

    def _run(self):
        while not self._stop.wait(3.0):
            try:
                self.collect()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def _pick_nic(self, drivers):
        def phys(row):
            return row.get("MediaConnectState") == 1 and not VIRTUAL_NIC_PAT.search(str(row.get("Name") or ""))
        connected = [d for d in drivers if phys(d)]
        monitor_nic = self.monitor.nic
        if monitor_nic and not VIRTUAL_NIC_PAT.search(monitor_nic):
            for d in drivers:
                if d.get("Name") == monitor_nic:
                    return d["Name"]
        if connected:
            return connected[0]["Name"]
        return monitor_nic

    def collect(self):
        now = time.time()
        stats = _ps_json(
            "Get-NetAdapterStatistics | Select-Object Name, ReceivedPacketErrors, "
            "OutboundPacketErrors, ReceivedDiscards, OutboundDiscards | ConvertTo-Json -Compress")
        drivers = _ps_json(
            "Get-NetAdapter | Select-Object Name, DriverVersion, DriverDate, "
            "DriverDescription, LinkSpeed, MediaConnectState | ConvertTo-Json -Compress")
        hw = _ps_json(
            "Get-NetAdapterHardwareInfo | Select-Object Name, Bus, Device, Function "
            "| ConvertTo-Json -Compress")
        power = _ps_json(
            "Get-NetAdapterPowerManagement | Select-Object Name, "
            "AllowComputerToTurnOffDevice | ConvertTo-Json -Compress")

        if isinstance(drivers, dict):
            drivers = [drivers]
        nic = self._pick_nic(drivers)

        def pick(lst, name):
            if isinstance(lst, dict):
                lst = [lst]
            for row in lst or []:
                if row.get("Name") == nic:
                    return row
            return {}

        st = pick(stats, nic)
        drv = pick(drivers, nic)
        hwinfo = pick(hw, nic)
        pwr = pick(power, nic)

        err_total = (st.get("ReceivedPacketErrors") or 0) + (st.get("OutboundPacketErrors") or 0)
        if self._last_err is not None:
            self._err_rate = max(0, err_total - self._last_err)
        self._last_err = err_total

        findings = []
        score = 100

        if self._err_rate > 10:
            score -= 25
            findings.append({"level": "high",
                             "text": f"硬件错误包增速 {self._err_rate}/s，疑似网卡/驱动异常或信号完整性问题"})
        elif self._err_rate > 0:
            score -= 8
            findings.append({"level": "medium",
                             "text": f"存在少量错误包（{self._err_rate}/s），观察即可"})

        drv_date = drv.get("DriverDate")
        drv_age_years = None
        if drv_date:
            try:
                t = time.mktime(time.strptime(str(drv_date)[:10], "%Y-%m-%d"))
                drv_age_years = (now - t) / 86400 / 365.25
                if drv_age_years > 5:
                    score -= 10
                    findings.append({"level": "medium",
                                     "text": f"驱动发布于 {drv_age_years:.0f} 年前（{drv_date}），"
                                             "过老驱动是稳定性隐患，建议到厂商官网更新"})
                elif drv_age_years > 3:
                    score -= 4
                    findings.append({"level": "low",
                                     "text": f"驱动已 {drv_age_years:.0f} 年未更新（{drv_date}）"})
            except ValueError:
                pass

        if self.monitor._speed_drop_ts and now - self.monitor._speed_drop_ts < 1800:
            score -= 15
            findings.append({"level": "medium",
                             "text": "近期发生链路速率协商骤降，无线场景多为信号劣化，"
                                     "有线场景检查网线/对端端口"})

        flaps = [l for l in self.monitor.link_history if l["ts"] / 1000 >= now - 1800]
        if len(flaps) >= 2:
            score -= min(20, len(flaps) * 7)
            findings.append({"level": "high",
                             "text": f"近30分钟链路切换 {len(flaps)} 次：若为无线，"
                                     "可能与省电设置有关；建议在设备管理器关闭该网卡「允许计算机关闭此设备以节约电源」"})
        pwr_off = pwr.get("AllowComputerToTurnOffDevice")
        if pwr_off in (1, True):
            findings.append({"level": "low",
                             "text": "电源管理允许系统关闭网卡以节能（间歇性掉线时可尝试关闭）"})

        if not findings:
            findings.append({"level": "low", "text": "未发现硬件层异常特征"})

        sensors = {
            "voltage": None,
            "temperature": None,
            "note": "网卡电压/温度传感器需厂商驱动（如 Intel PROSet/供应商SDK）暴露，"
                    "Windows 标准接口不提供；已用错误计数、驱动状态与链路协商作为硬件健康代理指标",
        }

        self.data = {
            "ts": now,
            "nic": nic,
            "health": max(0, min(100, score)),
            "err_rate": self._err_rate,
            "err_total": err_total,
            "driver": {
                "version": drv.get("DriverVersion"),
                "date": str(drv.get("DriverDate") or "")[:10] or None,
                "description": drv.get("DriverDescription"),
                "age_years": round(drv_age_years, 1) if drv_age_years is not None else None,
            },
            "link": {
                "speed": drv.get("LinkSpeed"),
                "connected": drv.get("MediaConnectState") == 1,
            },
            "hwinfo": {k: hwinfo.get(k) for k in ("Bus", "Device", "Function")},
            "findings": findings,
            "sensors": sensors,
        }
