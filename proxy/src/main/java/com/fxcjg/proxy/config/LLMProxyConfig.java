package com.fxcjg.proxy.config;

import java.util.Arrays;

import org.apache.http.client.config.RequestConfig;
import org.apache.http.impl.client.CloseableHttpClient;
import org.apache.http.impl.client.HttpClients;
import org.apache.http.impl.conn.PoolingHttpClientConnectionManager;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.client.ResponseErrorHandler;
import org.springframework.web.servlet.config.annotation.CorsRegistration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;
import org.springframework.http.client.HttpComponentsClientHttpRequestFactory;
import org.springframework.http.client.ClientHttpResponse;

@Configuration
public class LLMProxyConfig implements WebMvcConfigurer {
  private final LLMProxyProperties properties;

  public LLMProxyConfig(LLMProxyProperties properties) {
    this.properties = properties;
  }

  /** 代理后端请求连接池，显式控制总连接数和单 route 连接数。 */
  @Bean(name = "proxyConnectionManager")
  public PoolingHttpClientConnectionManager proxyConnectionManager() {
    PoolingHttpClientConnectionManager manager = new PoolingHttpClientConnectionManager();
    manager.setMaxTotal(properties.getMaxTotalConnections());
    manager.setDefaultMaxPerRoute(properties.getMaxConnectionsPerRoute());
    return manager;
  }

  /** 代理专用 RestTemplate，避免与宿主工程的默认实例耦合。 */
  @Bean(name = "proxyRestTemplate")
  public RestTemplate restTemplate(PoolingHttpClientConnectionManager proxyConnectionManager) {
    RequestConfig requestConfig = RequestConfig.custom().setConnectTimeout(properties.getConnectTimeoutMs()).setConnectionRequestTimeout(properties.getConnectionRequestTimeoutMs()).setSocketTimeout(properties.getSocketTimeoutMs()).build();
    CloseableHttpClient httpClient = HttpClients.custom().setConnectionManager(proxyConnectionManager).setDefaultRequestConfig(requestConfig).build();
    HttpComponentsClientHttpRequestFactory factory = new HttpComponentsClientHttpRequestFactory(httpClient);
    RestTemplate restTemplate = new RestTemplate(factory);
    ResponseErrorHandler errorHandler = new ResponseErrorHandler() {
      @Override
      public boolean hasError(ClientHttpResponse response) {
        return false;
      }

      @Override
      public void handleError(ClientHttpResponse response) {
      }
    };
    restTemplate.setErrorHandler(errorHandler);
    return restTemplate;
  }

  @Override
  public void addCorsMappings(CorsRegistry registry) {
    CorsRegistration registration = registry.addMapping("/api/**");
    String[] origins = split(properties.getCors().getAllowedOrigins());
    String[] methods = split(properties.getCors().getAllowedMethods());
    String[] headers = split(properties.getCors().getAllowedHeaders());

    if (origins.length == 1 && "*".equals(origins[0]) && properties.getCors().isAllowCredentials()) {
      registration.allowedOriginPatterns("*");
    } else if (origins.length > 0) {
      registration.allowedOrigins(origins);
    }

    if (methods.length > 0) {
      registration.allowedMethods(methods);
    }

    if (headers.length > 0) {
      registration.allowedHeaders(headers);
    }

    registration.allowCredentials(properties.getCors().isAllowCredentials());
  }

  private String[] split(String raw) {
    if (raw == null) {
      return new String[0];
    }
    String trimmed = raw.trim();
    if (trimmed.isEmpty()) {
      return new String[0];
    }
    if ("*".equals(trimmed)) {
      return new String[] { "*" };
    }
    return Arrays.stream(raw.split(",")).map(String::trim).filter(item -> !item.isEmpty()).toArray(String[]::new);
  }
}
