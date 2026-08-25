import json
import re
import socket
import subprocess
import threading
import time
import urllib.request
import winreg
from pathlib import Path
from socket import AF_INET

import psutil

from .monitor import _decode

PUBLIC_IP_API = "http://ip-api.com/json/?fields=query,country,regionName,city,isp,org,as&lang=zh-CN"
HOSTS_PATH = r"C:\Windows\System32\drivers\etc\hosts"
VIRTUAL_NIC_PAT = re.compile(r"clash|tun|tap|wintun|wireguard|openvpn|zerotier|tailscale|vpn|pppoe", re.I)

PS_NETCFG = (
    "Get-NetIPConfiguration | ForEach-Object { "
    "[pscustomobject]@{ alias=$_.InterfaceAlias; "
    "gw=(@($_.IPv4DefaultGateway).NextHop -join ','); "
    "dns=(@($_.DNSServer.ServerAddresses) -join ','); "
    "ip=$_.IPv4Address.IPAddress; prefix=$_.IPv4Address.PrefixLength } } | ConvertTo-Json -Compress"
)


def _ps_json(script):
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, timeout=15)
        text = _decode(p.stdout).strip()
        if not text:
            return []
        return json.loads(text)
    except Exception:
        return []


class NetInfo:
    def __init__(self):
        self.data = {}
        self.public_ip_changed_ts = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_pub_fetch = 0.0
        self._prev_public_ip = None

    def start(self):
        threading.Thread(target=self._run, name="netinfo", daemon=True).start()

    def stop(self):
        self._stop.set()

    def get(self):
        with self._lock:
            return self.data

    def _run(self):
        while not self._stop.wait(0):
            try:
                info = self.collect()
                with self._lock:
                    self.data = info
            except Exception:
                pass
            self._stop.wait(30)

    def collect(self):
        now = time.time()
        nics_cfg = _ps_json(PS_NETCFG)
        if isinstance(nics_cfg, dict):
            nics_cfg = [nics_cfg]
        cfg_by_alias = {c.get("alias"): c for c in nics_cfg}

        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        io = psutil.net_io_counters(pernic=True)

        interfaces = []
        virtual_names = []
        for name, addr_list in addrs.items():
            st = stats.get(name)
            mac = next((a.address for a in addr_list if a.family == psutil.AF_LINK), None)
            ips = [{"addr": a.address, "netmask": a.netmask}
                   for a in addr_list if a.family == AF_INET]
            if not ips and (st is None or not st.isup):
                continue
            cfg = cfg_by_alias.get(name, {})
            is_virtual = bool(VIRTUAL_NIC_PAT.search(name))
            if is_virtual:
                virtual_names.append(name)
            interfaces.append({
                "name": name,
                "mac": mac,
                "ips": ips,
                "gateway": cfg.get("gw") or None,
                "dns": [d for d in (cfg.get("dns") or "").split(",") if d],
                "speed_mbps": getattr(st, "speed", 0) if st else 0,
                "mtu": getattr(st, "mtu", 0) if st else 0,
                "up": bool(st and st.isup),
                "virtual": is_virtual,
                "total_mb": round((io[name].bytes_sent + io[name].bytes_recv) / 1048576, 1) if name in io else 0,
            })

        dns_servers = []
        seen = set()
        ipv4_pat = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
        for itf in interfaces:
            itf["dns"] = [d for d in itf["dns"] if ipv4_pat.match(d)]
            for s in itf["dns"]:
                if s not in seen:
                    seen.add(s)
                    dns_servers.append(s)

        proxy = self._proxy_info()
        hosts_info = self._hosts_info()

        pub = None
        if now - self._last_pub_fetch >= 600:
            pub = self._fetch_public_ip()
            if pub:
                self._last_pub_fetch = now

        data = {
            "ts": now,
            "hostname": socket.gethostname(),
            "interfaces": interfaces,
            "dns_servers": dns_servers,
            "proxy": proxy,
            "hosts": hosts_info,
            "public_ip": pub,
            "public_ip_changed_ts": self.public_ip_changed_ts,
            "virtual_adapters": virtual_names,
        }
        return data

    @staticmethod
    def _proxy_info():
        out = {"enabled": False, "server": None, "pac": None}
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            out["enabled"] = bool(enabled)
            for val_name, field in (("ProxyServer", "server"), ("AutoConfigURL", "pac")):
                try:
                    v, _ = winreg.QueryValueEx(key, val_name)
                    if v:
                        out[field] = v
                except OSError:
                    pass
        except OSError:
            pass
        return out

    @staticmethod
    def _hosts_info():
        try:
            p = Path(HOSTS_PATH)
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            active = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]
            return {"size": p.stat().st_size, "active_entries": len(active)}
        except OSError:
            return None

    def _fetch_public_ip(self):
        try:
            req = urllib.request.Request(PUBLIC_IP_API, headers={"User-Agent": "net-analyzer"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                j = json.loads(resp.read().decode("utf-8"))
            ip = j.get("query")
            if ip:
                if self._prev_public_ip and self._prev_public_ip != ip:
                    self.public_ip_changed_ts = time.time()
                self._prev_public_ip = ip
                return {"ip": ip, "isp": j.get("isp"), "org": j.get("org"),
                        "as": j.get("as"), "location": f"{j.get('country','')} {j.get('regionName','')} {j.get('city','')}".strip()}
        except Exception:
            return None
        return None
