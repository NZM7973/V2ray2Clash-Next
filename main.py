import os
import sys
import time
import json
import yaml
import base64
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import webbrowser

# Configuration
TEMPLATE_FILE = "template.yaml" # Keeping original name preference if it exists, or internal
OUTPUT_DIR = "订阅文件"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "config.yaml")

# Global storage for the server
SERVER_CONFIG_CONTENT = ""
SERVER_USER_INFO = ""

def decode_base64(s):
    # Remove whitespace/newlines from the base64 string
    s = s.replace('\r', '').replace('\n', '').strip()
    
    # Add padding if needed
    missing_padding = len(s) % 4
    if missing_padding:
        s += '=' * (4 - missing_padding)
        
    # Try standard decode
    try:
        return base64.b64decode(s).decode('utf-8')
    except:
        pass
        
    # Try urlsafe decode
    try:
        return base64.urlsafe_b64decode(s).decode('utf-8')
    except:
        return s

def parse_vmess(vmess_url):
    # vmess://base64_json
    try:
        b64 = vmess_url.replace("vmess://", "")
        data = json.loads(decode_base64(b64))
        
        server = data.get("add")
        name = data.get("ps", "vmess")

        proxy = {
            "name": name,
            "type": "vmess",
            "server": server,
            "port": int(data.get("port")),
            "uuid": data.get("id"),
            "alterId": int(data.get("aid", 0)),
            "cipher": "auto",
            "network": data.get("net", "tcp"),
            "tls": True if data.get("tls") == "tls" else False,
        }
        
        if proxy["network"] == "ws":
            proxy["ws-opts"] = {
                "path": data.get("path", "/"),
                "headers": {
                    "Host": data.get("host", "")
                }
            }
        return proxy
    except Exception as e:
        print(f"Error parsing vmess: {e}")
        return None

def parse_vless(vless_url):
    try:
        parsed = urllib.parse.urlparse(vless_url)
        params = urllib.parse.parse_qs(parsed.query)
        
        server = parsed.hostname
        if server.startswith('[') and server.endswith(']'):
            server = server[1:-1]
            
        name = urllib.parse.unquote(parsed.fragment) if parsed.fragment else "vless"

        proxy = {
            "name": name,
            "type": "vless",
            "server": parsed.hostname,
            "port": parsed.port,
            "uuid": parsed.username,
            "network": params.get("type", ["tcp"])[0],
            "tls": True if params.get("security", [""])[0] in ["tls", "reality"] else False,
            "udp": True,
        }
        
        flow = params.get("flow", [""])[0]
        if flow:
            proxy["flow"] = flow
        
        if params.get("security", [""])[0] == "reality":
            proxy["servername"] = params.get("sni", [""])[0]
            proxy["client-fingerprint"] = params.get("fp", ["chrome"])[0]
            proxy["reality-opts"] = {
                "public-key": params.get("pbk", [""])[0],
                "short-id": params.get("sid", [""])[0],
            }
        elif params.get("security", [""])[0] == "tls":
            proxy["servername"] = params.get("sni", [""])[0]
            # Some clients need client-fingerprint even for normal TLS
            if params.get("fp"):
                proxy["client-fingerprint"] = params.get("fp", ["chrome"])[0]
            
        return proxy
    except Exception as e:
        print(f"Error parsing vless: {e}")
        return None

def parse_hysteria2(hy2_url):
    try:
        parsed = urllib.parse.urlparse(hy2_url)
        params = urllib.parse.parse_qs(parsed.query)
        
        server = parsed.hostname
        if server.startswith('[') and server.endswith(']'):
            server = server[1:-1]
        
        name = urllib.parse.unquote(parsed.fragment) if parsed.fragment else "hysteria2"
            
        proxy = {
            "name": name,
            "type": "hysteria2",
            "server": parsed.hostname,
            "port": parsed.port,
            "password": parsed.username,
            "sni": params.get("sni", [""])[0],
            "skip-cert-verify": True if params.get("insecure", ["0"])[0] == "1" else False,
        }
        return proxy
    except Exception as e:
        print(f"Error parsing hysteria2: {e}")
        return None

def parse_tuic(tuic_url):
    try:
        parsed = urllib.parse.urlparse(tuic_url)
        params = urllib.parse.parse_qs(parsed.query)
        
        server = parsed.hostname
        if server.startswith('[') and server.endswith(']'):
            server = server[1:-1]

        name = urllib.parse.unquote(parsed.fragment) if parsed.fragment else "tuic"
            
        proxy = {
            "name": name,
            "type": "tuic",
            "server": parsed.hostname,
            "port": parsed.port,
            "uuid": parsed.username,
            "password": parsed.password,
            "sni": params.get("sni", [""])[0],
            "congestion-controller": params.get("congestion_control", ["bbr"])[0],
            "udp-relay-mode": params.get("udp_relay_mode", ["native"])[0],
            "skip-cert-verify": True if params.get("allow_insecure", ["0"])[0] == "1" else False,
        }
        return proxy
    except Exception as e:
        print(f"Error parsing tuic: {e}")
        return None

def get_template():
    # Helper to create a basic template if file doesn't exist
    return {
        "port": 7890,
        "socks-port": 7891,
        "allow-lan": True,
        "mode": "Rule",
        "log-level": "info",
        "external-controller": "0.0.0.0:9090",
        "proxies": [],
        "proxy-groups": [
            {
                "name": "🔰国外流量",
                "type": "select",
                "proxies": []
            },
            {
                "name": "🚀直接连接",
                "type": "select",
                "proxies": ["DIRECT"]
            }
        ],
        "rules": [
            "DOMAIN-SUFFIX,cn,🚀直接连接",
            "GEOIP,CN,🚀直接连接",
            "MATCH,🔰国外流量"
        ]
    }

def convert_subscriptions(url):
    content = ""
    user_info = None
    
    # Check if it's a remote subscription or direct link
    if url.lower().startswith("http://") or url.lower().startswith("https://"):
        print(f"正在获取订阅: {url}")
        headers = {"User-Agent": "Mozilla/5.0"}
        
        # Helper to fetch URL with retry
        def fetch_url(target_url):
            req = urllib.request.Request(target_url, headers=headers)
            with urllib.request.urlopen(req) as response:
                return response.read().decode('utf-8'), response.getheader('Subscription-Userinfo')

        try:
            content, user_info = fetch_url(url)
            if user_info:
                print(f"获取到流量信息: {user_info}")
                
        except urllib.error.HTTPError as e:
            # Handle HTTP 400 Bad Request (likely HTTP vs HTTPS mismatch on port 2096/8443 etc)
            if e.code == 400 and url.lower().startswith("http://"):
                 print(f"[提示] HTTP请求返回 400 错误，尝试切换为 HTTPS 重试...")
                 new_url = url.replace("http://", "https://", 1)
                 try:
                     content, user_info = fetch_url(new_url)
                     if user_info:
                        print(f"获取到流量信息: {user_info}")
                 except Exception as e2:
                     print(f"重试失败: {e2}")
                     return None, None
            else:
                print(f"获取订阅失败: {e}")
                return None, None
        except Exception as e:
            print(f"获取订阅失败: {e}")
            return None, None
    else:
        # Treat as raw content (single or multiple links)
        print("识别为直接链接，开始解析...")
        content = url

    # Decode if base64 (only if it looks like base64 and not just a vless:// link)
    lines = []
    if any(proto in content for proto in ["vless://", "vmess://", "hysteria2://", "tuic://", "ss://", "trojan://"]):
         # Assumed to be plain text list of links or single link
         lines = content.splitlines()
         if len(lines) == 1 and "," in lines[0] and "://" not in lines[0].split(",")[0]: 
             pass
    else:
        # Try base64 decode
        try:
            decoded = decode_base64(content)
            if decoded:
                lines = decoded.splitlines()
            else:
                lines = content.splitlines()
        except:
            lines = content.splitlines()
        
    proxies = []
    
    for line in lines:
        line = line.strip()
        if not line: continue
        
        p = None
        if line.startswith("vmess://"):
            p = parse_vmess(line)
        elif line.startswith("vless://"):
            p = parse_vless(line)
        elif line.startswith("hysteria2://"):
            p = parse_hysteria2(line)
        elif line.startswith("tuic://"):
            p = parse_tuic(line)
            
        if p:
            proxies.append(p)
            
    print(f"解析到 {len(proxies)} 个节点")
    
    # Generate YAML
    # Try to load existing template to preserve rules
    config = {}
    if os.path.exists(TEMPLATE_FILE):
        try:
            with open(TEMPLATE_FILE, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
        except:
            pass
            
    if not config:
        config = get_template()

    # Update proxies
    config["proxies"] = proxies
    
    proxy_names = [p["name"] for p in proxies]
    
    # Update groups
    for group in config.get("proxy-groups", []):
         if "proxies" not in group or group["proxies"] is None:
             group["proxies"] = []
             
         # Simplify: Just add all nodes to "🔰国外流量" or related
         if group["name"] in ["🔰国外流量", "Proxy", "节点选择", "Select"]:
              group["proxies"].extend(proxy_names)
    
    # Generate YAML string
    yaml_str = yaml.dump(config, allow_unicode=True, sort_keys=False)
    
    if user_info is None and not (url.lower().startswith("http://") or url.lower().startswith("https://")):
        print("\n========================================")
        print("[注意!] 您输入的是直接节点链接 (vless://...)")
        print("因此无法从服务器获取 [总流量/已用流量] 信息。")
        print("Clash 将无法显示流量统计。")
        print("若需显示流量，请使用 XUI 面板生成的 [订阅链接] (http://...)")
        print("========================================")

    return yaml_str, user_info



class SubscriptionHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global SERVER_CONFIG_CONTENT, SERVER_USER_INFO
        
        self.send_response(200)
        self.send_header('Content-type', 'text/yaml; charset=utf-8')
        self.send_header('Content-Disposition', 'attachment; filename="config.yaml"')
        
        # Add traffic info header if available
        if SERVER_USER_INFO:
            self.send_header('Subscription-Userinfo', SERVER_USER_INFO)
            
        self.end_headers()
        self.wfile.write(SERVER_CONFIG_CONTENT.encode('utf-8'))
    
    def log_message(self, format, *args):
        pass # Suppress logging

def start_server(port=7890):
    server_address = ('', port)
    httpd = HTTPServer(server_address, SubscriptionHandler)
    print(f"\n[INFO] 本地订阅服务器已启动!")
    print(f"[INFO] Clash 订阅地址: http://127.0.0.1:{port}")
    print(f"[INFO] 请在 Clash 中添加此 URL 更新订阅")
    print(f"[INFO] 按 Ctrl+C 停止服务...")
    httpd.serve_forever()

def main():
    print("========================================")
    print("       V2ray2Clash Next 转换工具")
    print("========================================")
    
    url = input("请输入 V2Ray/XUI 订阅链接: ").strip()
    if not url:
        print("未输入链接，退出")
        return

    yaml_content, user_info = convert_subscriptions(url)
    
    if not yaml_content:
        print("转换失败")
        return

    global SERVER_CONFIG_CONTENT, SERVER_USER_INFO
    SERVER_CONFIG_CONTENT = yaml_content
    SERVER_USER_INFO = user_info

    print("\n请选择输出模式:")
    print("1. 生成本地订阅地址 (推荐: 支持在 Clash 中显示流量)")
    print("2. 仅保存 YAML 文件")
    print("3. 两者都要")
    
    choice = input("请输入选项 (1/2/3): ").strip()
    
    # Traffic Override Logic
    if SERVER_USER_INFO and "total=0" in SERVER_USER_INFO:
        print("\n[提示] 检测到服务器返回的总流量为 0 (无限或未设置)。")
        print("如果您的机场/面板实际有限额 (例如 500GB)，您可以手动设置以在 Clash 中正确显示。")
        limit_gb = input("请输入总流量限制 (GB) [回车保持默认/无限]: ").strip()
        
        if limit_gb and limit_gb.isdigit():
            try:
                total_bytes = int(limit_gb) * 1024 * 1024 * 1024
                # Replace total=0 or insert total
                parts = SERVER_USER_INFO.split(';')
                new_parts = []
                found_total = False
                for part in parts:
                    if part.strip().startswith('total='):
                        new_parts.append(f" total={total_bytes}")
                        found_total = True
                    else:
                        new_parts.append(part)
                
                if not found_total:
                    new_parts.append(f" total={total_bytes}")
                
                SERVER_USER_INFO = ";".join(new_parts)
                print(f"[成功] 已将总流量修正为: {limit_gb} GB")
            except:
                print("输入无效，保持默认")

    save_file = False
    run_server = False
    
    if choice == '1':
        run_server = True
    elif choice == '2':
        save_file = True
    elif choice == '3':
        save_file = True
        run_server = True
    else:
        print("无效选项，默认仅保存文件")
        save_file = True

    if save_file:
        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(yaml_content)
        print(f"\n[SUCCESS] 配置文件已保存至: {os.path.abspath(OUTPUT_FILE)}")
        
    if run_server:
        # Find a free port or use default
        port = 25500
        start_server(port)

if __name__ == "__main__":
    main()
