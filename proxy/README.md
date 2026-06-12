# fxcjg proxy

A Spring Boot (JDK8) reverse proxy for `/api/**` endpoints. It forwards all `/api` requests to the backend service under `server/`.

## Quick start

1. Edit the backend target URL in `src/main/resources/application.properties`.
2. Run the proxy:

```bash
SERVICE_AUTH_TOKEN=your-token ./mvnw spring-boot:run
```

## Configuration

`src/main/resources/application.properties`:

- `server.port`: port for this proxy (default `8081`)
- `proxy.targetBaseUrl`: backend base URL (example `http://localhost:5001`)
- `proxy.serviceToken`: service token forwarded to backend (header value)
- `proxy.serviceHeader`: header name for service token (default `X-Service-Token`)
- `proxy.connectTimeoutMs`: upstream connect timeout (ms)
- `proxy.socketTimeoutMs`: upstream socket timeout (ms)
- `proxy.cors.*`: CORS settings for `/api/**`

## Notes

- The proxy preserves method, headers, query string, and body.
- If the backend returns a non-2xx status, the proxy forwards the status and body as-is.
- Multipart parsing is disabled so request bodies pass through untouched. Do not add multipart handling in the proxy unless you reassemble the payload.
- Responses are streamed to the client without buffering, so SSE or mixed streaming payloads pass through.
- Large upload/download limits depend on the underlying container (Tomcat/Jetty/Undertow). Configure the container in your runtime environment if needed.
