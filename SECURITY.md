# 安全策略 / Security Policy

## 报告漏洞

发现安全问题,请**不要**公开提 issue。请通过以下方式私下联系维护者:

- GitHub Security Advisory(仓库 → Security → Report a vulnerability),或
- 私信仓库维护者

我们会尽快确认并修复,修复后再公开披露。

## 部署者必读

本项目把敏感配置(LLM API key 等)**加密存进数据库**,加密密钥来自 `SETTINGS_SECRET`。请务必:

1. **改掉所有默认密钥/密码**(通过环境变量),否则等同于明文:
   - `SETTINGS_SECRET` —— 加密 API key 的主密钥,默认值会让 DB 里的密钥可被任何人解密
   - `JWT_SECRET` —— 登录 token 签名
   - `ADMIN_PASSWORD` —— 默认 `admin/admin`,首次登录会提示改
   启动时若仍是默认值,服务会打印醒目警告。

2. **切勿提交真实数据库**(`data/*.sqlite3`)或 `.env`。两者已在 `.gitignore` 中;数据库里含用户数据和加密后的密钥。

3. 生成强随机 secret:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

4. 若曾不慎泄露 key(提交进仓库 / 公开),请**立即到对应平台作废并重新生成**。
