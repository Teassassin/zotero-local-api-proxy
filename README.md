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

## 4. 附件/PDF 下载支持

该代理已针对 Zotero 附件接口做了兼容处理，可直接返回 PDF 字节流，而不只是返回本地 `file:///` URI。

- 支持直接访问：`/api/users/0/items/<attachmentKey>/file`
- 支持直接访问：`/api/users/0/items/<attachmentKey>/file/view`
- 若上游对上述接口返回 `404`，代理会自动回退到：`/api/users/0/items/<attachmentKey>/file/view/url`
- 若上游返回 `file:///...`，代理会在本机读取对应文件并流式返回给客户端（支持 `Range` 断点请求）

示例：

```bash
curl -I "http://192.168.1.10:23120/api/users/0/items/<attachmentKey>/file"
curl -H "Range: bytes=0-1023" "http://192.168.1.10:23120/api/users/0/items/<attachmentKey>/file" -o part.bin
```

### 路径说明（Windows + WSL）

- Zotero 常返回 `file:///C:/...` 这类路径。
- 代理会优先按原路径读取；若运行在非 Windows 环境，会额外尝试映射到 `/mnt/c/...` 这类 WSL 路径。
- 若附件在 Zotero 已显示存在但仍返回 404，请先确认该主机上对应附件文件是否真实落盘。

## 安全建议

- 该代理面向受信任的局域网，不建议直接暴露到公网。
- 如有需要，请通过防火墙限制来源 IP。
- 仅开放你需要的端口（默认 `23120`）。