import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.util.StreamUtils;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

import javax.servlet.http.HttpServletRequest;
import java.io.InputStream;
import java.net.URI;
import java.util.Enumeration;

@RestController
public class AiProxyController {

    // Nginx 地址
    private final String nginxServiceUrl = "http://127.0.0.1:80"; 
    private final RestTemplate restTemplate;

    public AiProxyController(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    @RequestMapping(value = "/agents/ai/api/**")
    public ResponseEntity<StreamingResponseBody> proxyRequest(HttpServletRequest request) {
        // 1. 拼接 URL
        String fullUrl = nginxServiceUrl + request.getRequestURI();
        if (request.getQueryString() != null) {
            fullUrl += "?" + request.getQueryString();
        }
        URI uri = URI.create(fullUrl);

        // 2. 准备请求回调（设置请求头 + Body）
        // 这里使用 execute 方法，它允许我们完全控制 Request 和 Response
        return restTemplate.execute(
            uri,
            HttpMethod.valueOf(request.getMethod()),
            clientRequest -> {
                // A. 复制请求头给后端
                Enumeration<String> headerNames = request.getHeaderNames();
                while (headerNames.hasMoreElements()) {
                    String name = headerNames.nextElement();
                    // 过滤掉 Host 和 Content-Length (让 RestTemplate 重新计算)
                    if (!"host".equalsIgnoreCase(name) && !"content-length".equalsIgnoreCase(name)) {
                        clientRequest.getHeaders().add(name, request.getHeader(name));
                    }
                }
                // B. 如果有 Body (比如 POST JSON)，这里复制过去
                // 注意：这里简单起见读取了 request 的流，如果 Body 很大建议也用流式复制
                StreamUtils.copy(request.getInputStream(), clientRequest.getBody());
            },
            clientResponse -> {
                // 3. 【关键步骤】收到后端响应了！
                
                // C. 提取后端返回的状态码
                HttpStatus statusCode = clientResponse.getStatusCode();
                
                // D. 提取后端返回的所有 Header，并准备原样返给前端
                HttpHeaders responseHeaders = new HttpHeaders();
                responseHeaders.addAll(clientResponse.getHeaders());
                
                // 注意：这里不需要手动设置 Content-Type 为 text/event-stream
                // 因为如果 Python 返回 JSON，这里的 headers 里就是 application/json
                // 如果 Python 返回 SSE，这里的 headers 里就是 text/event-stream
                // 我们只是搬运工。

                // E. 构建流式输出体
                StreamingResponseBody streamBody = outputStream -> {
                    InputStream bodyStream = clientResponse.getBody();
                    byte[] buffer = new byte[4096]; // 4KB 缓冲
                    int bytesRead;
                    while ((bytesRead = bodyStream.read(buffer)) != -1) {
                        outputStream.write(buffer, 0, bytesRead);
                        // F. 重要：每次写完必须 flush，否则 SSE 会卡顿
                        outputStream.flush(); 
                    }
                };

                return new ResponseEntity<>(streamBody, responseHeaders, statusCode);
            }
        );
    }
}