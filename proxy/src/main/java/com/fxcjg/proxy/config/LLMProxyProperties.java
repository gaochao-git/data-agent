package com.fxcjg.proxy.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "proxy")
public class LLMProxyProperties {
  private String targetBaseUrl;
  private String serviceToken;
  private String serviceHeader = "X-Service-Token";
  private int connectTimeoutMs = 5000;
  private int socketTimeoutMs = 1200000;
  private int connectionRequestTimeoutMs = 3000;
  private int maxTotalConnections = 100;
  private int maxConnectionsPerRoute = 50;
  private Cors cors = new Cors();

  public String getTargetBaseUrl() {
    return targetBaseUrl;
  }

  public void setTargetBaseUrl(String targetBaseUrl) {
    this.targetBaseUrl = targetBaseUrl;
  }

  public String getServiceToken() {
    return serviceToken;
  }

  public void setServiceToken(String serviceToken) {
    this.serviceToken = serviceToken;
  }

  public String getServiceHeader() {
    return serviceHeader;
  }

  public void setServiceHeader(String serviceHeader) {
    this.serviceHeader = serviceHeader;
  }

  public int getConnectTimeoutMs() {
    return connectTimeoutMs;
  }

  public void setConnectTimeoutMs(int connectTimeoutMs) {
    this.connectTimeoutMs = connectTimeoutMs;
  }

  public int getSocketTimeoutMs() {
    return socketTimeoutMs;
  }

  public void setSocketTimeoutMs(int socketTimeoutMs) {
    this.socketTimeoutMs = socketTimeoutMs;
  }

  public int getConnectionRequestTimeoutMs() {
    return connectionRequestTimeoutMs;
  }

  public void setConnectionRequestTimeoutMs(int connectionRequestTimeoutMs) {
    this.connectionRequestTimeoutMs = connectionRequestTimeoutMs;
  }

  public int getMaxTotalConnections() {
    return maxTotalConnections;
  }

  public void setMaxTotalConnections(int maxTotalConnections) {
    this.maxTotalConnections = maxTotalConnections;
  }

  public int getMaxConnectionsPerRoute() {
    return maxConnectionsPerRoute;
  }

  public void setMaxConnectionsPerRoute(int maxConnectionsPerRoute) {
    this.maxConnectionsPerRoute = maxConnectionsPerRoute;
  }

  public Cors getCors() { return cors; }

  public void setCors(Cors cors) { this.cors = cors; }

  public static class Cors {
    private String allowedOrigins = "*";
    private String allowedMethods = "*";
    private String allowedHeaders = "*";
    private boolean allowCredentials = false;

    public String getAllowedOrigins() {
      return allowedOrigins;
    }

    public void setAllowedOrigins(String allowedOrigins) {
      this.allowedOrigins = allowedOrigins;
    }

    public String getAllowedMethods() {
      return allowedMethods;
    }

    public void setAllowedMethods(String allowedMethods) {
      this.allowedMethods = allowedMethods;
    }

    public String getAllowedHeaders() {
      return allowedHeaders;
    }

    public void setAllowedHeaders(String allowedHeaders) {
      this.allowedHeaders = allowedHeaders;
    }

    public boolean isAllowCredentials() {
      return allowCredentials;
    }

    public void setAllowCredentials(boolean allowCredentials) {
      this.allowCredentials = allowCredentials;
    }
  }
}
