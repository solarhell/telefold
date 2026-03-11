# TeleFold

LLM 驱动的 Telegram 分组自动整理工具。抓取所有对话信息，调用 LLM 智能分类，一键生成 Telegram 分组。

## 功能

- **LLM 智能分类** — 综合对话标题、描述、成员数、样本消息，自动分成 5-8 个分组
- **自动清理** — 删除已注销账号、scam、fake 对话
- **NSFW 过滤** — 关键词 + restricted 标记识别，归入固定分组
- **陌生人分离** — 非联系人用户单独归类
- **置顶官方** — Telegram 官方账号自动置顶，不参与分类

## 安装

```bash
# 需要 Python >= 3.12
uv sync
```

## 配置

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

# 预览分类结果，不实际应用
telefold run --dry-run

# 查看详细对话信息
telefold run -v

# 每个对话抓 10 条样本消息（默认 5）
telefold run -s 10

# 只处理频道和超级群组
telefold run -t channel,supergroup

# 跳过确认提示
telefold run -y

# 只清理已注销账号
telefold clean
```

## 命令

### `telefold run`

抓取对话 → LLM 分类 → 创建分组。

| 选项 | 说明 |
|------|------|
| `-c, --config` | 配置文件路径（默认 `config.jsonc`） |
| `-s, --samples` | 每个对话抓取的样本消息数（默认 5） |
| `-t, --types` | 按类型过滤，逗号分隔（channel, supergroup, group, user, bot） |
| `-v, --verbose` | 打印详细对话信息 |
| `--dry-run` | 只分类不应用 |
| `--code` | 非交互式传入登录验证码 |
| `-y, --yes` | 跳过确认提示 |

### `telefold clean`

清理已注销账号的对话。

| 选项 | 说明 |
|------|------|
| `-c, --config` | 配置文件路径（默认 `config.jsonc`） |
| `--code` | 非交互式传入登录验证码 |
| `-y, --yes` | 跳过确认提示 |
