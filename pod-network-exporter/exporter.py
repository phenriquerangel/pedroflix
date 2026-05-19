#!/usr/bin/env python3
"""
pod-network-exporter
Lê /proc/<pid>/net/dev de cada pod e exporta métricas de rede por pod/namespace.
Funciona em ambientes Docker-in-Docker (minikube docker driver) onde o cAdvisor
não consegue resolver container labels via cgroups.

Estratégia:
1. Escaneia /proc para todos os PIDs
2. Extrai pod UID do cgroup path (/proc/<pid>/cgroup)
3. Deduplica por inode do network namespace (um PID por pod)
4. Lê /proc/<pid>/net/dev para stats de rede (eth0 do pod)
5. Consulta API k8s para mapear UID → nome/namespace do pod
6. Expõe métricas Prometheus em /metrics
"""
import os, re, time, json, ssl, sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import urlopen, Request

# Pod UID aparece no cgroup path como pod<uid> onde uid pode ter _ ou -
POD_UID_RE = re.compile(
    r'pod([0-9a-f]{8}[_-][0-9a-f]{4}[_-][0-9a-f]{4}[_-][0-9a-f]{4}[_-][0-9a-f]{12})'
)

def norm_uid(uid):
    return uid.replace('_', '-')

def get_pod_uid(pid):
    try:
        with open(f'/proc/{pid}/cgroup') as f:
            for line in f:
                m = POD_UID_RE.search(line)
                if m:
                    return norm_uid(m.group(1))
    except OSError:
        pass
    return None

def ns_inode(pid):
    try:
        return os.stat(f'/proc/{pid}/ns/net').st_ino
    except OSError:
        return None

def read_net_dev(pid):
    """
    Lê /proc/<pid>/net/dev e retorna bytes rx/tx do eth0 do pod.
    Formato: 'eth0:  <rx_bytes> ... <tx_bytes> ...'
    Índices: rx_bytes=1, tx_bytes=9 (após split por espaço)
    """
    try:
        with open(f'/proc/{pid}/net/dev') as f:
            for line in f:
                parts = line.split()
                if parts and parts[0] == 'eth0:':
                    return {'rx': int(parts[1]), 'tx': int(parts[9])}
    except OSError:
        pass
    return None

def k8s_pods(node_name):
    """Consulta a API k8s para listar pods no node atual."""
    try:
        token = open('/var/run/secrets/kubernetes.io/serviceaccount/token').read().strip()
        ca = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
        ctx = ssl.create_default_context(cafile=ca)
        url = f'https://kubernetes.default.svc/api/v1/pods?fieldSelector=spec.nodeName={node_name}'
        req = Request(url, headers={'Authorization': f'Bearer {token}'})
        with urlopen(req, context=ctx, timeout=5) as r:
            return {p['metadata']['uid']: p for p in json.load(r)['items']}
    except Exception as e:
        print(f'[warn] k8s API: {e}', file=sys.stderr)
        return {}

def collect(node_name):
    pod_info = k8s_pods(node_name)
    if not pod_info:
        return []

    # Mapeia pod_uid -> pid (um por network namespace)
    uid_to_pid = {}
    seen_ns = set()

    try:
        pids = [p for p in os.listdir('/proc') if p.isdigit()]
    except OSError:
        return []

    for pid in pids:
        uid = get_pod_uid(pid)
        if not uid:
            continue
        ns = ns_inode(pid)
        if ns is None or ns in seen_ns:
            continue
        seen_ns.add(ns)
        uid_to_pid[uid] = pid

    metrics = []
    for uid, pid in uid_to_pid.items():
        pod = pod_info.get(uid)
        if not pod:
            continue

        stats = read_net_dev(pid)
        if not stats:
            continue

        pod_name = pod['metadata']['name']
        namespace = pod['metadata']['namespace']
        labels = f'pod="{pod_name}",namespace="{namespace}",node="{node_name}"'
        metrics.append(f'pod_network_receive_bytes_total{{{labels}}} {stats["rx"]}')
        metrics.append(f'pod_network_transmit_bytes_total{{{labels}}} {stats["tx"]}')

    return metrics


HELP = """\
# HELP pod_network_receive_bytes_total Bytes recebidos no eth0 do pod (leitura de /proc)
# TYPE pod_network_receive_bytes_total counter
# HELP pod_network_transmit_bytes_total Bytes enviados no eth0 do pod (leitura de /proc)
# TYPE pod_network_transmit_bytes_total counter
"""

class Handler(BaseHTTPRequestHandler):
    node_name = ''

    def do_GET(self):
        if self.path == '/metrics':
            lines = collect(self.node_name)
            body = HELP + '\n'.join(lines) + '\n'
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
            self.end_headers()
            self.wfile.write(body.encode())
        elif self.path == '/healthz':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'ok')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass  # silencia access logs

if __name__ == '__main__':
    Handler.node_name = os.environ.get('NODE_NAME', 'unknown')
    port = int(os.environ.get('PORT', '9099'))
    print(f'pod-network-exporter iniciando em :{port} (node={Handler.node_name})')
    HTTPServer(('', port), Handler).serve_forever()
