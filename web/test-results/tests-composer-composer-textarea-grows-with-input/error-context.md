# Page snapshot

```yaml
- generic [ref=e7]:
  - generic [ref=e8]:
    - heading "智能运维平台" [level=1] [ref=e9]
    - paragraph [ref=e10]: 请登录以继续访问系统
  - generic [ref=e11]:
    - tablist [ref=e12]:
      - generic [ref=e14]:
        - tab "SSO单点登录" [selected] [ref=e16] [cursor=pointer]
        - tab "本地登录" [ref=e18] [cursor=pointer]
    - tabpanel "SSO单点登录" [ref=e21]:
      - generic [ref=e22]:
        - generic [ref=e23]:
          - img "global" [ref=e24]:
            - img [ref=e25]
          - paragraph [ref=e27]: 使用企业统一身份认证
          - paragraph [ref=e28]: 您将被重定向到SSO认证页面
        - button "global SSO 登录" [ref=e29] [cursor=pointer]:
          - img "global" [ref=e31]:
            - img [ref=e32]
          - generic [ref=e34]: SSO 登录
  - separator [ref=e35]:
    - generic [ref=e36]: 或
  - generic [ref=e37]:
    - generic [ref=e39]:
      - paragraph [ref=e40]: 提示：
      - list [ref=e41]:
        - listitem [ref=e42]: SSO登录：使用企业统一身份认证
        - listitem [ref=e43]: 本地登录：使用本地用户名密码认证
    - generic [ref=e45]:
      - text: 还没有账号？
      - link "立即注册" [ref=e46] [cursor=pointer]:
        - /url: /register
```