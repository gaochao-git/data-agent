Playwright 用法备忘

- 安装依赖与浏览器：
  ```bash
  cd web
  pnpm install
  pnpm exec playwright install
  ```

- 运行所有 e2e（无头）：
  ```bash
  pnpm test:e2e
  ```

- 指定环境访问路径/iframe/菜单（按需设置）：
  - `E2E_HOST`：外层页面地址，默认 `http://localhost:3000`
  - `E2E_COMPOSER_PATH`：外层路径，默认 `/`
  - `E2E_COMPOSER_TRIGGER`：点击打开你页面的菜单选择器，例如 `text=AI 页面`
  - `E2E_IFRAME_NAME`：如果页面在 iframe 中，填 iframe 的 name
  示例：
  ```bash
  E2E_HOST=http://localhost:3000 \
  E2E_COMPOSER_PATH=/home \
  E2E_COMPOSER_TRIGGER="text=AI 页面" \
  E2E_IFRAME_NAME=myFrame \
  pnpm test:e2e
  ```

- 可视化查看自动操作：
  1) 先启动前端（如 `pnpm dev`），保证地址可访问。
  2) 另开终端运行（在 `web` 目录）：
     ```bash
     PWDEBUG=1 pnpm exec playwright test tests/composer.spec.ts --headed
     ```
     调试面板会暂停在第 0 步，点击 ▶️ Resume 后能看到自动点击/输入全过程。

- 常用单测命令：
  ```bash
  pnpm exec playwright test tests/composer.spec.ts
  pnpm exec playwright test tests/composer.spec.ts --headed
  ```
