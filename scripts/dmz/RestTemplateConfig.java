import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestTemplate;

@Configuration
public class AiProxyConfiguration {

    // 给这个 Bean 起个名字，叫 "aiRestTemplate"
    @Bean("aiRestTemplate") 
    public RestTemplate aiRestTemplate() {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        
        // 1. 关键配置：关闭缓冲，支持大文件流式上传
        factory.setBufferRequestBody(false); 
        
        // 2. 关键配置：针对 AI 的超长超时时间（例如 5 分钟）
        // 普通业务可能只需要 30 秒，隔离配置互不影响
        factory.setReadTimeout(5 * 60 * 1000); 
        factory.setConnectTimeout(10 * 1000);  

        return new RestTemplate(factory);
    }
}