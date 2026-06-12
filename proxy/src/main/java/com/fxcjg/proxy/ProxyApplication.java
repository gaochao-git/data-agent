package com.fxcjg.proxy;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

import com.fxcjg.proxy.config.LLMProxyProperties;

@SpringBootApplication
@EnableConfigurationProperties(LLMProxyProperties.class)
public class ProxyApplication {
  public static void main(String[] args) {
    SpringApplication.run(ProxyApplication.class, args);
  }
}
