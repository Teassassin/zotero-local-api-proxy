# zotero-local-api-proxy

一个最小化 Python 反向代理，用于把仅本机可访问的 Zotero Local API 转发到局域网可访问地址。

## 1. 启动 Zotero 本地 API

确认 Zotero 已运行，并且本机可访问：

```bash
curl http://localhost:23119/api/
```

## 2. 启动代理

```bash
python3 proxy.py
```

默认参数：
- 监听：`0.0.0.0:23120`
- 上游：`http://localhost:23119`
- 仅转发路径前缀：`/api`

你也可以自定义：

```bash
python3 proxy.py --host 0.0.0.0 --port 23120 --upstream http://localhost:23119 --path-prefix /api
```

## 3. 局域网访问

假设代理机器 IP 为 `192.168.1.10`，其他主机可访问：

```bash
curl "http://192.168.1.10:23120/api/"
```

## 安全建议

- 该代理面向受信任的局域网，不建议直接暴露到公网。
- 如有需要，请通过防火墙限制来源 IP。
- 仅开放你需要的端口（默认 `23120`）。