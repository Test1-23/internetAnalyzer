import http.client
import json
import socket
import ssl
import tempfile
import threading
import time

from .monitor import PROBE_TARGETS

CDN_SIGNATURES = [
    ("cloudflare", ["cf-ray", "server: cloudflare", "__cfduid"]),
    ("cloudfront", ["x-amz-cf-id", "x-amz-cf-pop", "via: .*cloudfront"]),
    ("akamai", ["x-akamai", "server: akamai", "x-cache: .*akamai"]),
    ("fastly", ["x-served-by: .*cache-", "x-fastly"]),
    ("azure cdn", ["x-azure-ref", "via: .*azure"]),
    ("百度云加速", ["yjs-id", "server: yunjiasu"]),
    ("阿里云CDN", ["x-swift-cachetime", "eagleid", "server: tengine"]),
    ("腾讯云CDN", ["x-nws-log-uuid", "x-cache-lookup"]),
    ("又拍云", ["x-upyun"]),
    ("七牛云", ["x-qiniu"]),
]


def _match_cdn(headers_text, geo_org):
    text = headers_text.lower()
    for name, sigs in CDN_SIGNATURES:
        for s in sigs:
            import re
            if re.search(s, text):
                return name
    if geo_org:
        org = geo_org.lower()
        for kw in ("cloudflare", "cloudfront", "akamai", "fastly", "cdn", "content delivery"):
            if kw in org:
                return kw
    return None


def _decode_cert_der(der):
    path = tempfile.mktemp(suffix=".der")
    with open(path, "wb") as f:
        f.write(der)
    try:
        info = ssl._ssl._test_decode_cert(path)
        return {
            "subject": dict(info.get("subject", [])).get("organizationName")
                       or dict(info.get("subject", [])).get("commonName"),
            "issuer": dict(info.get("issuer", [])).get("organizationName")
                      or dict(info.get("issuer", [])).get("commonName"),
            "not_after": info.get("notAfter"),
            "san_count": len(info.get("san", [])) if info.get("san") else 0,
        }
    except Exception:
        return None
    finally:
        import os
        try:
            os.remove(path)
        except OSError:
            pass


def _days_until(hms):
    try:
        t = time.mktime(time.strptime(hms, "%b %d %H:%M:%S %Y %Z"))
        return round((t - time.time()) / 86400)
    except Exception:
        return None


class ServerProfiler:
    def __init__(self, monitor, netinfo, geoip, storage, interval=25.0):
        self.monitor = monitor
        self.netinfo = netinfo
        self.geoip = geoip
        self.storage = storage
        self.interval = interval
        self.profiles = {}
        self._targets = [(name, host) for host, _, name in PROBE_TARGETS]
        self._idx = 0
        if storage:
            try:
                self.profiles = storage.load_server_profiles()
            except Exception:
                self.profiles = {}
        self._stop = threading.Event()
        self._force = threading.Event()

    def start(self):
        threading.Thread(target=self._run, name="srvprobe", daemon=True).start()

    def stop(self):
        self._stop.set()

    def trigger(self, name=None):
        self._next_name = name
        self._force.set()

    def get_profiles(self):
        return list(self.profiles.values())

    def get_profile(self, name):
        return self.profiles.get(name)

    def _run(self):
        while not self._stop.wait(5.0):
            if self._force.is_set():
                self._force.clear()
                name = getattr(self, "_next_name", None)
            else:
                name = None
            if name:
                target = next((t for t in self._targets if t[0] == name), None)
                if target:
                    try:
                        self.profile_target(*target)
                    except Exception:
                        pass
                continue
            if self._targets:
                name, host = self._targets[self._idx % len(self._targets)]
                self._idx += 1
                try:
                    self.profile_target(name, host)
                except Exception:
                    pass
            self._stop.wait(self.interval)

    def profile_target(self, name, host):
        now = time.time()
        ni = self.netinfo.get() or {}
        proxy_active = bool(ni.get("virtual_adapters")) or (ni.get("proxy") or {}).get("enabled")

        ips = []
        try:
            for info in socket.getaddrinfo(host, 443, socket.AF_INET):
                ip = info[4][0]
                if ip not in ips:
                    ips.append(ip)
        except OSError:
            pass

        tcp_ms = self._tcp_time(host, 443)
        cert, tls_ms, connect_ms = None, None, None
        http = {}
        if ips:
            connect_ms, tls_ms, sock = self._tls_connect(host)
            if sock:
                cert = self._get_cert(sock)
                http, ttfb = self._http_over(sock, host)
                if ttfb is not None:
                    http["ttfb_ms"] = ttfb
                try:
                    sock.close()
                except OSError:
                    pass

        headers_text = " ".join(f"{k}: {v}" for k, v in (http.get("headers") or {}).items())
        geo_org = None
        if ips and self.geoip:
            g = self.geoip.get(ips[:1]).get(ips[0])
            if g:
                geo_org = g.get("isp") or ""
                http.setdefault("headers", {})
        cdn = _match_cdn(headers_text, geo_org)

        old = self.profiles.get(name) or {}
        prev_ips = old.get("ips") or []
        drift = len([ip for ip in ips if prev_ips and ip not in prev_ips])

        score = 100
        findings = []
        days = None
        if cert:
            days = _days_until(cert.get("not_after") or "")
            if days is not None:
                if days < 0:
                    score -= 60
                    findings.append({"level": "high",
                                     "text": f"TLS证书已过期 {abs(days)} 天！部分客户端将拒绝连接"})
                elif days < 14:
                    score -= 20
                    findings.append({"level": "medium",
                                     "text": f"TLS证书 {days} 天后过期（{cert['not_after']}）"})
        if tcp_ms is None and connect_ms is None:
            score -= 70
            findings.append({"level": "high", "text": "TCP/TLS 均无法建立连接，服务不可达"})
        else:
            ttfb = http.get("ttfb_ms")
            if ttfb is not None:
                if ttfb > 800:
                    score -= 25
                    findings.append({"level": "medium",
                                     "text": f"首字节响应 {ttfb:.0f}ms，服务器处理缓慢（非网络问题）"})
                elif ttfb > 400:
                    score -= 10
                    findings.append({"level": "low",
                                     "text": f"首字节响应 {ttfb:.0f}ms，偏慢"})
            if connect_ms is not None and connect_ms > 300:
                score -= 10
                findings.append({"level": "low",
                                 "text": f"TCP连接建立 {connect_ms:.0f}ms，链路距离远或路径拥塞"})
        if drift > 0:
            findings.append({"level": "low",
                             "text": f"检测到 {drift} 个新IP（CDN调度/故障切换活跃）"})
        if not ips:
            score -= 50
            findings.append({"level": "high", "text": "DNS解析失败"})

        parts = []
        if cdn:
            parts.append(f"CDN架构（{cdn}）")
        elif len(ips) > 1:
            parts.append(f"多节点集群（{len(ips)}个A记录）")
        else:
            parts.append("单源站")
        server_hdr = (http.get("headers") or {}).get("server")
        if server_hdr:
            parts.append(f"服务端 {server_hdr}")
        ttfb = http.get("ttfb_ms")
        if ttfb is not None:
            parts.append(f"TTFB {ttfb:.0f}ms")
        structure = " · ".join(parts)

        profile = {
            "ts": now,
            "name": name,
            "host": host,
            "ips": ips,
            "ip_drift": drift,
            "tcp_ms": tcp_ms,
            "connect_ms": connect_ms,
            "tls_ms": tls_ms,
            "ttfb_ms": http.get("ttfb_ms"),
            "status_code": http.get("status"),
            "server_header": server_hdr,
            "cdn": cdn,
            "cert": cert,
            "cert_days_left": days,
            "structure": structure,
            "findings": findings,
            "health": max(0, min(100, score)),
            "proxy_note": "经代理隧道观测" if proxy_active else None,
        }
        self.profiles[name] = profile
        if self.storage:
            try:
                self.storage.save_server_profile(name, host, profile, now)
            except Exception:
                pass

    @staticmethod
    def _tcp_time(host, port, attempts=3):
        vals = []
        for _ in range(attempts):
            t0 = time.perf_counter()
            try:
                with socket.create_connection((host, port), timeout=3):
                    vals.append((time.perf_counter() - t0) * 1000)
            except OSError:
                return None
        vals.sort()
        return round(vals[len(vals) // 2], 1)

    @staticmethod
    def _tls_connect(host, port=443, timeout=5):
        t0 = time.perf_counter()
        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            t1 = time.perf_counter()
            ctx = ssl.create_default_context()
            tls = ctx.wrap_socket(sock, server_hostname=host)
            t2 = time.perf_counter()
            return round((t1 - t0) * 1000, 1), round((t2 - t1) * 1000, 1), tls
        except (OSError, ssl.SSLError):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
            return None, None, None

    @staticmethod
    def _get_cert(tls_sock):
        try:
            der = tls_sock.getpeercert(binary_form=True)
            if der:
                return _decode_cert_der(der)
        except Exception:
            pass
        return None

    @staticmethod
    def _http_over(tls_sock, host):
        try:
            conn = http.client.HTTPSConnection(host, timeout=5)
            conn.sock = tls_sock
            t0 = time.perf_counter()
            conn.request("GET", "/", headers={"User-Agent": "net-analyzer", "Connection": "close"})
            resp = conn.getresponse()
            ttfb = round((time.perf_counter() - t0) * 1000, 1)
            headers = {k.lower(): v for k, v in resp.getheaders()}
            body = resp.read(64)
            conn.close()
            return {"status": resp.status, "headers": headers, "body_head": body[:32].hex()}, ttfb
        except Exception:
            return {}, None
