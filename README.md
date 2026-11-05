# TeleFold

LLM 驱动的 Telegram 分组自动整理工具。抓取所有对话信息，调用 LLM 智能分类，预览完整执行计划，确认后整理普通分组并保留共享分组。

## 功能

- **LLM 智能分类** — 综合对话标题、描述、成员数、样本消息，通常分成 5-8 个分组；不足 5 个对话时允许更少分组
- **自动清理** — 删除已注销账号、scam、fake 对话
- **NSFW 过滤** — 关键词 + restricted 标记识别，归入固定分组
- **陌生人分离** — 非联系人用户单独归类
- **置顶官方** — Telegram 官方账号自动置顶，不参与分类

## 安装

```bash
git clone https://github.com/solarhell/telefold.git
cd telefold
uv sync --locked --no-dev
source .venv/bin/activate
```

## 配置

支持 Linux 和 macOS，需要 Python 3.12+ 和 uv。运行时使用 Pydantic AI v2 的 OpenAI 精简依赖。

复制 `config.example.jsonc` 为 `config.jsonc` 并填入 LLM 配置：

```jsonc
{
  "llm": {
    "api_key": "sk-xxx",
    "model": "gpt-4o",
    "base_url": "https://api.openai.com/v1"
  }
}
```

支持任何 OpenAI 兼容 API，修改 `base_url` 即可切换（如 Deepseek、Groq、Together 等）。

首次运行时会交互式输入手机号和验证码完成 Telegram 登录。

## 使用

```bash
# 完整流程：抓取 → 分类 → 确认 → 应用
telefold run

# 预览删除、置顶和分组计划，不修改 Telegram
telefold run --dry-run

# 查看详细对话信息
telefold run -v

# 每个对话抓 10 条样本消息（默认 5）
telefold run -s 10

# 只处理频道和超级群组
telefold run -t channel,supergroup

# 跳过确认提示
telefold run -y

# 预览待清理的已注销账号
telefold clean --dry-run

# 确认后清理已注销账号
telefold clean
```

## 命令

### `telefold run`

抓取对话 → LLM 分类 → 校验并展示完整计划 → 确认后执行。

| 选项 | 说明 |
|------|------|
| `-c, --config` | 配置文件路径（默认 `config.jsonc`） |
| `-s, --samples` | 每个对话抓取的样本消息数（默认 5） |
| `-t, --types` | 按类型过滤，逗号分隔（channel, supergroup, group, user, bot） |
| `-v, --verbose` | 打印详细对话信息 |
| `--dry-run` | 只读取和分类，不删除、置顶或修改分组；与 `--yes` 同用仍不执行写入 |
| `--code` | 非交互式传入登录验证码 |
| `-y, --yes` | 确认执行计划中的删除、置顶和分组重建操作 |

### `telefold clean`

清理已注销账号的对话。

| 选项 | 说明 |
|------|------|
| `-c, --config` | 配置文件路径（默认 `config.jsonc`） |
| `--dry-run` | 仅预览待删除对话，不执行删除 |
| `--code` | 非交互式传入登录验证码 |
| `-y, --yes` | 确认删除已注销账号的对话 |

`clean` 同样支持 `--dry-run`，此时不会删除任何对话，`--yes` 不会覆盖预览模式。

`run --dry-run` 仍需 Telegram 登录、读取对话和 LLM 分类，因此仍可能产生 LLM 调用费用；`clean --dry-run` 不调用 LLM。计划会列出待删除对话及每个分组的成员，所有目标 peer 和请求编码在确认前完成检查，预览会列出将移除的旧普通分组。所有 Telegram 内容修改只在计划校验通过并得到确认后执行；拒绝确认会取消整份计划。分组名称最多 12 个字符，模型不能占用 NSFW 和陌生人的固定名称。模型输出的分组数量、对话覆盖和名称都会经过代码校验；不合规时最多要求模型修正 3 次，仍失败则在任何修改前停止。预览中的控制字符会转义显示，避免改变计划的排版。没有待创建的分组时保留已有分组。执行时复用普通分组 ID，先完成全部分组写入，再删除多余普通分组；默认分组与共享分组始终保留。执行前会复查分组快照，发现其他客户端的改动时停止并要求重新生成计划。分组和官方置顶成功后，最后才清理对话。任何写入失败都会非零退出，停止后续操作并断开连接。Telegram 操作不是事务，远程故障仍可能导致部分操作已完成。

## 开发与验证

```bash
uv sync --locked
uv run --locked ruff check
uv run --locked ruff format --check
uv run --locked pytest -q
uv build
```

测试由 pytest-socket 禁止创建 IPv4/IPv6 网络 socket，并拦截各类 DNS 解析；仅允许异步事件循环所需的本地 Unix socket。使用 Telegram mock 和本地 HTTP transport 验证删除/置顶/分组保护、异常清理及真实 Pydantic AI/OpenAI SDK 的分类协议。CI 在 Linux 和 macOS 的 Python 3.12–3.15 上执行相同检查，不需要 Telegram 会话或 API 密钥。

Dependabot 每周检查 uv 和 GitHub Actions 更新；次版本及补丁更新分组提交，主版本升级单独审查。
