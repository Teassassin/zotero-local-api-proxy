# AGENTS

## Project Goal
- Provide a minimal Python proxy so devices on the same LAN can access Zotero's local API.
- Forward incoming HTTP requests to the local Zotero endpoint (`http://localhost:23119`).

## Scope
- Keep implementation simple and dependency-free (Python standard library only).
- Expose only the `/api` path by default.
- Preserve method, headers, query parameters, and request body.

## Run/Dev Notes
- Main entry file: `proxy.py`
- Default bind: `0.0.0.0:23120`
- Default upstream: `http://localhost:23119`

## Safety Notes
- This proxy is intended for trusted LAN usage only.
- Prefer binding to specific LAN interface when possible.
- Add firewall rules to restrict source IP ranges if needed.