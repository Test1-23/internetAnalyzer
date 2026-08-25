import re
import socket
import subprocess
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from .monitor import _decode

ARP_PAT = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F]{2}(?:-[0-9a-fA-F]{2}){5})\s+")
GATEWAY_PORTS = (53, 80, 443, 22, 445, 8080)
PROBE_TIMEOUT = 0.4


class NodeProfiler:
    def __init__(self, monitor, netinfo, pathmon, storage, interval=30.0):
        self.monitor = monitor
        self.netinfo = netinfo
        self.pathmon = pathmon
        self.storage = storage
        self.interval = interval
        self.nodes = {}
        self._mac_ips = defaultdict(set)
        self._known = {}
        if storage:
            try:
                self._known = storage.load_lan_nodes()
            except Exception:
                self._known = {}
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=24)

    def start(self):
        threading.Thread(target=self._run, name="nodeprofiler", daemon=True).start()

    def stop(self):
        self._stop.set()

    def get_nodes(self):
        return sorted(self.nodes.values(),
                      key=lambda n: -n.get("gateway_score", 0))

    def _run(self):
        while not self._stop.wait(3.0):
            try:
                self.profile()
            except Exception:
                pass
            self._stop.wait(self.interval)

    @staticmethod
    def _arp_entries():
        p = subprocess.run(["arp", "-a"], capture_output=True, timeout=8)
        text = _decode(p.stdout)
        entries = {}
        for line in text.splitlines():
            m = ARP_PAT.match(line)
            if not m:
                continue
            ip, mac = m.group(1), m.group(2).lower()
            if ip.startswith(("224.", "239.", "255.", "127.")) or ip.endswith(".255"):
                continue
            if mac == "ff-ff-ff-ff-ff-ff" or mac.startswith("01-00-5e"):
                continue
            entries[ip] = mac
        return entries

    def _probe_ports(self, ip):
        open_ports = []
        for port in GATEWAY_PORTS:
            try:
                with socket.create_connection((ip, port), timeout=PROBE_TIMEOUT):
                    open_ports.append(port)
            except OSError:
                pass
            except Exception:
                break
        return open_ports

    def profile(self):
        now = time.time()
        arp = self._arp_entries()
        ni = self.netinfo.get() or {}
        gw = self.monitor.gateway
        dns_servers = set(ni.get("dns_servers") or [])
        internal_dns = {d for d in dns_servers if d in arp or (gw and d.startswith(_subnet(gw)))}
        trace = self.pathmon.get_latest()
        hop1 = trace["hops"][0]["ip"] if trace and trace.get("hops") else None

        for ip, mac in arp.items():
            self._mac_ips[mac].add(ip)

        candidates = set(arp.keys()) | ({gw} if gw else set()) | internal_dns
        candidates = {c for c in candidates if not c.startswith(("198.18.", "169.254.", "26."))}

        port_futs = {ip: self._executor.submit(self._probe_ports, ip) for ip in candidates}
        port_results = {}
        for ip, fut in port_futs.items():
            try:
                port_results[ip] = fut.result(timeout=5)
            except Exception:
                port_results[ip] = []

        prev_nodes = self.nodes
        nodes = {}
        for ip in candidates:
            mac = arp.get(ip)
            score = 0
            reasons = []
            if gw and ip == gw:
                score += 30
                reasons.append("默认路由指向该节点")
            if ip in internal_dns:
                score += 25
                reasons.append("为本机提供DNS服务")
            if hop1 and ip == hop1:
                score += 15
                reasons.append("路由路径第一跳")
            if mac and len(self._mac_ips.get(mac, set())) > 1:
                score += 10
                reasons.append(f"同一MAC关联{len(self._mac_ips[mac])}个IP（转发特征）")
            ports = port_results.get(ip, [])
            if 53 in ports:
                score += 10
                reasons.append("53/DNS端口开放")
            if 80 in ports or 443 in ports or 8080 in ports:
                score += 10
                reasons.append("开放Web管理端口")
            if 22 in ports:
                score += 3
                reasons.append("22/SSH端口开放")
            if 445 in ports and not (80 in ports or 443 in ports or 53 in ports):
                score -= 15
                reasons.append("445/SMB开放（更像普通主机）")

            if score >= 50 or (gw and ip == gw and (80 in ports or 443 in ports or 22 in ports)):
                role = "gateway"
            elif score >= 30:
                role = "likely_gateway"
            elif 53 in ports:
                role = "dns_server"
            else:
                role = "host"

            old = prev_nodes.get(ip) or self._known.get(ip) or {}
            first_seen = old.get("first_seen") or now
            node = {
                "ip": ip,
                "mac": mac,
                "role": role,
                "gateway_score": score,
                "reasons": reasons,
                "open_ports": ports,
                "first_seen": first_seen,
                "last_seen": now,
            }
            nodes[ip] = node
            self._known[ip] = node
            if self.storage:
                self.storage.upsert_lan_node(node)
        self.nodes = nodes


def _subnet(ip):
    parts = ip.split(".")
    return ".".join(parts[:3])
