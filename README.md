# resume-bot

一个跑在你自己 Windows 电脑上的本地求职助手。

它可以帮你上传简历、整理求职偏好、采集岗位列表、补全岗位 JD，并用 AI 给出更值得先看的岗位推荐。

**使用resume-bot，你不需要会 Git，也不需要会写代码。**

## 它能做什么

- 在本地网页里上传简历
- 在本地保存求职偏好
- 采集 BOSS 岗位列表
- 补抓岗位 JD
- 用 AI 推荐更值得先看的岗位
- 在网页里标记岗位：想投、不想投、暂缓
- 在网页里配置 AI Key，不强制手改配置文件

## 它不做什么

- 不自动投递简历
- 不自动给 HR 发消息
- 不承诺绕过任何平台限制
- 不保证 BOSS 一定能抓取成功
- 不上传你的简历、Key、浏览器登录态到项目作者那里

请只在你自己的电脑、你自己的账号登录态下使用，并遵守相关平台规则。

## 最简单安装方式

普通用户推荐用 ZIP，不用学 Git。

1. 打开本项目的 GitHub 页面。
2. 点击绿色 `Code` 按钮。
3. 点击 `Download ZIP`。
4. 解压到一个英文路径或简单中文路径，例如：
   - `D:\resume-bot`
   - `E:\tools\resume-bot`
5. 安装 Python 3。
6. 双击：
   - `启动求职助手.bat`

第一次启动会安装运行环境，可能会比较慢。黑窗口不要直接关掉。

更详细的图文步骤看：

- [新手安装教程](docs/GETTING_STARTED.md)

## 启动后做什么

启动成功后，浏览器会打开一个本地网页。

网址通常长这样：

```text
http://127.0.0.1:8765
```

如果 `8765` 被占用，脚本会自动换到附近端口。以黑窗口里显示的 `Local URL` 为准。

打开网页后建议按这个顺序来：

1. 进入 `AI 设置`
2. 填入你的文本模型 API 信息
3. 点击测试
4. 回到首页上传简历
5. 设置求职偏好
6. 登录 BOSS
7. 开始采集岗位
8. 查看推荐结果

## AI Key 怎么填

普通用户不需要先改 `.env` 文件。

推荐做法：

1. 先双击 `启动求职助手.bat`
2. 打开网页
3. 在网页左侧进入 `AI 设置`
4. 填 Provider、Base URL、API Key、Model
5. 点击测试
6. 测试通过后保存

`.env.example` 保留给高级用户或开发者使用。

## BOSS 使用说明

这个项目的 BOSS 相关能力只用于个人本地求职辅助。

原则：

- 只采集岗位信息
- 不自动投递
- 不自动发消息
- 需要你自己登录自己的 BOSS 账号
- 遇到验证、空白页、频繁失败时请停下来，不要硬刷

如果要使用 BOSS，建议先在本机浏览器里登录 BOSS，再按网页提示操作。

## 常见问题

如果你遇到这些情况：

- 双击没反应
- 提示找不到 Python
- 启动很慢
- 网页打不开
- AI 测试失败
- BOSS 没有登录
- 黑窗口停住不知道怎么办

请看：

- [常见问题和排错](docs/TROUBLESHOOTING.md)

## 给开发者

本项目是 Python 本地网页应用，主要入口：

- `quick_start_local.bat`：Windows 本地启动
- `scripts/run_local_web.py`：本地网页后端
- `src/resume_bot/`：核心代码
- `tests/`：回归测试

本地回归：

```powershell
.venv\Scripts\python.exe -m unittest tests.test_llm tests.test_local_web tests.test_matching tests.test_pipeline_source_selection tests.test_pipeline_queue_import
```

前端烟测：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test_frontend_smoke.ps1
```

## 来源与授权

- 本项目使用 MIT 协议，详见 [LICENSE](LICENSE)。
- BOSS 相关实现主要参考了 MIT 协议项目 [`lx419394005-cloud/boss-scripts`](https://github.com/lx419394005-cloud/boss-scripts)，详见 [NOTICE](NOTICE.md)。
- 前端静态资源包含 Bootstrap 和 Bootstrap Icons，详见 [NOTICE](NOTICE.md)。
