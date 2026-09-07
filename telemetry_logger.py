import os
import sys
import time
import json
import socket
import subprocess
import uuid
import urllib.request
import urllib.error
import urllib.parse
import platform
from threading import Lock

try:
    import psutil
except Exception:
    psutil = None


class TelemetryLogger:
    def __init__(
        self,
        worker_url,
        admin_token,
        timeout=12,
        app_name="MyApp",
        app_version="1.0.0",
        enabled=True,
    ):
        self.worker_url = worker_url.rstrip("/")
        self.admin_token = admin_token
        self.timeout = timeout
        self.app_name = app_name
        self.app_version = app_version
        self.enabled = enabled
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_get(d, key, default=""):
        if not isinstance(d, dict):
            return default
        val = d.get(key, default)
        return val if val is not None else default

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    @staticmethod
    def _get_machine_uuid():
        try:
            mac = uuid.getnode()
            if mac and mac != 0:
                return str(mac)
        except Exception:
            pass
        return "unknown"

    @staticmethod
    def _get_mac_address():
        try:
            mac = uuid.getnode()
            if mac and mac != 0:
                return ":".join(f"{(mac >> i) & 0xff:02x}" for i in range(40, -1, -8))
        except Exception:
            pass
        return "unknown"

    @staticmethod
    def _get_user_sid():
        try:
            if sys.platform.startswith("win"):
                out = subprocess.check_output(
                    ["whoami", "/user"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                for line in out.splitlines():
                    if line.startswith("S-1-5"):
                        return line.strip().split()[-1]
        except Exception:
            pass
        return "N/A"

    @staticmethod
    def _get_primary_email():
        try:
            if sys.platform.startswith("win"):
                import winreg

                for path in [
                    r"SOFTWARE\Microsoft\IdentityCRL\UserExtendedProperties",
                    r"Software\Microsoft\IdentityCRL\UserExtendedProperties",
                ]:
                    try:
                        root = (
                            winreg.HKEY_LOCAL_MACHINE
                            if path.startswith("SOFTWARE")
                            else winreg.HKEY_CURRENT_USER
                        )
                        key = winreg.OpenKey(root, path)
                        email, _ = winreg.QueryValueEx(key, "UserEmail")
                        winreg.CloseKey(key)
                        if email:
                            return email
                    except Exception:
                        pass
                try:
                    out = subprocess.check_output(
                        ["whoami", "/upn"],
                        text=True,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                    email = out.strip()
                    if "@" in email:
                        return email
                except Exception:
                    pass
        except Exception:
            pass
        return os.environ.get("USERNAME", "unknown")

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------
    @staticmethod
    def _get_local_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "unknown"

    @staticmethod
    def _get_public_ip():
        try:
            req = urllib.request.Request(
                "https://api.ipify.org",
                headers={"User-Agent": "TelemetryLogger"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.read().decode().strip()
        except Exception:
            return "unknown"

    @staticmethod
    def _get_ip_info(public_ip):
        info = {}
        try:
            if public_ip in ("unknown", ""):
                return info
            url = (
                "http://ip-api.com/json/"
                + public_ip
                + "?fields=status,message,country,countryCode,region,regionName,city,zip,lat,lon,timezone,isp,org,as,reverse,mobile,proxy,hosting"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "TelemetryLogger"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("status") == "success":
                    info = {
                        "country": data.get("country", ""),
                        "country_code": data.get("countryCode", ""),
                        "region": data.get("regionName", ""),
                        "city": data.get("city", ""),
                        "zip": data.get("zip", ""),
                        "coordinates": f"{data.get('lat', '')}, {data.get('lon', '')}",
                        "timezone": data.get("timezone", ""),
                        "isp": data.get("isp", ""),
                        "organization": data.get("org", ""),
                        "asn": data.get("as", ""),
                        "reverse_dns": data.get("reverse", ""),
                        "mobile": data.get("mobile", False),
                        "proxy": data.get("proxy", False),
                        "hosting": data.get("hosting", False),
                    }
        except Exception:
            pass
        return info

    @staticmethod
    def _doh_resolve(domain, record_type="A"):
        try:
            url = (
                "https://cloudflare-dns.com/dns-query?name="
                + domain
                + "&type="
                + record_type
            )
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/dns-json", "User-Agent": "TelemetryLogger"},
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                answers = data.get("Answer", [])
                return [
                    a.get("data") for a in answers if a.get("type") in (1, 28)
                ]
        except Exception:
            return []

    @staticmethod
    def _reverse_dns_doh(ip_address):
        try:
            rev = ".".join(reversed(ip_address.split("."))) + ".in-addr.arpa"
            results = TelemetryLogger._doh_resolve(rev, "PTR")
            return results[0] if results else ""
        except Exception:
            return ""

    def _get_network_interfaces(self):
        interfaces = []
        try:
            if psutil:
                for name, addrs in psutil.net_if_addrs().items():
                    for addr in addrs:
                        if addr.family == socket.AF_INET:
                            interfaces.append(
                                {
                                    "name": name,
                                    "ip": addr.address,
                                    "netmask": addr.netmask or "",
                                }
                            )
                            break
        except Exception:
            pass
        return interfaces

    def _get_all_interface_ips(self):
        interfaces = []
        try:
            if psutil:
                for name, addrs in psutil.net_if_addrs().items():
                    ips = []
                    for addr in addrs:
                        if addr.family in (socket.AF_INET, socket.AF_INET6):
                            ips.append(addr.address)
                    if ips:
                        interfaces.append({"name": name, "ips": ips})
        except Exception:
            pass
        return interfaces

    def _get_all_mac_addresses(self):
        macs = []
        try:
            if psutil:
                for name, addrs in psutil.net_if_addrs().items():
                    for addr in addrs:
                        if (
                            hasattr(addr, "address")
                            and addr.address
                            and ":" in addr.address
                            and len(addr.address) == 17
                        ):
                            macs.append({"interface": name, "mac": addr.address})
                            break
        except Exception:
            pass
        return macs

    def _get_network_interface_type(self):
        interface_types = []
        try:
            if psutil:
                for name, stats in psutil.net_if_stats().items():
                    interface_types.append(
                        {
                            "name": name,
                            "type": "Wi-Fi"
                            if "wi-fi" in name.lower()
                            or "wlan" in name.lower()
                            or "wireless" in name.lower()
                            else "Ethernet",
                            "is_up": stats.isup,
                            "speed_mbps": stats.speed or 0,
                        }
                    )
        except Exception:
            pass
        return interface_types

    @staticmethod
    def _get_wifi_bssids():
        bssids = []
        try:
            if sys.platform.startswith("win"):
                out = subprocess.check_output(
                    ["netsh", "wlan", "show", "interfaces"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                for line in out.splitlines():
                    if "BSSID" in line:
                        parts = line.split(":")
                        if len(parts) >= 2:
                            bssids.append(parts[1].strip())
            else:
                out = subprocess.check_output(
                    ["iw", "dev"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                for line in out.splitlines():
                    if "ssid" in line or "bssid" in line:
                        bssids.append(line.strip())
        except Exception:
            pass
        return bssids

    @staticmethod
    def _detect_vpn_proxy():
        flags = {"vpn_detected": False, "proxy_detected": False, "tun_tap_interfaces": []}
        try:
            if psutil:
                for name, stats in psutil.net_if_stats().items():
                    low = name.lower()
                    if "tun" in low or "tap" in low or "vpn" in low:
                        flags["vpn_detected"] = True
                        flags["tun_tap_interfaces"].append(name)
            env_proxy = any(
                k in os.environ
                for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]
            )
            if env_proxy:
                flags["proxy_detected"] = True
        except Exception:
            pass
        return flags

    @staticmethod
    def _get_router_gateway_mac():
        try:
            if sys.platform.startswith("win"):
                out = subprocess.check_output(
                    ["arp", "-a"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                for line in out.splitlines():
                    if "gateway" in line.lower() or ".1 " in line:
                        parts = line.split()
                        for part in parts:
                            if "-" in part or ":" in part:
                                return part.strip()
        except Exception:
            pass
        return "N/A"

    @staticmethod
    def _get_arp_table():
        arp_entries = []
        try:
            if sys.platform.startswith("win"):
                out = subprocess.check_output(
                    ["arp", "-a"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                for line in out.splitlines():
                    if "." in line and ("-" in line or ":" in line):
                        parts = line.split()
                        if len(parts) >= 3:
                            arp_entries.append(
                                {
                                    "ip": parts[0],
                                    "mac": parts[1],
                                    "type": parts[2] if len(parts) > 2 else "",
                                }
                            )
        except Exception:
            pass
        return arp_entries[:20]

    def _get_listening_ports(self):
        ports = []
        try:
            if psutil:
                for conn in psutil.net_connections(kind="inet"):
                    if conn.status == psutil.CONN_LISTEN:
                        pid = conn.pid
                        process_name = ""
                        try:
                            process = psutil.Process(pid)
                            process_name = process.name()
                        except Exception:
                            pass
                        ports.append(
                            {
                                "port": conn.laddr.port,
                                "address": conn.laddr.ip,
                                "pid": pid,
                                "process": process_name,
                            }
                        )
        except Exception:
            pass
        return ports

    # ------------------------------------------------------------------
    # System
    # ------------------------------------------------------------------
    @staticmethod
    def _get_system_uptime():
        try:
            if psutil:
                boot_ts = psutil.boot_time()
                uptime_seconds = time.time() - boot_ts
                return {
                    "boot_timestamp": time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(boot_ts),
                    ),
                    "uptime_seconds": round(uptime_seconds, 2),
                }
        except Exception:
            pass
        return {}

    @staticmethod
    def _get_ram_info():
        try:
            if psutil:
                mem = psutil.virtual_memory()
                return {
                    "total_bytes": mem.total,
                    "available_bytes": mem.available,
                    "used_bytes": mem.used,
                    "percent_used": mem.percent,
                }
        except Exception:
            pass
        return {}

    @staticmethod
    def _get_gpu_info():
        gpus = []
        try:
            if sys.platform.startswith("win"):
                import winreg

                key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\OpenGLDrivers"
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
                for i in range(winreg.QueryInfoKey(key)[0]):
                    gpus.append(winreg.EnumKey(key, i))
                winreg.CloseKey(key)
            elif sys.platform.startswith("linux"):
                out = subprocess.check_output(
                    ["lspci"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                for line in out.splitlines():
                    if "VGA" in line or "Display" in line:
                        gpus.append(line.strip())
        except Exception:
            pass
        return gpus

    @staticmethod
    def _get_registered_owner():
        try:
            if sys.platform.startswith("win"):
                import winreg

                key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
                owner, _ = winreg.QueryValueEx(key, "RegisteredOwner")
                org, _ = winreg.QueryValueEx(key, "RegisteredOrganization")
                winreg.CloseKey(key)
                return {"owner": owner or "N/A", "organization": org or "N/A"}
        except Exception:
            pass
        return {"owner": "N/A", "organization": "N/A"}

    @staticmethod
    def _get_system_product_key():
        try:
            if sys.platform.startswith("win"):
                import winreg

                key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
                product_id, _ = winreg.QueryValueEx(key, "ProductId")
                winreg.CloseKey(key)
                return product_id
        except Exception:
            pass
        return "N/A"

    @staticmethod
    def _get_windows_activation():
        try:
            if sys.platform.startswith("win"):
                import winreg

                key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
                activation, _ = winreg.QueryValueEx(key, "ProductStatus")
                winreg.CloseKey(key)
                return activation
        except Exception:
            pass
        return "N/A"

    @staticmethod
    def _get_domain_controller():
        try:
            if sys.platform.startswith("win"):
                domain = os.environ.get("USERDOMAIN", "")
                out = subprocess.check_output(
                    ["nltest", "/dsgetdc:" + domain],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                for line in out.splitlines():
                    if "DC Name:" in line or "Domain Controller:" in line:
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass
        return "N/A"

    @staticmethod
    def _get_system_language():
        try:
            if sys.platform.startswith("win"):
                import winreg

                key_path = r"SYSTEM\CurrentControlSet\Control\Keyboard Layouts"
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
                layouts = []
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        subkey = winreg.OpenKey(key, sub_name)
                        text, _ = winreg.QueryValueEx(subkey, "Layout Text")
                        winreg.CloseKey(subkey)
                        layouts.append(text)
                    except Exception:
                        pass
                winreg.CloseKey(key)
                return layouts[:3] if layouts else ["N/A"]
        except Exception:
            pass
        return ["N/A"]

    @staticmethod
    def _get_connected_devices():
        devices = []
        try:
            if sys.platform.startswith("win"):
                out = subprocess.check_output(
                    [
                        "powershell",
                        "-Command",
                        "Get-PnpDevice | Where-Object {$_.Status -eq 'OK'} | Select-Object -Property FriendlyName | ConvertTo-Json",
                    ],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
                data = json.loads(out)
                if isinstance(data, dict):
                    data = [data]
                for device in data[:20]:
                    devices.append(device.get("FriendlyName", "Unknown"))
        except Exception:
            pass
        return devices[:10]

    @staticmethod
    def _get_monitor_edid():
        monitors = []
        try:
            if sys.platform.startswith("win"):
                out = subprocess.check_output(
                    [
                        "powershell",
                        "-Command",
                        "Get-WmiObject -Namespace root\\wmi -Class WmiMonitorID | ForEach-Object { $_.ManufacturerName + ' | ' + $_.UserFriendlyName }",
                    ],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
                monitors = [line.strip() for line in out.splitlines() if line.strip()]
        except Exception:
            pass
        return monitors[:3] if monitors else ["N/A"]

    @staticmethod
    def _get_installed_software():
        software = []
        try:
            if sys.platform.startswith("win"):
                import winreg

                key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        subkey = winreg.OpenKey(key, sub_name)
                        display_name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                        winreg.CloseKey(subkey)
                        if display_name:
                            software.append(display_name)
                    except Exception:
                        pass
                winreg.CloseKey(key)
        except Exception:
            pass
        return software[:15]

    @staticmethod
    def _get_default_browser():
        try:
            if sys.platform.startswith("win"):
                import winreg

                key_path = r"SOFTWARE\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice"
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path)
                browser, _ = winreg.QueryValueEx(key, "ProgId")
                winreg.CloseKey(key)
                return browser
        except Exception:
            pass
        return "N/A"

    @staticmethod
    def _get_antivirus():
        av = []
        try:
            if sys.platform.startswith("win"):
                import winreg

                key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        subkey = winreg.OpenKey(key, sub_name)
                        display_name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                        winreg.CloseKey(subkey)
                        if display_name and any(
                            x in display_name.lower()
                            for x in [
                                "antivirus",
                                "security",
                                "defender",
                                "avg",
                                "avast",
                                "norton",
                                "mcafee",
                                "kaspersky",
                                "bitdefender",
                                "trend",
                                "symantec",
                                "malwarebytes",
                            ]
                        ):
                            av.append(display_name)
                    except Exception:
                        pass
                winreg.CloseKey(key)
        except Exception:
            pass
        return av[:5]

    @staticmethod
    def _get_disk_drives():
        drives = []
        try:
            if psutil:
                for part in psutil.disk_partitions(all=False):
                    try:
                        usage = psutil.disk_usage(part.mountpoint)
                        drives.append(
                            {
                                "device": part.device,
                                "mountpoint": part.mountpoint,
                                "fstype": part.fstype,
                                "total_gb": round(usage.total / (1024 ** 3), 2),
                                "free_gb": round(usage.free / (1024 ** 3), 2),
                            }
                        )
                    except Exception:
                        pass
        except Exception:
            pass
        return drives

    @staticmethod
    def _get_user_paths():
        try:
            return {
                "desktop": os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
                "documents": os.path.join(os.environ.get("USERPROFILE", ""), "Documents"),
                "downloads": os.path.join(os.environ.get("USERPROFILE", ""), "Downloads"),
                "appdata": os.environ.get("APPDATA", ""),
                "local_appdata": os.environ.get("LOCALAPPDATA", ""),
            }
        except Exception:
            return {}

    @staticmethod
    def _get_environment_info():
        env_vars = {}
        sensitive_keys = [
            "PASSWORD",
            "SECRET",
            "TOKEN",
            "KEY",
            "API",
            "CREDENTIAL",
            "PASS",
            "AUTH",
        ]
        try:
            for key, value in os.environ.items():
                if not any(s in key.upper() for s in sensitive_keys):
                    env_vars[key] = value
        except Exception:
            pass
        return env_vars

    # ------------------------------------------------------------------
    # Collector
    # ------------------------------------------------------------------
    def _collect_telemetry(self, username="unknown"):
        local_ip = self._get_local_ip()
        public_ip = self._get_public_ip()
        machine_uuid = self._get_machine_uuid()
        hostname = platform.node() or "unknown"

        return {
            "machine_uuid": machine_uuid,
            "public_ip": public_ip,
            "local_ip": local_ip,
            "hostname": hostname,
        }

    @staticmethod
    def _check_local_ports():
        common_ports = [
            21,
            22,
            23,
            25,
            53,
            80,
            110,
            135,
            139,
            143,
            443,
            445,
            993,
            995,
            3306,
            3389,
            5432,
            5900,
            6379,
            8080,
            8443,
        ]
        open_ports = []
        for port in common_ports:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.15)
                result = sock.connect_ex(("127.0.0.1", port))
                sock.close()
                if result == 0:
                    open_ports.append(port)
            except Exception:
                pass
        return open_ports

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def send_telemetry(self, username="unknown"):
        if not self.enabled or not self.worker_url:
            return None
        payload = self._collect_telemetry(username)
        machine_uuid = payload.get("machine_uuid", "unknown")
        public_ip = payload.get("public_ip", "unknown")
        local_ip = payload.get("local_ip", "unknown")
        hostname = payload.get("hostname", "unknown")

        print("\n[Telemetry] This app sends a one-time check to verify access.")
        print(f"[Telemetry] Machine ID : {machine_uuid}")
        print(f"[Telemetry] Public IP  : {public_ip}")
        print(f"[Telemetry] Local IP   : {local_ip}")
        print(f"[Telemetry] Desktop    : {hostname}")
        choice = input("[Telemetry] Do you want to share these details? [Y/n]: ").strip().lower()

        if choice in ("", "y", "yes"):
            data_payload = payload
        else:
            data_payload = {}
            print("[Telemetry] Only your machine ID will be sent for access checks.\n")

        try:
            params = urllib.parse.urlencode(
                {
                    "id": machine_uuid,
                    "data": json.dumps(data_payload),
                }
            )
            target_url = self.worker_url + "/?" + params
            req = urllib.request.Request(
                target_url,
                headers={"User-Agent": "TelemetryLogger"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                try:
                    return json.loads(resp.read().decode("utf-8"))
                except Exception:
                    return {"status": "ok"}
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"status": "error", "code": e.code}
        except Exception as e:
            print(f"[!] Telemetry upload failed: {e}")
            return None

    def admin_request(self, command, machine_uuid, reason=""):
        if not self.enabled or not self.worker_url:
            return None
        url = self.worker_url.rstrip("/") + "/admin"
        payload = {
            "command": command,
            "machine_uuid": machine_uuid,
            "reason": reason,
        }
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "X-Admin-Token": self.admin_token,
                    "User-Agent": "TelemetryLogger-Admin",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                try:
                    return json.loads(resp.read().decode("utf-8"))
                except Exception:
                    return {"status": "ok"}
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"status": "error", "code": e.code}
        except Exception:
            return None

    def check_banned(self, machine_uuid):
        resp = self.admin_request("list", "")
        if resp and isinstance(resp, dict):
            banned_list = resp.get("banned", [])
            return any(entry.get("uuid") == machine_uuid for entry in banned_list)
        return False

    def check_whitelisted(self, machine_uuid):
        resp = self.admin_request("list_whitelist", "")
        if resp and isinstance(resp, dict):
            whitelisted_list = resp.get("whitelisted", [])
            return any(entry.get("uuid") == machine_uuid for entry in whitelisted_list)
        return False
