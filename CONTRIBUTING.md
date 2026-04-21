# CONTRIBUTING

## 本地运行

```bash
git clone <repo-url> ~/ta-assistant
cd ~/ta-assistant

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 运行测试
pytest test/ -v

# 冒烟测试
python -m src.main --message "【转发学生提问】Proteus 在哪下载?"
```

## 添加测试

- 测试文件放在 `test/`,命名为 `test_<模块名>.py`
- 每个模块的测试应覆盖:
  - 正常路径
  - 边界/异常输入
  - 空输入
- 运行 `pytest test/ -v` 确认全部通过后再提 PR

## PR 要求

- `pytest test/ -v` 必须全部通过
- `src/` 下禁止 `print()` 写 stdout,统一用 `from src.utils.stderr_log import warn as _warn`
- 新增 `config.py` 配置项时同步更新 `.env.example`
- 更新 `docs/deployment.md` 如有部署变更
