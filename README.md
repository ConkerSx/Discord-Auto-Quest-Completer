# Discord Auto Quest — Made by th.ragg on Discord
# discord.gg/7FCbjDMKUH

Bot open source para automatizar missões (quests) do Discord.

## Aviso
- Uso por sua conta e risco.
- Automação com token de usuário pode violar os Termos do Discord.
- Não compartilhe seu token.

## Configuração (env)

```bash
export BOT_TOKEN="token_do_bot"
export OWNER_ID="seu_discord_id"
export NOTIFY_ROLE_ID="0" # cargo de ping no notify (0 = desligado)
export BRAND="Auto Quest" # opcional
export SUPPORT_INVITE="" # opcional
export NOTIFY_INTERVAL_MIN="15" # opcional
export TUTORIAL_TERMS_CHANNEL="0"  # opcional
export TUTORIAL_TOKEN_CHANNEL="0"  # opcional
```

## Rodar

```bash
pip install -r requirements.txt
python autoquest.py
```

## Comandos (dono)
- `!painel orbs` — painel
- `!setup qn` — token do notify
- `!quest notify` — ativa notify no canal

## Painel
Login · Quests · Tutorial · Config · Sair
