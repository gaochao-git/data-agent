package com.fxcjg.proxy.web;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Enumeration;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.apache.http.impl.conn.PoolingHttpClientConnectionManager;
import org.apache.http.pool.PoolStats;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.util.StreamUtils;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.client.RequestCallback;
import org.springframework.web.client.ResponseExtractor;

import com.fxcjg.proxy.config.LLMProxyProperties;

/**
 * 统一代理 /api/** 请求到后端服务。
 * 核心目标：
 * - 不解析、不缓冲请求体，避免 multipart 被消费；
 * - 全量透传请求头（剔除 hop-by-hop 头）；
 * - 流式回传响应体，兼容 SSE 或混合流；
 * - 注入服务鉴权头，简化后端鉴权。
 */
@RestController
@RequestMapping("/agents")
public class LLMProxyController {
  private static final Logger logger = LoggerFactory.getLogger(LLMProxyController.class);
  private static final Set<String> SKIP_REQUEST_HEADERS = new HashSet<String>(Arrays.asList("host", "content-length", "transfer-encoding", "connection"));
  private static final Set<String> SKIP_RESPONSE_HEADERS = new HashSet<String>(Arrays.asList("transfer-encoding", "connection"));

  /** 代理专用 RestTemplate（不依赖宿主工程的默认实例）。 */
  private final RestTemplate proxyRestTemplate;
  private final PoolingHttpClientConnectionManager proxyConnectionManager;
  private final LLMProxyProperties properties;

  public LLMProxyController(@Qualifier("proxyRestTemplate") RestTemplate restTemplate, @Qualifier("proxyConnectionManager") PoolingHttpClientConnectionManager connectionManager, LLMProxyProperties properties) {
    this.proxyRestTemplate = restTemplate;
    this.proxyConnectionManager = connectionManager;
    this.properties = properties;
  }

  @RequestMapping(value = {"/api/**", "/healthz"})
  public void proxy(HttpServletRequest request, HttpServletResponse response) throws IOException {
    String requestId = UUID.randomUUID().toString().substring(0, 8);
    String targetUrl = buildTargetUrl(request);
    HttpMethod method = HttpMethod.resolve(request.getMethod());
    if (method == null) {
      response.setStatus(HttpStatus.METHOD_NOT_ALLOWED.value());
      return;
    }

    long contentLength = request.getContentLengthLong();
    HttpHeaders headers = extractRequestHeaders(request);
    applyForwardedHeaders(request, headers);
    applyServiceAuthHeader(headers);
    logger.info("[proxy] start id={} method={} uri={} target={} content_type={} content_length={}", requestId, method, request.getRequestURI(), targetUrl, request.getContentType(), contentLength);
    long startedAt = System.currentTimeMillis();

    try {
      RequestCallback requestCallback = clientRequest -> {
        clientRequest.getHeaders().putAll(headers);
        if (contentLength != 0) {
          if (contentLength > 0) {
            clientRequest.getHeaders().setContentLength(contentLength);
          }
          long copied = copyRequestBody(request, clientRequest.getBody());
          logger.info("[proxy] request_body_copied id={} bytes={} pool={}", requestId, copied, poolStatsText());
        }
      };
      ResponseExtractor<Void> responseExtractor = clientResponse -> {
        int status = clientResponse.getRawStatusCode();
        response.setStatus(status);
        copyResponseHeaders(clientResponse.getHeaders(), response);
        logger.info("[proxy] response_headers id={} status={} duration_ms={} pool={} resp_content_type={} resp_content_length={}", requestId, status, System.currentTimeMillis() - startedAt, poolStatsText(), clientResponse.getHeaders().getFirst(HttpHeaders.CONTENT_TYPE), clientResponse.getHeaders().getFirst(HttpHeaders.CONTENT_LENGTH));
        try {
          streamResponseBody(clientResponse.getBody(), response.getOutputStream());
          logger.info("[proxy] end id={} status={} duration_ms={} resp_content_type={}", requestId, status, System.currentTimeMillis() - startedAt, clientResponse.getHeaders().getFirst(HttpHeaders.CONTENT_TYPE));
        } catch (IOException ioex) {
          logger.warn("[proxy] stream_error id={} status={} duration_ms={} err={}", requestId, status, System.currentTimeMillis() - startedAt, ioex.toString());
          throw ioex;
        }
        return null;
      };
      logger.info("[proxy] execute_start id={} pool={}", requestId, poolStatsText());
      proxyRestTemplate.execute(targetUrl, method, requestCallback, responseExtractor);
      logger.info("[proxy] execute_end id={} duration_ms={} pool={}", requestId, System.currentTimeMillis() - startedAt, poolStatsText());
    } catch (RestClientException ex) {
      response.setStatus(HttpStatus.BAD_GATEWAY.value());
      response.getOutputStream().write(("Proxy error: " + ex.getMessage()).getBytes(StandardCharsets.UTF_8));
      logger.error("[proxy] upstream_error id={} duration_ms={} pool={} err={}", requestId, System.currentTimeMillis() - startedAt, poolStatsText(), ex.toString(), ex);
    }
  }

  /** 输出连接池状态，用于定位是否在等待可用后端连接。 */
  private String poolStatsText() {
    PoolStats stats = proxyConnectionManager.getTotalStats();
    return "leased=" + stats.getLeased() + ",pending=" + stats.getPending() + ",available=" + stats.getAvailable() + ",max=" + stats.getMax();
  }

  /** 根据配置拼接目标地址，保持原请求 path 与 query。 */
  private String buildTargetUrl(HttpServletRequest request) {
    String base = properties.getTargetBaseUrl();
    if (base == null || base.trim().isEmpty()) {
      return request.getRequestURL().toString();
    }
    String normalizedBase = base.endsWith("/") ? base.substring(0, base.length() - 1) : base;
    String path = request.getRequestURI();
    if (path.startsWith("/agents")) {
      path = path.substring("/agents".length());
      if (path.isEmpty()) {
        path = "/";
      }
    }
    String contextPath = request.getContextPath();
    if (contextPath != null && !contextPath.isEmpty() && path.startsWith(contextPath)) {
      path = path.substring(contextPath.length());
    }
    String query = request.getQueryString();
    StringBuilder target = new StringBuilder(normalizedBase).append(path);
    if (query != null && !query.isEmpty()) {
      target.append('?').append(query);
    }
    return target.toString();
  }

  /** 抽取并透传请求头，过滤 hop-by-hop 头。 */
  private HttpHeaders extractRequestHeaders(HttpServletRequest request) {
    HttpHeaders headers = new HttpHeaders();
    Enumeration<String> headerNames = request.getHeaderNames();
    while (headerNames.hasMoreElements()) {
      String headerName = headerNames.nextElement();
      if (SKIP_REQUEST_HEADERS.contains(headerName.toLowerCase())) {
        continue;
      }
      Enumeration<String> values = request.getHeaders(headerName);
      while (values.hasMoreElements()) {
        headers.add(headerName, values.nextElement());
      }
    }
    return headers;
  }

  /** 追加 X-Forwarded-* 头，保留原始链路信息。 */
  private void applyForwardedHeaders(HttpServletRequest request, HttpHeaders headers) {
    String existingForwardedFor = request.getHeader("X-Forwarded-For");
    String remoteAddr = request.getRemoteAddr();
    if (remoteAddr != null && !remoteAddr.isEmpty()) {
      if (existingForwardedFor == null || existingForwardedFor.isEmpty()) {
        headers.set("X-Forwarded-For", remoteAddr);
      } else if (!existingForwardedFor.contains(remoteAddr)) {
        headers.set("X-Forwarded-For", existingForwardedFor + ", " + remoteAddr);
      }
    }

    if (request.getHeader("X-Forwarded-Proto") == null) {
      headers.set("X-Forwarded-Proto", request.getScheme());
    }

    if (request.getHeader("X-Forwarded-Host") == null) {
      headers.set("X-Forwarded-Host", request.getServerName());
    }
  }

  /** 注入服务鉴权头，供后端直接放行。 */
  private void applyServiceAuthHeader(HttpHeaders headers) {
    String token = properties.getServiceToken();
    if (token == null || token.trim().isEmpty()) {
      return;
    }
    String headerName = properties.getServiceHeader();
    if (headerName == null || headerName.trim().isEmpty()) {
      headerName = "X-Service-Token";
    }
    headers.set(headerName, token.trim());
  }

  /** 复制响应头到下游，过滤 hop-by-hop 头，避免传递无效连接信息。 */
  private void copyResponseHeaders(HttpHeaders sourceHeaders, HttpServletResponse response) {
    if (sourceHeaders == null) {
      return;
    }
    for (String headerName : sourceHeaders.keySet()) {
      if (SKIP_RESPONSE_HEADERS.contains(headerName.toLowerCase())) {
        continue;
      }
      for (String value : sourceHeaders.get(headerName)) {
        response.addHeader(headerName, value);
      }
    }
  }

  /** 按块复制响应体并立即 flush，用于支持 SSE/混合流。 */
  private void streamResponseBody(InputStream input, OutputStream output) throws IOException {
    if (input == null) {
      return;
    }
    byte[] buffer = new byte[8192];
    int read;
    while ((read = input.read(buffer)) != -1) {
      output.write(buffer, 0, read);
      output.flush();
    }
  }

  /** 按块复制请求体并返回已复制字节数。 */
  private long copyRequestBody(HttpServletRequest request, OutputStream output) throws IOException {
    try (InputStream input = request.getInputStream()) {
      byte[] buffer = new byte[8192];
      int read;
      long total = 0L;
      while ((read = input.read(buffer)) != -1) {
        output.write(buffer, 0, read);
        output.flush();
        total += read;
      }
      return total;
    }
  }
}
